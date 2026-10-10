import json
import asyncio
import anyio
import os
from uuid import uuid4

import httpx
import pytest
from redis.asyncio import Redis

from src.services.admission.limit_counter import LimitCounter
from src.services.admission.output_limit_types import OutputPolicy, OutputScope
from tests.test_cache import (
    _enable_cache,
    _configure_groq_openai_compatible_chat_model,
    _refresh_runtime_registry,
)
from tests.mcp.test_chat_execution import _RecordingGateway, _tool_call_response
from tests.test_mcp_gateway import _AuditSink

pytestmark = [
    pytest.mark.redis,
    pytest.mark.skipif(not os.getenv("DELTALLM_TEST_REDIS_URL"), reason="Redis URL is required"),
]


@pytest.fixture
async def output_app(test_app):
    redis = Redis.from_url(os.environ["DELTALLM_TEST_REDIS_URL"], decode_responses=True)
    record = next(iter(test_app.state._test_repo.records.values()))
    previous_token = record.token
    test_app.state._test_key = f"sk-output-{uuid4().hex}"
    record.token = test_app.state.key_service.hash_key(test_app.state._test_key)
    test_app.state._test_repo.records[record.token] = test_app.state._test_repo.records.pop(
        previous_token
    )
    record.rpm_limit = 1000
    record.output_tpm_limit = 100
    # Each fixture has a new namespace; existing fake auth/cache dependencies stay isolated.
    environment = uuid4().hex
    test_app.state.limit_counter = LimitCounter(
        redis_client=redis, degraded_mode="fail_closed", environment=environment
    )
    yield test_app, redis, record, environment
    await redis.aclose()


@pytest.mark.parametrize(
    "endpoint,body",
    [
        (
            "/v1/chat/completions",
            {"messages": [{"role": "user", "content": "hello"}], "max_tokens": 80},
        ),
        (
            "/v1/chat/completions",
            {"messages": [{"role": "user", "content": "hello"}], "max_completion_tokens": 80},
        ),
        ("/v1/completions", {"prompt": "hello", "max_tokens": 80}),
        ("/v1/responses", {"input": "hello", "max_output_tokens": 80}),
        ("/v1/messages", {"messages": [{"role": "user", "content": "hello"}], "max_tokens": 80}),
    ],
)
async def test_all_text_adapters_account_before_json_delivery(output_app, endpoint, body):
    app, redis, record, environment = output_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            endpoint,
            headers={"Authorization": f"Bearer {app.state._test_key}"},
            json={"model": "gpt-4o-mini", **body},
        )
    assert response.status_code == 200, response.text
    assert response.headers["x-ratelimit-limit-output-tokens"] == "100"
    assert response.headers["x-ratelimit-remaining-output-tokens"] == "99"
    keys = OutputPolicy((OutputScope("key_output_tpm", record.token, 100),)).keys(
        environment=environment
    )
    assert int(await redis.hget(keys[0], "used")) == 1


async def test_no_cap_and_cap_larger_than_remaining_do_not_change_admission(output_app):
    app, redis, record, environment = output_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]}
        before = app.state.http_client.post_calls
        missing = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {app.state._test_key}"},
            json=body,
        )
        large = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {app.state._test_key}"},
            json={**body, "max_tokens": 101},
        )
    assert missing.status_code == large.status_code == 200
    assert app.state.http_client.post_calls == before + 2
    assert int(await redis.hget(bucket_key(record, environment), "used")) == 2


