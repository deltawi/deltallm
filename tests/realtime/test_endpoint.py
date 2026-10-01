import asyncio
import json
import sys
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient, WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

from src.models.errors import BudgetExceededError, PermissionDeniedError, RateLimitError
from src.realtime.contracts import RealtimeLimits
from src.realtime.runtime import RealtimeRuntime
from tests.realtime.fakes import Admission, Connector, Socket

pytestmark = pytest.mark.app


class EchoSocket(Socket):
    def __init__(self):
        super().__init__()
        self.incoming.put_nowait(
            '{"type":"session.created","session":{"id":"sess1","model":"provider-model"}}'
        )

    async def send_text(self, message):
        await super().send_text(message)
        event = json.loads(message)
        if event["type"] == "session.update":
            event["type"] = "session.updated"
            self.incoming.put_nowait(json.dumps(event))
        elif event["type"] == "response.create":
            self.incoming.put_nowait(
                '{"type":"response.output_audio.delta","response_id":"resp1","delta":"AA=="}'
            )
            self.incoming.put_nowait(
                '{"type":"response.done","response":{"id":"resp1","usage":null}}'
            )


def install(app, *, profile="realtime", **limits):
    admission = Admission(profile=profile)
    connector = Connector(EchoSocket())
    app.state.realtime_runtime = RealtimeRuntime(
        admission=admission,
        connector=connector,
        limits=RealtimeLimits(**limits),
    )
    return admission, connector


def test_application_denies_realtime_when_no_admission_runtime_is_installed(test_app):
    with pytest.raises(WebSocketDenialResponse) as caught:
        with TestClient(test_app).websocket_connect("/v1/realtime?model=voice"):
            pytest.fail("unconfigured Realtime must not accept a socket")
    assert caught.value.status_code == 503
    assert caught.value.json()["error"]["code"] == "realtime_unavailable"


def test_native_flow_authenticates_and_finalizes_one_pinned_upstream(test_app):
    admission, connector = install(test_app)
    with TestClient(test_app).websocket_connect(
        "/v1/realtime?model=voice", headers={"Authorization": "Bearer sk-test"}
    ) as ws:
        created = ws.receive_json()
        assert created["session"] == {"id": "sess1", "model": "voice"}
        ws.send_json({"type": "session.update", "session": {"type": "realtime", "model": "voice"}})
        assert ws.receive_json()["session"]["model"] == "voice"
        ws.send_json({"type": "response.create"})
        assert ws.receive_json()["delta"] == "AA=="
        assert ws.receive_json()["response"]["id"] == "resp1"
    assert connector.opens == connector.closed == admission.closed == 1
    assert admission.requests[0].auth.organization_id == "org-default"
    assert admission.requests[0].auth.api_key != "sk-test"
    assert len(admission.permit.receipts) == 1
    assert test_app.state.realtime_runtime.active_sessions == 0


@pytest.mark.parametrize(
    "headers,status",
    [
        ({}, 401),
        ({"Authorization": "Bearer bad-key"}, 401),
        ({"Authorization": "Bearer sk-test", "Origin": "https://browser.example"}, 400),
        ({"Authorization": "Bearer sk-test", "OpenAI-Beta": "realtime=v1"}, 400),
    ],
)
def test_handshake_denials_never_open_provider(test_app, headers, status):
    admission, connector = install(test_app)
    with pytest.raises(WebSocketDenialResponse) as caught:
        with TestClient(test_app).websocket_connect("/v1/realtime?model=voice", headers=headers):
            pytest.fail("handshake should have been denied")
    assert caught.value.status_code == status
    assert not admission.requests and not connector.opens
    assert test_app.state.realtime_runtime.active_sessions == 0


@pytest.mark.parametrize(
    "query",
    [
        "",
        "model=",
        "model=voice&model=another",
        "model=voice&api_key=secret",
        "model=voice&intent=other",
    ],
)
def test_unknown_or_ambiguous_query_parameters_are_rejected(test_app, query):
    admission, connector = install(test_app)
    with pytest.raises(WebSocketDenialResponse) as caught:
        with TestClient(test_app).websocket_connect(
            "/v1/realtime?" + query, headers={"Authorization": "Bearer sk-test"}
        ):
            pytest.fail("query should have been rejected")
    assert caught.value.status_code == 400
    assert not admission.requests and not connector.opens


