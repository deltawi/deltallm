import json

import pytest

from src.realtime.contracts import RealtimeLimits
from src.realtime.controls import prepare_session, validate_controls
from src.realtime.errors import RealtimeError
from tests.realtime.fakes import Socket, target


@pytest.mark.parametrize(
    "control",
    [
        {"audio": {"input": {"turn_detection": {"type": "server_vad"}}}},
        {"max_output_tokens": "inf"},
        {"max_output_tokens": True},
        {"max_output_tokens": 4097},
        {"prompt": {"id": "server-prompt"}},
    ],
)
def test_billable_controls_cannot_escape_manual_admission(control):
    with pytest.raises(RealtimeError):
        validate_controls(
            {"type": "session.update", "session": control},
            profile="realtime",
            max_output_tokens=4096,
        )


@pytest.mark.parametrize(
    "kind", ["response.create", "conversation.item.create", "conversation.item.delete"]
)
def test_transcription_has_one_audio_buffer_admission_boundary(kind):
    with pytest.raises(RealtimeError):
        validate_controls({"type": kind}, profile="transcription", max_output_tokens=4096)


@pytest.mark.parametrize("profile", ["realtime", "transcription"])
async def test_prepare_confirms_controls_and_preserves_native_event_order(profile):
    socket = Socket()
    session = {
        "type": profile,
        "model": "provider-model",
        "max_output_tokens": 4096,
        "audio": {"input": {"turn_detection": None, "transcription": None}},
    }
    if profile == "transcription":
        session["audio"]["input"]["transcription"] = {"model": "provider-model"}
    for kind in ("session.created", "session.updated"):
        socket.incoming.put_nowait(json.dumps({"type": kind, "session": session}))
    prepared = await prepare_session(
        socket, target=target(profile), limits=RealtimeLimits(), max_output_tokens=4096
    )
    setup = json.loads(await socket.outgoing.get())["session"]
    assert setup["audio"]["input"]["turn_detection"] is None
    assert json.loads(await prepared.receive_text())["type"] == "session.created"
    assert json.loads(await prepared.receive_text())["type"] == "session.updated"


@pytest.mark.parametrize(
    "patch",
    [
        {"audio": {"input": {"turn_detection": {"type": "server_vad"}}}},
        {"audio": {"input": {"turn_detection": None, "transcription": {"model": "other"}}}},
        {"model": "other"},
        {"max_output_tokens": "inf"},
        {"type": "transcription"},
    ],
)
async def test_mismatched_provider_setup_denies_upgrade(patch):
    socket = Socket()
    session = {
        "type": "realtime",
        "model": "provider-model",
        "max_output_tokens": 4096,
        "audio": {"input": {"turn_detection": None}},
    }
    socket.incoming.put_nowait('{"type":"session.created"}')
    socket.incoming.put_nowait(
        json.dumps({"type": "session.updated", "session": {**session, **patch}})
    )
    with pytest.raises(RealtimeError):
        await prepare_session(
            socket, target=target(), limits=RealtimeLimits(), max_output_tokens=4096
        )


@pytest.mark.parametrize(
    "format", [None, {"type": "audio/pcmu"}, {"type": "audio/pcm", "rate": 16000}]
)
def test_duration_admission_rejects_changed_audio_format(format):
    with pytest.raises(RealtimeError, match="PCM"):
        validate_controls(
            {"type": "session.update", "session": {"audio": {"input": {"format": format}}}},
            profile="transcription",
            max_output_tokens=4096,
            duration_pcm=True,
        )


@pytest.mark.parametrize(
    "format", [None, {"type": "audio/pcma"}, {"type": "audio/pcm", "rate": 24000}]
)
async def test_duration_handshake_requires_confirmed_bounded_format(format):
    socket = Socket()
    socket.incoming.put_nowait('{"type":"session.created"}')
    socket.incoming.put_nowait(
        json.dumps(
            {
                "type": "session.updated",
                "session": {
                    "type": "transcription",
                    "audio": {
                        "input": {
                            "format": format,
                            "turn_detection": None,
                            "transcription": {"model": "provider-model"},
                        }
                    },
                },
            }
        )
    )
    if format == {"type": "audio/pcm", "rate": 24000}:
        await prepare_session(
            socket,
            target=target("transcription"),
            limits=RealtimeLimits(),
            max_output_tokens=4096,
            duration_pcm=True,
        )
        setup = json.loads(await socket.outgoing.get())
        assert setup["session"]["audio"]["input"]["format"] == format
    else:
        with pytest.raises(RealtimeError, match="PCM"):
            await prepare_session(
                socket,
                target=target("transcription"),
                limits=RealtimeLimits(),
                max_output_tokens=4096,
                duration_pcm=True,
            )


@pytest.mark.parametrize(
    "profile,model", [("realtime", "provider-model"), ("transcription", "other")]
)
def test_client_cannot_add_unadmitted_transcription_or_change_its_model(profile, model):
    with pytest.raises(RealtimeError):
        validate_controls(
            {
                "type": "session.update",
                "session": {"audio": {"input": {"transcription": {"model": model}}}},
            },
            profile=profile,
            max_output_tokens=4096,
            transcription_model="provider-model",
        )