@pytest.mark.parametrize("stream", [False, True])
async def test_crossing_call_finishes_and_next_call_is_denied(output_app, stream):
    app, redis, record, environment = output_app

    async def upstream(request):
        from tests.mcp.test_chat_execution import _tool_call_response

        result = _tool_call_response()
        result["choices"][0]["message"] = {"role": "assistant", "content": "ok"}
        result["usage"]["completion_tokens"] = 120
        if not stream:
            return httpx.Response(200, json=result)
        chunk = {
            **result,
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}],
        }
        return httpx.Response(
            200, content=(f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n").encode()
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as provider:
        app.state.http_client = provider
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            body = {
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "cross"}],
                "stream": stream,
            }
            headers = {"Authorization": f"Bearer {app.state._test_key}"}
            first = await client.post("/v1/chat/completions", headers=headers, json=body)
            second = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert first.status_code == 200, first.text
    assert first.headers["x-ratelimit-remaining-output-tokens"] == ("100" if stream else "0")
    assert second.status_code == 429, second.text
    assert second.headers["x-ratelimit-remaining-output-tokens"] == "0"
    assert 1 <= int(second.headers["Retry-After"]) <= 60
    assert int(await redis.hget(bucket_key(record, environment), "used")) == 120


@pytest.mark.parametrize(
    "endpoint,body",
    [
        (
            "/v1/chat/completions",
            {"messages": [{"role": "user", "content": "hello"}], "max_tokens": 80},
        ),
        ("/v1/completions", {"prompt": "hello", "max_tokens": 80}),
        ("/v1/responses", {"input": "hello", "max_output_tokens": 80}),
        ("/v1/messages", {"messages": [{"role": "user", "content": "hello"}], "max_tokens": 80}),
    ],
)
async def test_sse_records_complete_usage_and_headers_keep_admission_snapshot(
    output_app, endpoint, body
):
    app, redis, record, environment = output_app
    chunks = [
        {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "gpt-4o-mini",
            "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}],
        },
        {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "gpt-4o-mini",
            "choices": [],
            "usage": {"completion_tokens": 2},
        },
    ]

    async def upstream(request):
        return httpx.Response(
            200,
            content=(
                "\n\n".join(f"data: {json.dumps(chunk)}" for chunk in chunks)
                + "\n\ndata: [DONE]\n\n"
            ).encode(),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as provider:
        app.state.http_client = provider
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                endpoint,
                headers={"Authorization": f"Bearer {app.state._test_key}"},
                json={"model": "gpt-4o-mini", **body, "stream": True},
            )
    assert response.status_code == 200, response.text
    assert response.headers["x-ratelimit-remaining-output-tokens"] == "100"
    assert response.text
    keys = OutputPolicy((OutputScope("key_output_tpm", record.token, 100),)).keys(
        environment=environment
    )
    assert int(await redis.hget(keys[0], "used")) == 2


def bucket_key(record, environment):
    return OutputPolicy((OutputScope("key_output_tpm", record.token, 100),)).keys(
        environment=environment
    )[0]


@pytest.mark.parametrize("stream", [False, True])
async def test_cache_hit_generates_no_output_charge(output_app, stream, monkeypatch):
    app, redis, record, environment = output_app
    if stream:
        from tests.conftest import MockHTTPStreamResponse

        original = MockHTTPStreamResponse.aiter_lines

        async def with_usage(response):
            async for line in original(response):
                if line == "data: [DONE]":
                    yield 'data: {"choices":[],"usage":{"completion_tokens":2}}'
                yield line

        monkeypatch.setattr(MockHTTPStreamResponse, "aiter_lines", with_usage)
    _enable_cache(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        body = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "cached"}],
            "max_tokens": 40,
            "stream": stream,
        }
        first = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {app.state._test_key}"},
            json=body,
        )
        before = int(await redis.hget(bucket_key(record, environment), "used"))
        second = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {app.state._test_key}"},
            json=body,
        )
    assert first.status_code == second.status_code == 200
    assert second.headers["x-deltallm-cache-hit"] == "true"
    assert int(await redis.hget(bucket_key(record, environment), "used")) == before


