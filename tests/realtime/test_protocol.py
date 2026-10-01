import json

import pytest

from src.realtime.contracts import RealtimeError
from src.realtime.protocol import client_event, parse_event, server_event
from tests.realtime.fakes import target


def test_model_mapping_preserves_native_fields_and_original_event():
    event = {
        "type": "session.update",
        "event_id": "e1",
        "session": {
            "type": "realtime",
            "model": "voice",
            "instructions": "Say provider-model",
            "audio": {"output": {"voice": "marin"}},
            "future_safe_field": {"x": 1},
        },
    }
    mapped = client_event(event, target())
    assert mapped["session"]["model"] == "provider-model"
    assert event["session"]["model"] == "voice"
    assert mapped["session"]["instructions"] == "Say provider-model"
    assert mapped["session"]["future_safe_field"] == {"x": 1}
    mapped["type"] = "session.updated"
    assert server_event(mapped, target())["session"] == event["session"]


@pytest.mark.parametrize(
    "kind",
    [
        "response.output_audio.delta",
        "response.function_call_arguments.delta",
        "conversation.item.created",
        "future.server.event",
    ],
)
def test_native_server_events_keep_ids_audio_and_unknown_fields(kind):
    event = {"type": kind, "event_id": "ev1", "item_id": "item1", "delta": "AA==", "extra": [1, 2]}
    assert server_event(event, target()) == event


@pytest.mark.parametrize(
    "event,code",
    [
        ({"type": "session.update", "session": {"model": "other"}}, "model_not_allowed"),
        ({"type": "session.update", "session": {"type": "transcription"}}, "profile_not_allowed"),
        ({"type": "transcription_session.update"}, "unsupported_event"),
        (
            {
                "type": "response.create",
                "response": {"tools": [{"type": "mcp", "server_url": "https://private"}]},
            },
            "unsupported_tool",
        ),
        (
            {
                "type": "session.update",
                "session": {"audio": {"input": {"transcription": {"model": "other"}}}},
            },
            "transcription_not_admitted",
        ),
    ],
)
def test_unadmitted_control_changes_are_not_forwarded(event, code):
    with pytest.raises(RealtimeError) as caught:
        client_event(event, target())
    assert caught.value.code == code


def test_transcription_alias_is_bound_to_its_own_profile():
    event = {
        "type": "session.update",
        "session": {
            "type": "transcription",
            "audio": {
                "input": {
                    "transcription": {"model": "voice", "language": "en"},
                    "turn_detection": None,
                }
            },
        },
    }
    result = client_event(event, target("transcription"))
    assert result["session"]["audio"]["input"]["transcription"]["model"] == "provider-model"
    event["session"]["audio"]["input"]["transcription"]["model"] = "unauthorized"
    with pytest.raises(RealtimeError, match="cannot be changed"):
        client_event(event, target("transcription"))


def test_function_tools_and_client_owned_results_pass_through():
    events = [
        {
            "type": "session.update",
            "session": {
                "tools": [{"type": "function", "name": "weather", "parameters": {"type": "object"}}]
            },
        },
        {
            "type": "conversation.item.create",
            "item": {"type": "function_call_output", "call_id": "call1", "output": "sunny"},
        },
        {"type": "response.cancel", "response_id": "resp1"},
        {
            "type": "conversation.item.truncate",
            "item_id": "item1",
            "content_index": 0,
            "audio_end_ms": 100,
        },
    ]
    for event in events:
        assert client_event(event, target()) == event


@pytest.mark.parametrize(
    "message", ["[]", '{"type":1}', '{"type":"x","type":"y"}', '{"type":"x","v":NaN}', "not json"]
)
def test_malformed_json_is_rejected(message):
    with pytest.raises(RealtimeError):
        parse_event(message, max_bytes=1024)


def test_message_limit_counts_utf8_bytes():
    message = '{"type":"x","text":"é"}'
    with pytest.raises(RealtimeError) as caught:
        parse_event(message, max_bytes=len(message))
    assert caught.value.close_code == 1009


def test_overflowed_json_numbers_are_rejected_before_forwarding():
    with pytest.raises(RealtimeError):
        parse_event('{"type":"session.update","value":1e999}', max_bytes=1024)


@pytest.mark.parametrize(
    "event",
    [
        {"type": "response.create", "response": {"model": "other"}},
        {"type": "session.update", "session": {"input_audio_transcription": {"model": "other"}}},
        {
            "type": "conversation.item.create",
            "item": {"content": [{"type": "input_image", "image_url": "secret"}]},
        },
        {
            "type": "response.create",
            "response": {"input": [{"content": [{"type": "input_image", "image_url": "secret"}]}]},
        },
    ],
)
def test_additional_billable_dimensions_and_beta_fields_are_not_forwarded(event):
    with pytest.raises(RealtimeError):
        client_event(event, target())


def test_upstream_errors_do_not_echo_credentials_or_internal_data():
    result = server_event(
        {
            "type": "error",
            "event_id": "provider-secret",
            "error": {
                "message": "provider-secret",
                "code": "https://internal",
                "param": "secret",
            },
        },
        target(),
    )
    assert "provider-secret" not in json.dumps(result)
    assert "internal" not in json.dumps(result)
    assert result["type"] == "error"
    result = server_event(
        {
            "type": "response.done",
            "response": {
                "id": "resp1",
                "status": "failed",
                "status_details": {"error": {"message": "provider-secret"}},
            },
        },
        target(),
    )
    assert "provider-secret" not in json.dumps(result)
    assert result["response"]["id"] == "resp1"


@pytest.mark.parametrize("control", [{}, {"tools": []}])
@pytest.mark.parametrize(
    "kind,field", [("session.update", "session"), ("response.create", "response")]
)
def test_missing_or_empty_tools_keep_their_native_shape(control, kind, field):
    event = {"type": kind, field: control}
    assert client_event(event, target()) == event


@pytest.mark.parametrize("kind", [[], {}, 1, True, None])
def test_malformed_content_types_are_validation_errors(kind):
    event = {"type": "conversation.item.create", "item": {"content": [{"type": kind}]}}
    with pytest.raises(RealtimeError) as caught:
        client_event(event, target())
    assert caught.value.code == "invalid_event"