@pytest.mark.parametrize(
    "error,status", [(PermissionDeniedError(), 403), (BudgetExceededError(), 429)]
)
def test_admission_errors_deny_before_provider_connection(test_app, error, status):
    admission, connector = install(test_app)
    admission.error = error
    with pytest.raises(WebSocketDenialResponse) as caught:
        with TestClient(test_app).websocket_connect(
            "/v1/realtime?model=voice", headers={"Authorization": "Bearer sk-test"}
        ):
            pytest.fail("admission should have denied the session")
    assert caught.value.status_code == status
    assert not connector.opens


def test_transcription_handshake_can_resolve_default_without_client_model(test_app):
    admission, connector = install(test_app, profile="transcription")
    with TestClient(test_app).websocket_connect(
        "/v1/realtime?intent=transcription", headers={"Authorization": "Bearer sk-test"}
    ) as ws:
        ws.receive_json()
        ws.send_json(
            {
                "type": "session.update",
                "session": {
                    "type": "transcription",
                    "audio": {
                        "input": {"transcription": {"model": "voice"}, "turn_detection": None}
                    },
                },
            }
        )
        assert ws.receive_json()["session"]["audio"]["input"]["transcription"]["model"] == "voice"
    assert admission.requests[0].model is None
    assert admission.requests[0].profile == "transcription"
    assert connector.closed == 1


def test_mismatched_admission_is_rejected_and_released(test_app):
    admission, connector = install(test_app)
    admission.target = replace(admission.target, public_model="another-tenant")
    with pytest.raises(WebSocketDenialResponse):
        with TestClient(test_app).websocket_connect(
            "/v1/realtime?model=voice", headers={"Authorization": "Bearer sk-test"}
        ):
            pytest.fail("mismatched binding accepted")
    assert not connector.opens
    assert admission.closed == 1


def test_binary_frames_terminate_and_release_both_owners(test_app):
    admission, connector = install(test_app)
    with TestClient(test_app).websocket_connect(
        "/v1/realtime?model=voice", headers={"Authorization": "Bearer sk-test"}
    ) as ws:
        ws.receive_json()
        ws.send_bytes(b"audio")
        assert ws.receive_json()["error"]["code"] == "unsupported_frame"
        with pytest.raises(WebSocketDisconnect) as caught:
            ws.receive_json()
        assert caught.value.code == 1003
    assert connector.closed == admission.closed == 1


def test_custom_request_only_auth_hook_is_not_given_a_fake_http_request(test_app):
    install(test_app)
    custom = SimpleNamespace(authenticate=AsyncMock())
    test_app.state.custom_auth_manager = custom
    with pytest.raises(WebSocketDenialResponse) as caught:
        with TestClient(test_app).websocket_connect(
            "/v1/realtime?model=voice", headers={"Authorization": "Bearer custom-token"}
        ):
            pytest.fail("HTTP-only auth hook was used for a websocket")
    assert caught.value.status_code == 503
    custom.authenticate.assert_not_called()


def test_handshake_timeout_never_opens_provider(test_app):
    _, connector = install(test_app, handshake_seconds=0.02)

    async def validate_key(key):
        await asyncio.Event().wait()

    test_app.state.key_service = SimpleNamespace(validate_key=validate_key)
    with pytest.raises(WebSocketDenialResponse) as caught:
        with TestClient(test_app).websocket_connect(
            "/v1/realtime?model=voice", headers={"Authorization": "Bearer sk-test"}
        ):
            pytest.fail("unbounded authentication")
    assert caught.value.status_code == 503
    assert not connector.opens