async def test_modern_output_caps_have_separate_response_cache_entries(output_app):
    app, redis, record, environment = output_app
    _enable_cache(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        responses = []
        for cap in (40, 50, 50):
            response = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {app.state._test_key}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": "modern cached cap"}],
                    "max_completion_tokens": cap,
                },
            )
            assert response.status_code == 200, response.text
            responses.append(response)
    assert [r.headers["x-deltallm-cache-hit"] for r in responses] == ["false", "false", "true"]
    assert app.state.http_client.post_calls == 2
    assert int(await redis.hget(bucket_key(record, environment), "used")) == 2


async def test_partial_stream_with_usage_but_no_terminal_marks_unknown(output_app):
    app, redis, record, environment = output_app

    async def upstream(request):
        chunk = {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "test",
            "choices": [{"index": 0, "delta": {"content": "partial"}, "finish_reason": None}],
        }
        usage = {**chunk, "choices": [], "usage": {"completion_tokens": 2}}
        return httpx.Response(
            200, content=(f"data: {json.dumps(chunk)}\n\ndata: {json.dumps(usage)}\n\n").encode()
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as provider:
        app.state.http_client = provider
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {app.state._test_key}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": "partial"}],
                    "max_tokens": 80,
                    "stream": True,
                },
            )
    assert int(await redis.hget(bucket_key(record, environment), "used")) == 0
    assert int(await redis.hget(bucket_key(record, environment), "unknown")) == 1


async def test_cancellation_after_dispatch_marks_unknown(output_app):
    app, redis, record, environment = output_app
    dispatched = asyncio.Event()

    async def upstream(request):
        dispatched.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as provider:
        app.state.http_client = provider
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            task = asyncio.create_task(
                client.post(
                    "/v1/chat/completions",
                    headers={"Authorization": f"Bearer {app.state._test_key}"},
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [{"role": "user", "content": "cancel"}],
                        "max_tokens": 80,
                    },
                )
            )
            await asyncio.wait_for(dispatched.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert int(await redis.hget(bucket_key(record, environment), "used")) == 0
    assert int(await redis.hget(bucket_key(record, environment), "unknown")) == 1


@pytest.mark.parametrize("stream", [False, True], ids=["json", "stream"])
async def test_pool_timeout_leaves_shared_output_known_and_next_call_allowed(
    output_app, monkeypatch, stream
):
    from unittest.mock import AsyncMock
    from tests.providers.test_output_transport import _BusyConnection

    app, redis, record, environment = output_app
    record.org_output_tpm_limit = 100
    accounting = AsyncMock(wraps=app.state.limit_counter.account_output)
    app.state.limit_counter.account_output = accounting
    transport = httpx.AsyncHTTPTransport(limits=httpx.Limits(max_connections=1))
    busy = _BusyConnection()
    transport._pool._connections.append(busy)
    monkeypatch.setattr(
        "src.chat.executor.build_upstream_request_timeout_for_request",
        lambda *args: httpx.Timeout(1.0, pool=0.005),
    )
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": stream,
    }
    original_client = app.state.http_client
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        try:
            async with httpx.AsyncClient(transport=transport) as provider:
                app.state.http_client = provider
                response = await client.post(
                    "/v1/chat/completions",
                    headers={"Authorization": f"Bearer {app.state._test_key}"},
                    json=body,
                )
            assert response.status_code == 503, response.text
            assert response.json()["error"]["code"] == "upstream_pool_timeout"
            assert busy.sent_requests == 0
            accounting.assert_not_awaited()
            assert not [
                key
                async for key in redis.scan_iter(match=f"deltallm:{environment}:v2:output-tpm:*")
            ]
        finally:
            app.state.http_client = original_client
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {app.state._test_key}"},
            json={**body, "stream": False},
        )
    assert response.status_code == 200, response.text
    accounting.assert_awaited_once()
    assert accounting.call_args.args[0].actual == 1
    assert len(accounting.call_args.args[0].policy.scopes) == 2
    for key in accounting.call_args.args[0].policy.keys(environment=environment):
        assert await redis.hmget(key, "used", "unknown") == ["1", "0"]


