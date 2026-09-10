"""Terminal delivery awaits accounting, with durability determined by spend mode."""

import asyncio
from contextlib import asynccontextmanager
import json
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import uvicorn

from src.billing.spend import SpendTrackingService
from src.billing.spend_ingestion import SpendIngestionConfig, SpendIngestionService
from src.chat.stream_response import DeadlineStreamingResponse
from src.router.execution import ManagedFailoverResult, RequestDeadline
from tests.router.selection.test_realtime import configure
from tests.router.selection.provider_fixtures import response_body

pytestmark = pytest.mark.app


class SpendRecorder:
    def __init__(self, *, durable=True, fail=False):
        self.durable_ingestion_enabled = durable
        self.fail = fail
        self.events = []
        self.attempts = 0

    async def log_spend(self, **kwargs):
        self.attempts += 1
        await asyncio.sleep(0)  # A commit is awaited, never scheduled and forgotten.
        if self.fail:
            raise ConnectionError("fixture accounting unavailable")
        self.events.append(kwargs)

    async def log_request_failure(self, **kwargs):
        raise AssertionError("A post-provider commit failure must not become a zero-cost event")


class ProviderStream(httpx.AsyncByteStream):
    def __init__(self, *, hold_eof=False):
        self.hold_eof = hold_eof
        self.closed = asyncio.Event()

    async def __aiter__(self):
        frames = [
            {"id": "stream-commit", "choices": [{"index": 0, "delta": {"role": "assistant"}}]},
            {
                "id": "stream-commit",
                "choices": [{"index": 0, "delta": {"content": "answer"}, "finish_reason": "stop"}],
            },
            {
                "id": "stream-commit",
                "choices": [],
                "usage": {"prompt_tokens": 13, "completion_tokens": 5, "total_tokens": 18},
            },
        ]
        for frame in frames:
            yield f"data: {json.dumps(frame)}\n\n".encode()
        yield b"data: [DONE]\n\n"
        if self.hold_eof:
            await asyncio.Event().wait()

    async def aclose(self):
        self.closed.set()


@asynccontextmanager
async def configured_stream(test_app, *, selected=False, hold_eof=False, durable=True, fail=False):
    stream = ProviderStream(hold_eof=hold_eof)
    calls = []

    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        if selected and body["model"] == "classifier":
            return httpx.Response(200, json=response_body(text='{"lane":"economy"}'))
        return httpx.Response(200, stream=stream)

    recorder = SpendRecorder(durable=durable, fail=fail)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as upstream:
        billing = None
        if selected:
            billing, _ = configure(test_app, upstream, independent=True)
        else:
            test_app.state.http_client.stream = upstream.stream
        test_app.state.spend_tracking_service = recorder
        yield recorder, stream, calls, billing


def request_body(endpoint):
    body = {"model": "gpt-4o-mini", "stream": True}
    if endpoint == "/v1/completions":
        return {**body, "prompt": "hello"}
    if endpoint == "/v1/messages":
        body["max_tokens"] = 32
    body.update(
        {"input": "hello"}
        if endpoint.endswith("responses")
        else {"messages": [{"role": "user", "content": "hello"}]}
    )
    return body


@pytest.mark.parametrize(
    "endpoint", ["/v1/chat/completions", "/v1/responses", "/v1/completions", "/v1/messages"]
)
@pytest.mark.parametrize("durable", [True, False])
async def test_terminal_frame_follows_one_awaited_answer_write(
    test_app, monkeypatch, endpoint, durable
):
    observed = []
    releases = []
    original = DeadlineStreamingResponse.stream_response
    release = ManagedFailoverResult.release

    async def record_release(self):
        releases.append(True)
        await release(self)

    monkeypatch.setattr(ManagedFailoverResult, "release", record_release)
    async with configured_stream(test_app, durable=durable) as (recorder, stream, calls, _):

        async def observe(self, send):
            async def capture(message):
                chunk = message.get("body", b"")
                if b"data: [DONE]" in chunk or b"event: message_stop" in chunk:
                    observed.append(len(recorder.events))
                    assert releases == []  # Permit belongs to the final downstream frame.
                await send(message)

            await original(self, capture)

        monkeypatch.setattr(DeadlineStreamingResponse, "stream_response", observe)
        body = request_body(endpoint)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=test_app), base_url="http://test"
        ) as client:
            response = await client.post(
                endpoint, headers={"Authorization": "Bearer sk-test"}, json=body
            )
        assert response.status_code == 200
        assert observed == [1]
        assert recorder.attempts == 1
        assert recorder.events[0]["usage"]["total_tokens"] == 18
        assert len(calls) == 1
        assert stream.closed.is_set()
        assert releases == [True]


