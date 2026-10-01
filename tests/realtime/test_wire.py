"""Real WebSocket framing against local providers; no OpenAI account or key."""

import json
from dataclasses import replace
from http import HTTPStatus

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import InvalidStatus

from src.realtime.runtime import RealtimeRuntime
from tests.realtime.fakes import Admission
from tests.test_stream_accounting_commit import loopback_gateway

pytestmark = pytest.mark.app


async def test_native_websocket_flow_uses_provider_credentials_and_preserves_events(test_app):
    observed = []

    async def provider(connection):
        assert connection.request.headers["Authorization"] == "Bearer provider-secret"
        await connection.send(
            '{"type":"session.created","session":{"id":"sess1","model":"provider-model"}}'
        )
        async for message in connection:
            event = json.loads(message)
            observed.append(event)
            if event["type"] == "session.update":
                await connection.send(
                    json.dumps({"type": "session.updated", "session": event["session"]})
                )
            if event["type"] == "response.create":
                await connection.send(
                    '{"type":"response.output_audio.delta","response_id":"resp1","delta":"AA=="}'
                )
                await connection.send(
                    '{"type":"response.done","response":{"id":"resp1","usage":null}}'
                )

    admission = Admission()
    async with serve(provider, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        # Only this test fixture substitutes a local provider origin. Production
        # resolve_realtime_target rejects loopback and non-TLS endpoints.
        admission.target = replace(
            admission.target, url=f"ws://127.0.0.1:{port}/v1/realtime?model=provider-model"
        )
        runtime = RealtimeRuntime(admission=admission)
        test_app.state.realtime_runtime = runtime
        async with loopback_gateway(test_app) as base:
            async with connect(
                base.replace("http:", "ws:") + "/v1/realtime?model=voice",
                additional_headers={"Authorization": "Bearer sk-test"},
                proxy=None,
            ) as socket:
                assert json.loads(await socket.recv())["session"]["model"] == "voice"
                await socket.send(
                    '{"type":"session.update","session":{"type":"realtime","model":"voice"}}'
                )
                assert json.loads(await socket.recv())["session"]["model"] == "voice"
                await socket.send(
                    '{"type":"input_audio_buffer.append","audio":"AA==","event_id":"audio1"}'
                )
                await socket.send('{"type":"response.create"}')
                assert json.loads(await socket.recv())["delta"] == "AA=="
                assert json.loads(await socket.recv())["response"]["id"] == "resp1"
        assert admission.closed == 1 and runtime.active_sessions == 0
    assert observed[0]["session"]["model"] == "provider-model"
    assert observed[1]["event_id"] == "audio1"
    assert len(admission.permit.receipts) == 1


async def test_upstream_redirect_is_not_followed_or_exposed(test_app):
    paths = []

    async def redirect(connection, request):
        paths.append(request.path)
        response = connection.respond(HTTPStatus.TEMPORARY_REDIRECT, "provider-secret")
        response.headers["Location"] = "/credential-leak"
        return response

    async def provider(connection):
        pytest.fail("redirecting provider should not accept a websocket")

    admission = Admission()
    async with serve(provider, "127.0.0.1", 0, process_request=redirect) as server:
        port = server.sockets[0].getsockname()[1]
        admission.target = replace(admission.target, url=f"ws://127.0.0.1:{port}/v1/realtime")
        test_app.state.realtime_runtime = RealtimeRuntime(admission=admission)
        async with loopback_gateway(test_app) as base:
            with pytest.raises(InvalidStatus) as caught:
                async with connect(
                    base.replace("http:", "ws:") + "/v1/realtime?model=voice",
                    additional_headers={"Authorization": "Bearer sk-test"},
                    proxy=None,
                ):
                    pytest.fail("redirect should not establish a session")
    assert caught.value.response.status_code == 503
    assert b"provider-secret" not in caught.value.response.body
    assert paths == ["/v1/realtime"]
    assert admission.closed == 1