@pytest.mark.parametrize("actual", [0, 7, 101])
async def test_bedrock_disconnect_at_final_frame_keeps_known_output(output_app, actual):
    from unittest.mock import AsyncMock
    from tests.test_provider_compat import _encode_eventstream_message

    app, redis, record, environment = output_app
    record.org_output_tpm_limit = 100
    app.state.model_registry["gpt-4o-mini"] = [
        {
            "deltallm_params": {
                "model": "bedrock/test",
                "api_base": "https://bedrock.test",
                "aws_access_key_id": "test",
                "aws_secret_access_key": "test",
            }
        }
    ]
    _refresh_runtime_registry(app)
    accounting = AsyncMock(wraps=app.state.limit_counter.account_output)
    app.state.limit_counter.account_output = accounting
    events = [
        ("messageStart", {"role": "assistant"}),
        ("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": "ok"}}),
        ("messageStop", {"stopReason": "end_turn"}),
        (
            "metadata",
            {"usage": {"inputTokens": 2, "outputTokens": actual, "totalTokens": 2 + actual}},
        ),
    ]
    frames = b"".join(
        _encode_eventstream_message({":message-type": "event", ":event-type": kind}, payload)
        for kind, payload in events
    )

    def upstream(request):
        if request.url.path.endswith("/converse-stream"):
            return httpx.Response(200, content=frames)
        return httpx.Response(
            200,
            json={
                "output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}},
                "stopReason": "end_turn",
                "usage": {"inputTokens": 2, "outputTokens": actual, "totalTokens": 2 + actual},
            },
        )

    final_sent = anyio.Event()
    body = json.dumps(
        {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
    ).encode()
    delivered_body = False

    async def receive():
        nonlocal delivered_body
        if not delivered_body:
            delivered_body = True
            return {"type": "http.request", "body": body, "more_body": False}
        await final_sent.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and b'"finish_reason":"stop"' in message.get(
            "body", b""
        ):
            final_sent.set()
            raise OSError("client disconnected during the final frame")

    scope = {
        "type": "http",
        "asgi": {"spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/v1/chat/completions",
        "raw_path": b"/v1/chat/completions",
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "server": ("test", 80),
        "client": ("127.0.0.1", 1),
        "headers": [
            (b"authorization", f"Bearer {app.state._test_key}".encode()),
            (b"content-type", b"application/json"),
        ],
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as provider:
        app.state.http_client = provider
        with pytest.raises(OSError, match="client disconnected during the final frame"):
            await asyncio.wait_for(app(scope, receive, send), 3)
        assert final_sent.is_set()
        if actual:
            accounting.assert_awaited_once()
            event = accounting.call_args.args[0]
            assert event.actual == actual and len(event.policy.scopes) == 2
            for key in event.policy.keys(environment=environment):
                assert await redis.hmget(key, "used", "unknown") == [str(actual), "0"]
        else:
            accounting.assert_not_awaited()
            assert not await redis.exists(bucket_key(record, environment))
        receipts = [
            key
            async for key in redis.scan_iter(
                match=f"deltallm:{environment}:v2:output-tpm:receipt:*"
            )
        ]
        assert len(receipts) == int(actual > 0)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {app.state._test_key}"},
                json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "next"}]},
            )
    assert response.status_code == (429 if actual >= 100 else 200), response.text