async def test_accounting_barrier_does_not_buffer_content_tokens(test_app, monkeypatch):
    commit_started = asyncio.Event()
    allow_commit = asyncio.Event()
    sent = []
    original = DeadlineStreamingResponse.stream_response
    async with configured_stream(test_app) as (recorder, _, _, _):
        write = recorder.log_spend

        async def blocked_write(**kwargs):
            commit_started.set()
            await allow_commit.wait()
            await write(**kwargs)

        async def observe(self, send):
            async def capture(message):
                sent.append(message.get("body", b""))
                await send(message)

            await original(self, capture)

        recorder.log_spend = blocked_write
        monkeypatch.setattr(DeadlineStreamingResponse, "stream_response", observe)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=test_app), base_url="http://test"
        ) as client:
            pending = asyncio.create_task(
                client.post(
                    "/v1/chat/completions",
                    headers={"Authorization": "Bearer sk-test"},
                    json=request_body("/v1/chat/completions"),
                )
            )
            try:
                await asyncio.wait_for(commit_started.wait(), 5)
                assert b"answer" in b"".join(sent)
                assert b"[DONE]" not in b"".join(sent)
                assert not pending.done()
                allow_commit.set()
                response = await asyncio.wait_for(pending, 5)
                assert "data: [DONE]" in response.text
                assert len(recorder.events) == 1
            finally:
                allow_commit.set()
                if not pending.done():
                    pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)


async def test_failed_answer_commit_never_sends_success_terminal(test_app):
    async with configured_stream(test_app, fail=True) as (recorder, stream, calls, _):
        transport = httpx.ASGITransport(app=test_app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer sk-test"},
                json=request_body("/v1/chat/completions"),
            )
        assert "answer" in response.text
        assert "data: [DONE]" not in response.text
        assert recorder.attempts == 1
        assert recorder.events == []
        assert len(calls) == 1  # Never repeat paid provider work for an accounting failure.
        assert stream.closed.is_set()


@pytest.mark.parametrize(
    "endpoint", ["/v1/chat/completions", "/v1/responses", "/v1/completions", "/v1/messages"]
)
@pytest.mark.parametrize("spend_mode", ["legacy", "outbox"])
async def test_terminal_guarantee_matches_real_spend_mode_on_write_failure(
    test_app, monkeypatch, caplog, endpoint, spend_mode
):
    # Keep the real ingestion service, event writer and ledger behavior. Only
    # persistence boundaries fail; the recorder above cannot model swallowed errors.
    db = SimpleNamespace(
        query_raw=AsyncMock(side_effect=ConnectionError("fixture database unavailable")),
        execute_raw=AsyncMock(side_effect=ConnectionError("fixture database unavailable")),
    )
    service = SpendIngestionService(
        db_client=db,
        writer=SpendTrackingService(db),
        config=SpendIngestionConfig(enabled=spend_mode == "outbox", worker_enabled=False),
    )
    enqueue = AsyncMock(side_effect=ConnectionError("fixture outbox unavailable"))
    monkeypatch.setattr(service.repository, "enqueue", enqueue)
    async with configured_stream(test_app) as (_, stream, calls, _):
        test_app.state.spend_tracking_service = service
        transport = httpx.ASGITransport(app=test_app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                endpoint,
                headers={"Authorization": "Bearer sk-test"},
                json=request_body(endpoint),
            )
        terminal = "event: message_stop" if endpoint == "/v1/messages" else "data: [DONE]"
        assert response.status_code == 200  # Content was already sent before accounting.
        assert "answer" in response.text
        assert len(calls) == 1 and stream.closed.is_set()
        if spend_mode == "legacy":
            # Characterize existing legacy debt, not a durability guarantee.
            assert terminal in response.text
            db.query_raw.assert_awaited_once()
            assert db.execute_raw.await_count > 0
            assert "failed to write normalized spend event" in caplog.text
            assert "failed to increment spend" in caplog.text
            enqueue.assert_not_awaited()
        else:
            assert terminal not in response.text
            enqueue.assert_awaited_once()
            assert enqueue.call_args.kwargs["event_type"] == "spend"
            db.query_raw.assert_not_awaited()
            db.execute_raw.assert_not_awaited()


