import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from src.chat.executor import execute_chat, open_stream_with_first_chunk
from src.models.requests import ChatCompletionRequest
from src.providers.bedrock import BedrockAdapter
from src.providers.chat_hop import (
    BoundedChatResponse,
    ChatHopError,
    ChatHopFailureCause,
    execute_chat_hop,
)
from src.providers.chat_upstream import ChatUpstream
from src.providers.openai import OpenAIAdapter
from src.router.router import Deployment
from src.services.admission.output_limit_types import OutputPolicy, OutputScope, OutputSnapshot
from src.services.admission.output_token_context import OutputTokenContext
from src.services.admission.rate_limit_lease import RateLimitState


class _BusyConnection:
    sent_requests = 0

    def is_closed(self) -> bool:
        return False

    def has_expired(self) -> bool:
        return False

    def is_idle(self) -> bool:
        return False

    def is_available(self) -> bool:
        return False

    def can_handle_request(self, origin: object) -> bool:
        return True

    async def aclose(self) -> None:
        pass

    async def handle_async_request(self, request: object) -> None:
        self.sent_requests += 1
        raise AssertionError("The busy connection must not send this request")


def _governed_request(client: httpx.AsyncClient, monkeypatch):
    snapshot = OutputSnapshot(
        OutputPolicy((OutputScope("org_output_tpm", "org", 1000),)),
        10,
        660,
        (0,),
        (False,),
    )
    limiter = SimpleNamespace(account_output=AsyncMock(return_value=snapshot))
    context = OutputTokenContext(limiter, snapshot, RateLimitState())
    upstream = ChatUpstream(
        OpenAIAdapter(client), "https://provider.test/v1", "/chat/completions", {}, 10
    )
    monkeypatch.setattr("src.chat.executor.resolve_chat_upstream", lambda *args, **kwargs: upstream)
    request = SimpleNamespace(
        state=SimpleNamespace(output_token_context=context),
        app=SimpleNamespace(state=SimpleNamespace(http_client=client)),
    )
    deployment = Deployment(
        deployment_id="openai",
        model_name="test",
        deltallm_params={"model": "openai/test"},
        model_info={},
    )
    return context, limiter, request, deployment


async def _attempt(request, deployment: Deployment, *, stream: bool) -> None:
    payload = ChatCompletionRequest(
        model="test", messages=[{"role": "user", "content": "hello"}], stream=stream
    )
    if stream:
        await open_stream_with_first_chunk(request, payload, deployment)
    else:
        await execute_chat(request, payload, deployment, record_usage=False)


@pytest.mark.parametrize("stream", [False, True], ids=["json", "stream"])
async def test_real_pool_timeout_records_zero_without_sending_or_accounting(monkeypatch, stream):
    transport = httpx.AsyncHTTPTransport(limits=httpx.Limits(max_connections=1))
    busy = _BusyConnection()
    # Exercise HTTPX's real acquisition deadline with its only connection busy.
    transport._pool._connections.append(busy)
    monkeypatch.setattr(
        "src.chat.executor.build_upstream_request_timeout_for_request",
        lambda *args: httpx.Timeout(1.0, pool=0.005),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        context, limiter, request, deployment = _governed_request(client, monkeypatch)
        with pytest.raises(httpx.PoolTimeout):
            await _attempt(request, deployment, stream=stream)
        assert busy.sent_requests == 0
        assert not transport._pool._requests
        assert context.closed and context.reported_output == 0
        limiter.account_output.assert_not_awaited()
        await context.finish()
        limiter.account_output.assert_not_awaited()


@pytest.mark.parametrize("stream", [False, True], ids=["json", "stream"])
@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.WriteTimeout])
async def test_ambiguous_timeout_still_records_unknown(monkeypatch, stream, error_type):
    def failed(request):
        raise error_type("Provider transport timed out", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(failed)) as client:
        context, limiter, request, deployment = _governed_request(client, monkeypatch)
        with pytest.raises(error_type):
            await _attempt(request, deployment, stream=stream)
        assert context.closed and context.reported_output is None
        limiter.account_output.assert_awaited_once()
        assert limiter.account_output.call_args.args[0].actual is None
        await context.finish()
        limiter.account_output.assert_awaited_once()