@pytest.mark.parametrize("actual", [0, 7, 101])
@pytest.mark.parametrize("field", ["inputTokens", "totalTokens"])
async def test_bedrock_invalid_metadata_accounts_raw_output_and_keeps_error(
    output_app, actual, field
):
    from unittest.mock import AsyncMock
    from tests.test_provider_compat import _encode_eventstream_message

    app, redis, record, environment = output_app
    record.org_output_tpm_limit = 100
    app.state.model_registry["gpt-4o-mini"] = [
        {
            "deltallm_params": {
                "model": "bedrock/test",
                "api_base": "https://bedrock.test",
                "aws_access_key_id": "test",
                "aws_secret_access_key": "test",
            }
        }
    ]
    _refresh_runtime_registry(app)
    accounting = AsyncMock(wraps=app.state.limit_counter.account_output)
    app.state.limit_counter.account_output = accounting
    usage = {"inputTokens": 2, "outputTokens": actual, "totalTokens": 2 + actual}
    del usage[field]
    events = [
        ("messageStart", {"role": "assistant"}),
        ("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": "ok"}}),
        ("messageStop", {"stopReason": "end_turn"}),
        ("metadata", {"usage": usage}),
    ]
    frames = b"".join(
        _encode_eventstream_message({":message-type": "event", ":event-type": kind}, payload)
        for kind, payload in events
    )
    calls = []

    def upstream(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/converse-stream"):
            return httpx.Response(200, content=frames)
        return httpx.Response(
            200,
            json={
                "output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}},
                "stopReason": "end_turn",
                "usage": {"inputTokens": 2, "outputTokens": 1, "totalTokens": 3},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as provider:
        app.state.http_client = provider
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = {"Authorization": f"Bearer {app.state._test_key}"}
            body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]}
            first = await client.post(
                "/v1/chat/completions", headers=headers, json={**body, "stream": True}
            )
            assert first.status_code == 200, first.text
            assert '"content":"ok"' in first.text
            assert '"finish_reason":"stop"' not in first.text
            assert "data: [DONE]" not in first.text
            if actual:
                accounting.assert_awaited_once()
                event = accounting.call_args.args[0]
                assert event.actual == actual and len(event.policy.scopes) == 2
                for key in event.policy.keys(environment=environment):
                    assert await redis.hmget(key, "used", "unknown") == [str(actual), "0"]
            else:
                accounting.assert_not_awaited()
                assert not await redis.exists(bucket_key(record, environment))
            receipts = [
                key
                async for key in redis.scan_iter(
                    match=f"deltallm:{environment}:v2:output-tpm:receipt:*"
                )
            ]
            assert len(receipts) == int(actual > 0)
            second = await client.post("/v1/chat/completions", headers=headers, json=body)
            assert second.status_code == (429 if actual >= 100 else 200), second.text
            assert len(calls) == (1 if actual >= 100 else 2)


@pytest.mark.parametrize("actual", [None, 0, 7, 101])
async def test_bedrock_failure_after_final_metadata_keeps_known_accounting(output_app, actual):
    from unittest.mock import AsyncMock
    from tests.test_provider_compat import _encode_eventstream_message

    app, redis, record, environment = output_app
    record.org_output_tpm_limit = 100
    app.state.model_registry["gpt-4o-mini"] = [
        {
            "deltallm_params": {
                "model": "bedrock/test",
                "api_base": "https://bedrock.test",
                "aws_access_key_id": "test",
                "aws_secret_access_key": "test",
            }
        }
    ]
    _refresh_runtime_registry(app)
    accounting = AsyncMock(wraps=app.state.limit_counter.account_output)
    app.state.limit_counter.account_output = accounting
    events = [
        ("messageStart", {"role": "assistant"}),
        ("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": "ok"}}),
        ("messageStop", {"stopReason": "end_turn"}),
    ]
    if actual is not None:
        events.append(
            (
                "metadata",
                {"usage": {"inputTokens": 2, "outputTokens": actual, "totalTokens": 2 + actual}},
            )
        )
    frames = b"".join(
        _encode_eventstream_message({":message-type": "event", ":event-type": kind}, body)
        for kind, body in events
    )

    class FailedStream(httpx.AsyncByteStream):
        closes = 0

        async def __aiter__(self):
            yield frames
            raise httpx.ReadTimeout("Transport failed after message stop")

        async def aclose(self):
            self.closes += 1

    transport_stream = FailedStream()
    calls = []

    def upstream(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/converse-stream"):
            return httpx.Response(200, stream=transport_stream)
        return httpx.Response(
            200,
            json={
                "output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}},
                "stopReason": "end_turn",
                "usage": {"inputTokens": 2, "outputTokens": 1, "totalTokens": 3},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as provider:
        app.state.http_client = provider
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = {"Authorization": f"Bearer {app.state._test_key}"}
            body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]}
            first = await client.post(
                "/v1/chat/completions", headers=headers, json={**body, "stream": True}
            )
            assert first.status_code == 200, first.text
            assert '"content":"ok"' in first.text and "data: [DONE]" not in first.text
            assert transport_stream.closes == 1
            if actual == 0:
                accounting.assert_not_awaited()
                assert not await redis.exists(bucket_key(record, environment))
            else:
                accounting.assert_awaited_once()
                event = accounting.call_args.args[0]
                assert event.actual == actual and len(event.policy.scopes) == 2
                for key in event.policy.keys(environment=environment):
                    assert await redis.hmget(key, "used", "unknown") == [
                        str(actual or 0),
                        str(int(actual is None)),
                    ]
            receipts = [
                key
                async for key in redis.scan_iter(
                    match=f"deltallm:{environment}:v2:output-tpm:receipt:*"
                )
            ]
            assert len(receipts) == int(actual != 0)
            second = await client.post("/v1/chat/completions", headers=headers, json=body)
            expected = 503 if actual is None else 429 if actual >= 100 else 200
            assert second.status_code == expected, second.text
            if actual is None:
                assert second.json()["error"]["code"] == "output_tpm_usage_unknown"
            assert len(calls) == (2 if expected == 200 else 1)


@pytest.mark.parametrize("actual", [None, 101])
async def test_asgi_stream_disconnect_accounts_and_enforces_next_call(output_app, actual):
    app, redis, record, environment = output_app
    first_sent = anyio.Event()
    accounting_started = anyio.Event()
    account_output = app.state.limit_counter.account_output

    async def account(event):
        accounting_started.set()
        await anyio.sleep(0)
        return await account_output(event)

    app.state.limit_counter.account_output = account

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            chunk = {
                "id": "disconnect",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "partial"},
                        "finish_reason": "stop" if actual is not None else None,
                    }
                ],
            }
            if actual is not None:
                chunk["usage"] = {
                    "prompt_tokens": 3,
                    "completion_tokens": actual,
                    "total_tokens": 3 + actual,
                }
            yield f"data: {json.dumps(chunk)}\n\n".encode()
            await anyio.sleep_forever()

    body = json.dumps(
        {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": True}
    ).encode()
    delivered_body = False

    async def receive():
        nonlocal delivered_body
        if not delivered_body:
            delivered_body = True
            return {"type": "http.request", "body": body, "more_body": False}
        await first_sent.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            first_sent.set()

    scope = {
        "type": "http",
        "asgi": {"spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/v1/chat/completions",
        "raw_path": b"/v1/chat/completions",
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "server": ("test", 80),
        "client": ("127.0.0.1", 1),
        "headers": [
            (b"authorization", f"Bearer {app.state._test_key}".encode()),
            (b"content-type", b"application/json"),
        ],
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Stream()))
    ) as provider:
        app.state.http_client = provider
        await asyncio.wait_for(app(scope, receive, send), 3)
        assert first_sent.is_set()
        assert accounting_started.is_set()
        assert int(await redis.hget(bucket_key(record, environment), "used")) == (actual or 0)
        assert int(await redis.hget(bucket_key(record, environment), "unknown")) == int(
            actual is None
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            blocked = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {app.state._test_key}"},
                json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "next"}]},
            )
        assert blocked.status_code == (503 if actual is None else 429), blocked.text