async def test_required_audit_failure_withholds_terminal_without_repeating_charge(
    test_app, monkeypatch
):
    audit = AsyncMock(side_effect=ConnectionError("fixture required audit unavailable"))
    monkeypatch.setattr("src.chat.telemetry.emit_text_audit_event", audit)
    async with configured_stream(test_app) as (recorder, stream, calls, _):
        transport = httpx.ASGITransport(app=test_app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer sk-test"},
                json=request_body("/v1/chat/completions"),
            )
        assert "answer" in response.text
        assert "[DONE]" not in response.text
        assert len(recorder.events) == recorder.attempts == 1
        audit.assert_awaited_once()
        assert len(calls) == 1 and stream.closed.is_set()


async def test_blocked_answer_commit_obeys_request_deadline_and_releases_capacity(
    test_app, monkeypatch
):
    started = asyncio.Event()
    cancelled = asyncio.Event()
    async with configured_stream(test_app) as (recorder, stream, calls, _):

        async def blocked_write(**kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        recorder.log_spend = blocked_write
        monkeypatch.setattr(
            test_app.state.failover_manager,
            "create_request_deadline",
            lambda _: RequestDeadline.after(0.2),
        )
        transport = httpx.ASGITransport(app=test_app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await asyncio.wait_for(
                client.post(
                    "/v1/chat/completions",
                    headers={"Authorization": "Bearer sk-test"},
                    json=request_body("/v1/chat/completions"),
                ),
                3,
            )
        assert started.is_set() and cancelled.is_set()
        assert "answer" in response.text
        assert "[DONE]" not in response.text
        assert stream.closed.is_set()
        assert len(calls) == 1
        target = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
        state = test_app.state.router_state_backend
        assert await state.get_active_requests(target.deployment_id) == 0
        health = await state.get_health(target.deployment_id)
        assert int(health.get("consecutive_failures", 0)) == 0


@asynccontextmanager
async def loopback_gateway(app):
    started = asyncio.Event()

    class TestServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            started.set()

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = TestServer(
            uvicorn.Config(
                app,
                lifespan="off",
                access_log=False,
                log_level="critical",
                timeout_graceful_shutdown=2,
            )
        )
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            await asyncio.wait_for(started.wait(), 5)
            yield f"http://127.0.0.1:{listener.getsockname()[1]}"
        finally:
            server.should_exit = True
            try:
                await asyncio.wait_for(serving, 5)
            finally:
                if not serving.done():
                    serving.cancel()
                await asyncio.gather(serving, return_exceptions=True)


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize(
    "endpoint", ["/v1/chat/completions", "/v1/responses", "/v1/completions", "/v1/messages"]
)
async def test_real_http_close_at_done_keeps_answer_charge_without_waiting_for_provider_eof(
    test_app, selected, endpoint
):
    async with configured_stream(test_app, selected=selected, hold_eof=True) as (
        recorder,
        upstream,
        calls,
        billing,
    ):
        async with loopback_gateway(test_app) as base_url:
            async with httpx.AsyncClient(base_url=base_url, timeout=5) as client:
                async with client.stream(
                    "POST",
                    endpoint,
                    headers={"Authorization": "Bearer sk-test"},
                    json=request_body(endpoint),
                ) as response:
                    assert response.status_code == 200
                    async for line in response.aiter_lines():
                        if line in ("data: [DONE]", "event: message_stop"):
                            assert len(recorder.events) == 1
                            break  # Intentionally do not drain to EOF.
                    else:
                        pytest.fail("Missing terminal frame")
        assert recorder.attempts == 1
        assert upstream.closed.is_set()
        assert recorder.events[0]["usage"]["total_tokens"] == 18
        assert len(calls) == (2 if selected else 1)
        if selected:
            billing.accept_selector.assert_awaited_once()
