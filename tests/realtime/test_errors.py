import json

import pytest

from src.realtime.contracts import RealtimeError
from src.realtime.protocol import server_event
from tests.realtime.fakes import target


@pytest.mark.parametrize("kind", ["error", "conversation.item.input_audio_transcription.failed"])
def test_error_sanitization_preserves_native_correlation_without_diagnostics(kind):
    event = {
        "type": kind,
        "event_id": "server-123",
        "item_id": "item-123",
        "content_index": 0,
        "debug": "provider-secret",
        "error": {
            "type": "server_error",
            "code": "https://internal.example",
            "message": "provider-secret https://internal.example",
            "param": "provider-secret",
            "event_id": "client-456",
            "extra": {"secret": "provider-secret"},
        },
    }
    result = server_event(event, target("transcription"))
    assert result["type"] == kind
    assert result["event_id"] == "server-123"
    assert result["error"]["event_id"] == "client-456"
    assert result["error"]["type"] == "server_error"
    assert result["error"]["code"] == "upstream_error"
    assert "provider-secret" not in json.dumps(result)
    assert "internal.example" not in json.dumps(result)
    if kind != "error":
        assert result["item_id"] == "item-123"
        assert result["content_index"] == 0
    assert event["error"]["message"].startswith("provider-secret")  # input is untouched


def test_response_failure_uses_the_same_sanitizer_and_preserves_response_identity():
    result = server_event(
        {
            "type": "response.done",
            "event_id": "server-123",
            "response": {
                "id": "response-123",
                "status": "failed",
                "status_details": {
                    "type": "failed",
                    "error": {"message": "provider-secret", "event_id": "client-456"},
                },
            },
        },
        target(),
    )
    assert result["event_id"] == "server-123"
    assert result["response"]["id"] == "response-123"
    assert result["response"]["status_details"]["error"]["event_id"] == "client-456"
    assert "provider-secret" not in json.dumps(result)


@pytest.mark.parametrize(
    "unsafe_id",
    ["provider-secret", "event_provider-secret", "https://internal", "bad\nID", "x" * 257, 123],
)
def test_unsafe_correlation_is_not_echoed(unsafe_id):
    result = server_event(
        {"type": "error", "event_id": unsafe_id, "error": {"event_id": unsafe_id}}, target()
    )
    assert result["event_id"].startswith("event_")
    assert result["event_id"] != unsafe_id
    assert "event_id" not in result["error"]


@pytest.mark.parametrize(
    "error", [None, "provider-secret", {"type": {"secret": "provider-secret"}}]
)
def test_malformed_upstream_diagnostics_still_have_a_safe_native_error(error):
    result = server_event({"type": "error", "error": error}, target())
    assert result["error"]["type"] == "invalid_request_error"
    assert "provider-secret" not in json.dumps(result)


def test_gateway_errors_have_stable_unique_server_event_ids():
    first = RealtimeError("invalid_event", "Invalid event")
    second = RealtimeError("invalid_event", "Invalid event")
    assert first.event()["event_id"].startswith("event_")
    assert first.event()["event_id"] == first.event()["event_id"]
    assert first.event()["event_id"] != second.event()["event_id"]