@pytest.mark.parametrize("policy", [None, 100])
@pytest.mark.parametrize("usage_only", [False, True])
async def test_messages_stream_keeps_terminal_usage(output_app, policy, usage_only):
    app, redis, record, environment = output_app
    record.output_tpm_limit = policy

    def upstream(request):
        chunk = {
            "id": "messages-usage",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "gpt-4o-mini",
            "choices": [{"index": 0, "delta": {"content": "hello"}, "finish_reason": "stop"}],
        }
        usage = {"prompt_tokens": 3, "completion_tokens": 7, "total_tokens": 10}
        lines = (
            [chunk, {**chunk, "choices": [], "usage": usage}]
            if usage_only
            else [{**chunk, "usage": usage}]
        )
        return httpx.Response(
            200,
            content=(
                "".join(f"data: {json.dumps(line)}\n\n" for line in lines) + "data: [DONE]\n\n"
            ).encode(),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as provider:
        app.state.http_client = provider
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/messages",
                headers={"Authorization": f"Bearer {app.state._test_key}"},
                json={
                    "model": "gpt-4o-mini",
                    "max_tokens": 128,
                    "messages": [{"role": "user", "content": "hello"}],
                    "stream": True,
                },
            )
    assert response.status_code == 200, response.text
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    final = next(event for event in events if event["type"] == "message_delta")
    assert final["usage"] == {"input_tokens": 3, "output_tokens": 7}
    if policy is not None:
        assert int(await redis.hget(bucket_key(record, environment), "used")) == 7