@pytest.mark.parametrize(
    "error_class", [BudgetExceededError, PermissionDeniedError, RateLimitError]
)
@pytest.mark.parametrize("event_id", ["client-456", "provider-secret", None])
def test_command_policy_denials_keep_safe_correlation(test_app, error_class, event_id):
    admission, connector = install(test_app)

    def deny(event):
        raise error_class(message="provider-secret", param="https://internal", code="private")

    admission.permit.authorize_client_event = deny
    with TestClient(test_app).websocket_connect(
        "/v1/realtime?model=voice", headers={"Authorization": "Bearer sk-test"}
    ) as ws:
        ws.receive_json()
        command = {"type": "response.create"}
        if event_id is not None:
            command["event_id"] = event_id
        ws.send_json(command)
        error = ws.receive_json()
        assert error["event_id"].startswith("event_")
        assert error["error"]["code"] == "admission_denied"
        if event_id == "client-456":
            assert error["error"]["event_id"] == event_id
        else:
            assert "event_id" not in error["error"]
        assert "provider-secret" not in json.dumps(error)
        assert "internal" not in json.dumps(error) and "private" not in json.dumps(error)
        with pytest.raises(WebSocketDisconnect) as caught:
            ws.receive_json()
        assert caught.value.code == 1008
    assert connector.socket.outgoing.empty()
    assert connector.closed == admission.closed == 1
    assert not test_app.state.realtime_runtime._cleanup_failed


@pytest.mark.parametrize(
    "event_type,control", [("session.update", "session"), ("response.create", "response")]
)
@pytest.mark.parametrize(
    "tools", [1, 0, True, False, "function", "", {}, {"type": "function"}, None]
)
def test_malformed_tool_containers_are_correlated_client_errors(
    test_app, event_type, control, tools
):
    admission, connector = install(test_app)
    with TestClient(test_app).websocket_connect(
        "/v1/realtime?model=voice", headers={"Authorization": "Bearer sk-test"}
    ) as ws:
        ws.receive_json()
        ws.send_json({"type": event_type, "event_id": "client-789", control: {"tools": tools}})
        error = ws.receive_json()
        assert error["error"]["code"] == "invalid_event"
        assert error["error"]["event_id"] == "client-789"
        with pytest.raises(WebSocketDisconnect) as caught:
            ws.receive_json()
        assert caught.value.code == 1008
    assert connector.socket.outgoing.empty()
    assert not admission.permit.authorized
    assert connector.closed == admission.closed == 1


@pytest.mark.parametrize(
    "event_type,control", [("session.update", "session"), ("response.create", "response")]
)
def test_deep_client_commands_are_correlated_validation_errors(test_app, event_type, control):
    admission, connector = install(test_app)
    # Parsing can accept more nesting than the subsequent event copy. Build
    # the JSON directly so the test client's encoder cannot reject it first.
    depth = sys.getrecursionlimit() // 2
    nested = "[" * depth + "0" + "]" * depth
    message = (
        '{"type":'
        + json.dumps(event_type)
        + ',"event_id":"client-deep",'
        + json.dumps(control)
        + ':{"tools":[{"type":"function","name":"noop",'
        '"parameters":{"enum":' + nested + "}}]}}"
    )
    assert json.loads(message)["event_id"] == "client-deep"
    with TestClient(test_app).websocket_connect(
        "/v1/realtime?model=voice", headers={"Authorization": "Bearer sk-test"}
    ) as ws:
        ws.receive_json()
        ws.send_text(message)
        error = ws.receive_json()
        assert error["event_id"].startswith("event_")
        assert error["error"]["code"] == "invalid_event"
        assert error["error"]["event_id"] == "client-deep"
        with pytest.raises(WebSocketDisconnect) as caught:
            ws.receive_json()
        assert caught.value.code == 1008
    assert connector.socket.outgoing.empty()
    assert not admission.permit.authorized
    assert connector.closed == admission.closed == 1
    assert not test_app.state.realtime_runtime._cleanup_failed


def test_deep_provider_events_are_sanitized_upstream_errors(test_app):
    admission, connector = install(test_app)
    depth = sys.getrecursionlimit() // 2
    message = (
        '{"type":"response.done","response":{"id":"resp1","metadata":'
        + "[" * depth
        + '"provider-secret"'
        + "]" * depth
        + "}}"
    )
    assert json.loads(message)["type"] == "response.done"
    connector.socket.incoming.put_nowait(message)
    with TestClient(test_app).websocket_connect(
        "/v1/realtime?model=voice", headers={"Authorization": "Bearer sk-test"}
    ) as ws:
        ws.receive_json()
        error = ws.receive_json()
        assert error["error"]["code"] == "invalid_upstream_event"
        assert "provider-secret" not in json.dumps(error)
        with pytest.raises(WebSocketDisconnect) as caught:
            ws.receive_json()
        assert caught.value.code == 1011
    assert not admission.permit.receipts
    assert connector.closed == admission.closed == 1