@pytest.mark.parametrize("error_type", [httpx.PoolTimeout, httpx.ReadTimeout, httpx.WriteTimeout])
async def test_bounded_hop_preserves_error_contract_and_pool_zero_evidence(error_type):
    def failed(request):
        raise error_type("Provider transport timed out", request=request)

    output_observer, observer = Mock(), Mock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(failed)) as client:
        upstream = ChatUpstream(
            OpenAIAdapter(client), "https://provider.test/v1", "/chat/completions", {}, 10
        )
        with pytest.raises(ChatHopError) as caught:
            await execute_chat_hop(
                client=client,
                upstream=upstream,
                params={"model": "openai/test"},
                payload={"model": "test", "messages": []},
                model_name="test",
                timeout=httpx.Timeout(1),
                bounded=BoundedChatResponse(),
                output_observer=output_observer,
                observer=observer,
            )
    assert caught.value.cause == ChatHopFailureCause.TRANSPORT_ERROR
    if error_type is httpx.PoolTimeout:
        output_observer.assert_called_once_with(0)
    else:
        output_observer.assert_not_called()
    observer.assert_called_once()
    assert observer.call_args.args[:2] == ("upstream_http", "error")


@pytest.mark.parametrize("stream", [False, True], ids=["json", "stream"])
@pytest.mark.parametrize("governed", [False, True], ids=["no-policy", "output-policy"])
@pytest.mark.parametrize(
    "caller_cap,default_cap",
    [
        ("max_completion_tokens", "max_tokens"),
        ("max_tokens", "max_completion_tokens"),
        ("max_completion_tokens", "max_completion_tokens"),
        ("max_tokens", "max_tokens"),
    ],
)
async def test_caller_output_cap_overrides_deployment_default_alias(
    monkeypatch, stream, governed, caller_cap, default_cap
):
    sent = []

    def upstream(request):
        sent.append(json.loads(request.content))
        result = {
            "id": "complete",
            "object": "chat.completion",
            "created": 1,
            "model": "test",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        }
        if stream:
            result.update(
                object="chat.completion.chunk",
                choices=[{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}],
            )
            return httpx.Response(200, text=f"data: {json.dumps(result)}\n\ndata: [DONE]\n\n")
        return httpx.Response(200, json=result)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        context, limiter, request, deployment = _governed_request(client, monkeypatch)
        if not governed:
            request.state.output_token_context = None
        deployment.model_info["default_params"] = {default_cap: 1024, "seed": 123}
        payload = ChatCompletionRequest(
            model="test",
            messages=[{"role": "user", "content": "hello"}],
            stream=stream,
            **{caller_cap: 3},
        )
        if stream:
            opened = await open_stream_with_first_chunk(request, payload, deployment)
            try:
                [line async for line in opened.translated_stream]
            finally:
                if governed:
                    await context.finish()
                await opened.close()
        else:
            await execute_chat(request, payload, deployment, record_usage=False)

    assert len(sent) == 1
    assert sent[0][caller_cap] == 3
    assert sum(cap in sent[0] for cap in ("max_tokens", "max_completion_tokens")) == 1
    assert sent[0]["seed"] == 123
    if governed:
        limiter.account_output.assert_awaited_once()
        assert limiter.account_output.call_args.args[0].actual == 3
    else:
        limiter.account_output.assert_not_awaited()


@pytest.mark.parametrize("actual", [0, 7])
@pytest.mark.parametrize("has_metadata", [False, True], ids=["partial", "complete"])
@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, asyncio.CancelledError])
async def test_bedrock_transport_failure_retains_received_final_usage(
    monkeypatch, actual, has_metadata, error_type
):
    from tests.test_provider_compat import _encode_eventstream_message

    events = [
        ("messageStart", {"role": "assistant"}),
        ("messageStop", {"stopReason": "end_turn"}),
    ]
    if has_metadata:
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
            raise error_type("Transport failed after message stop")

        async def aclose(self):
            self.closes += 1

    transport_stream = FailedStream()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=transport_stream))
    ) as client:
        context, limiter, request, deployment = _governed_request(client, monkeypatch)
        deployment.deltallm_params.update(
            model="bedrock/test", aws_access_key_id="test", aws_secret_access_key="test"
        )
        upstream = ChatUpstream(
            BedrockAdapter(client), "https://bedrock.test", "/model/test/converse-stream", {}, 10
        )
        monkeypatch.setattr(
            "src.chat.executor.resolve_chat_upstream", lambda *args, **kwargs: upstream
        )
        with pytest.raises(error_type, match="Transport failed after message stop"):
            await _attempt(request, deployment, stream=True)
        assert context.closed
        assert context.reported_output == (actual if has_metadata else None)
        assert transport_stream.closes == 1
        await context.finish()
        if has_metadata and actual == 0:
            limiter.account_output.assert_not_awaited()
        else:
            limiter.account_output.assert_awaited_once()
            assert limiter.account_output.call_args.args[0].actual == (
                actual if has_metadata else None
            )