@pytest.mark.parametrize("first_output", [1, 101])
async def test_admitted_mcp_phases_finish_after_quota_crossing(output_app, first_output):
    app, redis, record, environment = output_app
    gateway = _RecordingGateway()
    app.state.mcp_gateway_service = gateway
    app.state.audit_service = _AuditSink()
    calls = []

    async def upstream(request):
        payload = json.loads(request.content)
        calls.append(payload)
        response = _tool_call_response()
        response["usage"]["completion_tokens"] = first_output
        if any(message["role"] == "tool" for message in payload["messages"]):
            response["choices"][0]["message"] = {"role": "assistant", "content": "done"}
            response["choices"][0]["finish_reason"] = "stop"
            response["usage"]["completion_tokens"] = 3
        return httpx.Response(200, json=response)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as provider:
        app.state.http_client = provider
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {app.state._test_key}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": "search"}],
                    "max_tokens": 80,
                    "tools": [{"type": "mcp", "server": "docs"}],
                },
            )
    assert response.status_code == 200, response.text
    assert len(calls) == 2 and gateway.tool_calls == ["docs.search"]
    assert int(await redis.hget(bucket_key(record, environment), "used")) == first_output + 3


async def test_groq_compatible_provider_dispatches_and_records_output(output_app):
    app, redis, record, environment = output_app
    _configure_groq_openai_compatible_chat_model(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {app.state._test_key}"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "compatible"}],
                "max_tokens": 80,
            },
        )
    assert response.status_code == 200, response.text
    assert app.state.http_client.post_calls == 1
    assert int(await redis.hget(bucket_key(record, environment), "used")) == 1


@pytest.mark.parametrize("output_count", [5, None])
async def test_anthropic_records_only_complete_raw_output_evidence(output_app, output_count):
    app, redis, record, environment = output_app
    app.state.model_registry["gpt-4o-mini"][0]["deltallm_params"].update(
        provider="anthropic",
        model="anthropic/claude-sonnet-4-5",
        api_base="https://api.anthropic.com/v1",
    )
    _refresh_runtime_registry(app)
    events = [
        {
            "type": "message_start",
            "message": {
                "id": "msg",
                "model": "claude-sonnet-4-5",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        },
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "done"},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": output_count} if output_count is not None else {},
        },
        {"type": "message_stop"},
    ]

    async def upstream(request):
        assert json.loads(request.content)["max_tokens"] == 80
        return httpx.Response(
            200,
            content=(
                "\n\n".join(f"data: {json.dumps(event)}" for event in events) + "\n\n"
            ).encode(),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as provider:
        app.state.http_client = provider
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {app.state._test_key}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": "anthropic"}],
                    "max_tokens": 80,
                    "stream": True,
                },
            )
    assert response.status_code == 200, response.text
    assert "data: [DONE]" in response.text
    assert int(await redis.hget(bucket_key(record, environment), "used")) == (
        0 if output_count is None else output_count
    )


