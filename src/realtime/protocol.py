from __future__ import annotations

import json
from math import isfinite
from collections.abc import Mapping
from copy import deepcopy

from src.providers.openai_realtime import OpenAIRealtimeTarget
from src.realtime.contracts import RealtimeError
from src.realtime.errors import new_event_id, safe_identifier, sanitize_error

CLIENT_EVENTS = frozenset(
    {
        "session.update",
        "input_audio_buffer.append",
        "input_audio_buffer.commit",
        "input_audio_buffer.clear",
        "conversation.item.create",
        "conversation.item.retrieve",
        "conversation.item.truncate",
        "conversation.item.delete",
        "response.create",
        "response.cancel",
    }
)
USAGE_EVENTS = frozenset({"response.done", "conversation.item.input_audio_transcription.completed"})


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate property")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("non-finite JSON value")


def _finite_float(value: str) -> float:
    number = float(value)
    if not isfinite(number):
        raise ValueError("non-finite JSON value")
    return number


def parse_event(message: str, *, max_bytes: int) -> dict[str, object]:
    try:
        if len(message.encode("utf-8")) > max_bytes:
            raise RealtimeError(
                "message_too_large", "Realtime message is too large", close_code=1009
            )
        event = json.loads(
            message,
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
            parse_float=_finite_float,
        )
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise ValueError("event must have a type")
        if not event["type"] or len(event["type"]) > 160:
            raise ValueError("invalid event type")
        return event
    except (ValueError, RecursionError, UnicodeError) as exc:
        raise RealtimeError("invalid_event", "Expected a valid JSON event with a type") from exc


def encode_event(event: Mapping[str, object]) -> str:
    return json.dumps(event, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _copy_event(event: dict[str, object]) -> dict[str, object]:
    try:
        return deepcopy(event)
    except RecursionError as exc:
        # The JSON decoder can accept deeper nesting than deepcopy supports.
        raise RealtimeError("invalid_event", "Realtime event nesting is too deep") from exc


def _object(parent: dict, key: str) -> dict | None:
    if key not in parent or parent[key] is None:
        return None
    if not isinstance(parent[key], dict):
        raise RealtimeError("invalid_event", "Invalid Realtime control object")
    return parent[key]


def _map_model(control: dict, target: OpenAIRealtimeTarget, *, inbound: bool) -> None:
    model = control.get("model")
    if model is None:
        return
    if inbound:
        if model != target.public_model:
            raise RealtimeError("model_not_allowed", "The session model cannot be changed")
        control["model"] = target.upstream_model
    elif model == target.upstream_model:
        control["model"] = target.public_model


def _map_transcription(session: dict, target: OpenAIRealtimeTarget, *, inbound: bool) -> None:
    audio = _object(session, "audio")
    audio_input = _object(audio, "input") if audio is not None else None
    transcription = _object(audio_input, "transcription") if audio_input is not None else None
    if transcription is None:
        return
    if target.profile != "transcription":
        if inbound:
            raise RealtimeError(
                "transcription_not_admitted", "Input transcription needs separate admission"
            )
        return
    _map_model(transcription, target, inbound=inbound)


def client_event(event: dict[str, object], target: OpenAIRealtimeTarget) -> dict[str, object]:
    if event["type"] not in CLIENT_EVENTS:
        raise RealtimeError("unsupported_event", "Realtime event is not supported")
    mapped = _copy_event(event)
    if event["type"] == "session.update":
        session = _object(mapped, "session")
        if session is None:
            raise RealtimeError("invalid_event", "session.update requires a session")
        if session.get("type", target.profile) != target.profile:
            raise RealtimeError("profile_not_allowed", "The session type cannot be changed")
        _map_model(session, target, inbound=True)
        _map_transcription(session, target, inbound=True)
        if "input_audio_transcription" in session:
            raise RealtimeError(
                "unsupported_protocol", "Only the GA Realtime protocol is supported"
            )
    # Tools remain client owned. Hosted tools and image pricing are not
    # qualified here; admission must also validate per-session/turn controls.
    for control in (mapped.get("session"), mapped.get("response")):
        if isinstance(control, dict):
            if control is mapped.get("response"):
                _map_model(control, target, inbound=True)
            tools = control.get("tools", [])
            if not isinstance(tools, list):
                raise RealtimeError("invalid_event", "Realtime tools must be an array")
            for tool in tools:
                if not isinstance(tool, dict) or tool.get("type") != "function":
                    raise RealtimeError(
                        "unsupported_tool", "Only client-owned function tools are supported"
                    )
    _check_content(mapped)
    return mapped


def _check_content(event: dict) -> None:
    items = [event.get("item")]
    response = event.get("response")
    if isinstance(response, dict):
        inputs = response.get("input", [])
        if isinstance(inputs, list):
            items.extend(inputs)
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("content"), list):
            for content in item["content"]:
                if not isinstance(content, dict) or not isinstance(content.get("type"), str):
                    raise RealtimeError("invalid_event", "Invalid Realtime content type")
                if content["type"] in {"input_image", "image"}:
                    raise RealtimeError(
                        "unsupported_content", "Image input is not supported for Realtime"
                    )


def server_event(event: dict[str, object], target: OpenAIRealtimeTarget) -> dict[str, object]:
    mapped = _copy_event(event)
    if event["type"] in {"error", "conversation.item.input_audio_transcription.failed"}:
        result = {
            "type": event["type"],
            "event_id": safe_identifier(event.get("event_id"), target.headers) or new_event_id(),
            "error": sanitize_error(
                event.get("error"),
                target.headers,
                default_type="invalid_request_error"
                if event["type"] == "error"
                else "server_error",
            ),
        }
        if event["type"] != "error":
            item_id = safe_identifier(event.get("item_id"), target.headers)
            if item_id is not None:
                result["item_id"] = item_id
            index = event.get("content_index")
            if isinstance(index, int) and not isinstance(index, bool) and index >= 0:
                result["content_index"] = index
        return result
    if event["type"] in {"session.created", "session.updated"}:
        session = _object(mapped, "session")
        if session is not None:
            _map_model(session, target, inbound=False)
            _map_transcription(session, target, inbound=False)
    if event["type"] == "response.done":
        response = _object(mapped, "response")
        if response is not None:
            _map_model(response, target, inbound=False)
            details = _object(response, "status_details")
            if details is not None and details.get("error") is not None:
                details["error"] = sanitize_error(
                    details["error"], target.headers, default_type="server_error"
                )
    return mapped
