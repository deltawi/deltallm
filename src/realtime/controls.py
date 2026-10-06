from __future__ import annotations

from collections import deque
from collections.abc import Mapping

from src.providers.openai_realtime import OpenAIRealtimeTarget
from src.realtime.contracts import RealtimeLimits, TextSocket
from src.realtime.errors import RealtimeError, new_event_id
from src.realtime.protocol import encode_event, parse_event


def validate_controls(
    event: Mapping[str, object],
    *,
    profile: str,
    max_output_tokens: int,
    duration_pcm: bool = False,
    transcription_model: str | None = None,
) -> None:
    """Manual native turns give admission a boundary before provider work starts."""
    if profile == "transcription" and str(event.get("type", "")).startswith(
        ("response.", "conversation.item.")
    ):
        raise RealtimeError("unsupported_event", "Transcription requires the input audio buffer")
    for key in ("session", "response"):
        control = event.get(key)
        if not isinstance(control, Mapping):
            continue
        if control.get("prompt") is not None:
            raise RealtimeError(
                "unsupported_prompt", "Realtime prompt references are not supported"
            )
        maximum = control.get("max_output_tokens", max_output_tokens)
        if type(maximum) is not int or not 1 <= maximum <= max_output_tokens:
            raise RealtimeError("output_limit_exceeded", "Realtime output token limit exceeded")
        audio = control.get("audio")
        audio_input = audio.get("input") if isinstance(audio, Mapping) else None
        if isinstance(audio_input, Mapping):
            if (
                duration_pcm
                and "format" in audio_input
                and not _duration_pcm(audio_input["format"])
            ):
                raise RealtimeError(
                    "unsupported_audio_format", "Duration accounting requires 24 kHz PCM audio"
                )
            transcription = audio_input.get("transcription")
            if profile == "realtime" and transcription is not None:
                raise RealtimeError(
                    "unsupported_profile", "Response sessions cannot add transcription work"
                )
            if (
                profile == "transcription"
                and transcription_model is not None
                and (
                    "transcription" in audio_input
                    and (
                        not isinstance(transcription, Mapping)
                        or transcription.get("model") != transcription_model
                    )
                )
            ):
                raise RealtimeError(
                    "invalid_session", "The transcription model must match admission"
                )
            if audio_input.get("turn_detection") is not None:
                raise RealtimeError(
                    "automatic_turns_unsupported", "Realtime currently requires manual turn control"
                )
            if (
                "transcription" in audio_input
                and audio_input["transcription"] is None
                and profile == "transcription"
            ):
                raise RealtimeError(
                    "invalid_session", "The transcription model must remain configured"
                )


class _PreparedSocket:
    def __init__(self, upstream: TextSocket, events: list[str]) -> None:
        self.upstream = upstream
        self.events = deque(events)

    async def receive_text(self) -> str:
        if self.events:
            return self.events.popleft()
        return await self.upstream.receive_text()

    async def send_text(self, message: str) -> None:
        await self.upstream.send_text(message)


async def prepare_session(
    upstream: TextSocket,
    *,
    target: OpenAIRealtimeTarget,
    limits: RealtimeLimits,
    max_output_tokens: int,
    duration_pcm: bool = False,
) -> TextSocket:
    """Confirm manual control before the client can send the first audio byte."""
    created_text = await upstream.receive_text()
    created = parse_event(created_text, max_bytes=limits.max_message_bytes)
    if created.get("type") != "session.created":
        raise RealtimeError(
            "upstream_session_invalid", "Upstream did not create a Realtime session"
        )
    input_controls: dict[str, object] = {"turn_detection": None}
    if duration_pcm:
        input_controls["format"] = {"type": "audio/pcm", "rate": 24000}
    session: dict[str, object] = {"type": target.profile, "audio": {"input": input_controls}}
    if target.profile == "transcription":
        input_controls["transcription"] = {"model": target.upstream_model}
    else:
        input_controls["transcription"] = None
        session["max_output_tokens"] = max_output_tokens
    await upstream.send_text(
        encode_event({"type": "session.update", "event_id": new_event_id(), "session": session})
    )
    updated_text = await upstream.receive_text()
    updated = parse_event(updated_text, max_bytes=limits.max_message_bytes)
    effective = updated.get("session")
    if updated.get("type") != "session.updated" or not isinstance(effective, dict):
        raise RealtimeError(
            "upstream_session_invalid", "Upstream did not confirm Realtime session controls"
        )
    audio = effective.get("audio")
    inputs = audio.get("input") if isinstance(audio, dict) else None
    if (
        effective.get("type") != target.profile
        or not isinstance(inputs, dict)
        or "turn_detection" not in inputs
        or inputs["turn_detection"] is not None
    ):
        raise RealtimeError(
            "upstream_session_invalid", "Upstream did not confirm manual turn control"
        )
    if duration_pcm and not _duration_pcm(inputs.get("format")):
        raise RealtimeError(
            "upstream_session_invalid", "Upstream did not confirm the bounded PCM audio format"
        )
    if target.profile == "transcription":
        transcription = inputs.get("transcription")
        if (
            not isinstance(transcription, dict)
            or transcription.get("model") != target.upstream_model
        ):
            raise RealtimeError(
                "upstream_session_invalid", "Upstream transcription model does not match admission"
            )
    elif (
        inputs.get("transcription") is not None
        or effective.get("model") != target.upstream_model
        or effective.get("max_output_tokens") != max_output_tokens
    ):
        raise RealtimeError(
            "upstream_session_invalid", "Upstream response controls do not match admission"
        )
    # Preserve both native events in order. Setup completes before HTTP upgrade.
    return _PreparedSocket(upstream, [created_text, updated_text])


def _duration_pcm(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get("type") == "audio/pcm"
        and (type(value.get("rate")) is int and value["rate"] == 24000)
    )