@pytest.mark.parametrize("cap", [40, 80])
async def test_admitted_retry_finishes_after_unknown_attempt(output_app, cap):
    from dataclasses import replace
    from tests.mcp.test_chat_execution import _tool_call_response

    app, redis, record, environment = output_app
    manager = app.state.failover_manager
    manager.config = replace(manager.config, num_retries=1)
    dispatches = []

    async def upstream(request):
        dispatches.append(json.loads(request.content))
        if len(dispatches) == 1:
            return httpx.Response(500, json={"error": {"message": "unavailable"}})
        result = _tool_call_response()
        result["choices"][0]["message"] = {"role": "assistant", "content": "done"}
        result["choices"][0]["finish_reason"] = "stop"
        return httpx.Response(200, json=result)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as provider:
        app.state.http_client = provider
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {app.state._test_key}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": "retry"}],
                    "max_tokens": cap,
                },
            )
    assert response.status_code == 200, response.text
    assert len(dispatches) == 2
    assert int(await redis.hget(bucket_key(record, environment), "used")) == 1
    assert int(await redis.hget(bucket_key(record, environment), "unknown")) == 1
    assert "x-ratelimit-remaining-output-tokens" not in response.headers


async def test_successful_missing_stream_usage_pauses_new_calls_until_reset(output_app):
    app, redis, record, environment = output_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        body = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "unknown"}],
            "stream": True,
        }
        headers = {"Authorization": f"Bearer {app.state._test_key}"}
        first = await client.post("/v1/chat/completions", headers=headers, json=body)
        second = await client.post("/v1/chat/completions", headers=headers, json=body)
        assert first.status_code == 200 and "data: [DONE]" in first.text
        assert second.status_code == 503
        assert second.json()["error"]["code"] == "output_tpm_usage_unknown"
        assert "x-ratelimit-remaining-output-tokens" not in second.headers
        assert 1 <= int(second.headers["Retry-After"]) <= 60
        await redis.hincrby(bucket_key(record, environment), "window_id", -1)
        resumed = await client.post("/v1/chat/completions", headers=headers, json=body)
        assert resumed.status_code == 200


@pytest.mark.parametrize(
    "provider_name,layout",
    [("groq", "metadata"), ("groq", "top"), ("vllm", "top"), ("openai", "top")],
)
async def test_compatible_custom_base_streams_report_output_without_a_cap(
    output_app, provider_name, layout
):
    app, redis, record, environment = output_app
    params = app.state.model_registry["gpt-4o-mini"][0]["deltallm_params"]
    params.update(
        provider=provider_name,
        model=f"{provider_name}/text-model",
        api_base="https://compatible.test/v1",
    )
    _refresh_runtime_registry(app)

    async def upstream(request):
        payload = json.loads(request.content)
        assert "max_tokens" not in payload and "max_completion_tokens" not in payload
        if provider_name in {"vllm", "openai"}:
            assert payload["stream_options"]["include_usage"] is True
        else:
            assert "stream_options" not in payload
        chunk = {
            "id": "c",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}],
        }
        if layout == "metadata":
            chunk["x_groq"] = {"id": "r", "usage": {"completion_tokens": 7}}
        else:
            chunk["usage"] = {"completion_tokens": 7}
        return httpx.Response(
            200, content=(f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n").encode()
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as upstream_client:
        app.state.http_client = upstream_client
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {app.state._test_key}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": "compatible"}],
                    "stream": True,
                },
            )
    assert response.status_code == 200, response.text
    assert '"completion_tokens"' not in response.text
    assert int(await redis.hget(bucket_key(record, environment), "used")) == 7
