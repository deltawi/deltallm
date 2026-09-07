import asyncio
import hashlib
import json

import httpx
import pytest

from src.providers.chat_upstream import ChatGenerationProfile, OptionalGenerationControl
from src.providers.error_body import bound_provider_error_response_body
from src.router.selection.contracts import SelectorCause, SelectorHopSuccess, SelectorInvariantError
from tests.router.selection.provider_fixtures import (
    FixedClock,
    PROMPT,
    TrackingStream,
    bridge,
    encoded,
    response_body,
)


async def invoke(hop, seconds=1, deployment_id="classifier-concrete"):
    return await hop.invoke(
        deployment_id=deployment_id,
        prompt=PROMPT,
        expires_at=asyncio.get_running_loop().time() + seconds,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "azure", "vllm", "anthropic", "gemini", "bedrock"])
async def test_one_bounded_native_hop_without_answer_parameters(provider, monkeypatch):
    monkeypatch.setattr("src.providers.signing.datetime", FixedClock)
    stream, requests = TrackingStream([encoded(response_body(provider))]), []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=stream)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=httpx.Timeout(2)
    ) as client:
        result = await invoke(bridge(client, provider))
        assert isinstance(result, SelectorHopSuccess) and result.text == '{"lane":"economy"}'
        assert result.usage.kind == "reported" and result.usage.total_tokens == 18
        assert not client.is_closed and stream.closed == 1 and len(requests) == 1
    request = requests[0]
    wire = json.loads(request.content)
    assert len(request.content) <= 262144 and request.headers["accept-encoding"] == "identity"
    assert all(0 < value <= 1 for value in request.extensions["timeout"].values())
    for omitted in (
        "temperature",
        "tools",
        "tool_choice",
        "metadata",
        "user",
        "stop",
        "response_format",
        "top_p",
    ):
        assert omitted not in wire
    assert b"must-not-leak" not in request.content
    if provider in ("openai", "azure", "vllm"):
        assert wire["max_tokens"] == 64 and wire["n"] == 1 and wire["stream"] is False
        assert request.headers["api-key" if provider == "azure" else "authorization"] in (
            "provider-test-key",
            "Bearer provider-test-key",
        )
    elif provider == "anthropic":
        assert wire["max_tokens"] == 64 and wire["system"] == PROMPT.system
        assert request.headers["x-api-key"] == "provider-test-key" and request.url.path.endswith(
            "/messages"
        )
    elif provider == "gemini":
        assert wire["generationConfig"] == {"maxOutputTokens": 64}
        assert request.url.params["key"] == "provider-test-key"
    else:
        assert wire["inferenceConfig"] == {"maxTokens": 64}
        assert request.url.path.endswith("/classifier-small/converse")
        assert request.headers["x-amz-date"] == "20260907T120000Z"
        assert (
            request.headers["x-amz-content-sha256"] == hashlib.sha256(request.content).hexdigest()
        )
        assert (
            "Credential=test-access/20260907/us-east-1/bedrock/aws4_request"
            in request.headers["authorization"]
        )


@pytest.mark.asyncio
async def test_optional_controls_require_explicit_capability_proof():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, stream=TrackingStream([encoded(response_body())]))

    profile = ChatGenerationProfile(
        zero_temperature=OptionalGenerationControl.SUPPORTED,
        json_object=OptionalGenerationControl.SUPPORTED,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await invoke(bridge(client, profile=profile))
    assert requests[0]["temperature"] == 0 and requests[0]["response_format"] == {
        "type": "json_object"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 307, 400, 401, 403, 408, 429, 500, 503])
async def test_no_redirect_retry_or_error_body_exposure(status):
    requests, stream = [], TrackingStream([b'{"error":{"message":"private upstream detail"}}'])

    def handler(request):
        requests.append(request)
        return httpx.Response(
            status, headers={"location": "https://do-not-follow.test"}, stream=stream
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
        event_hooks={"response": [bound_provider_error_response_body]},
    ) as client:
        result = await invoke(bridge(client))
    assert result.cause is (
        SelectorCause.INVALID_RESPONSE if status < 400 else SelectorCause.PROVIDER_ERROR
    )
    assert result.usage.kind == "unknown" and stream.closed == 1 and len(requests) == 1
    assert "private" not in repr(result) and "provider.test" not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chunks,headers,cause",
    [
        ([b"x" * 65537], {}, SelectorCause.RESPONSE_TOO_LARGE),
        ([b"x" * 32768, b"x" * 32769], {"content-length": "1"}, SelectorCause.RESPONSE_TOO_LARGE),
        ([b"x"], {"content-length": "65537"}, SelectorCause.RESPONSE_TOO_LARGE),
        ([b"never decompress"], {"content-encoding": "gzip"}, SelectorCause.RESPONSE_ENCODING),
        ([b"{"], {}, SelectorCause.INVALID_RESPONSE),
    ],
)
async def test_observed_and_declared_body_bounds_before_translation(chunks, headers, cause):
    stream = TrackingStream(chunks)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers=headers, stream=stream)
        )
    ) as client:
        result = await invoke(bridge(client))
    assert result.cause is cause and stream.closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "empty",
        "length",
        "tool",
        "multiple",
        "refusal",
        "legacy_tool",
        "bad_usage",
        "oversized",
        "unicode",
        "finish",
    ],
)
async def test_invalid_canonical_completion_is_not_a_classification(mutation):
    body = response_body()
    choice = body["choices"][0]
    if mutation == "empty":
        choice["message"]["content"] = ""
    elif mutation == "length":
        choice["finish_reason"] = "length"
    elif mutation == "tool":
        choice["message"]["tool_calls"] = [
            {"id": "call", "type": "function", "function": {"name": "private", "arguments": "{}"}}
        ]
    elif mutation == "multiple":
        body["choices"].append(choice.copy())
    elif mutation in ("refusal", "legacy_tool"):
        choice["message"]["refusal" if mutation == "refusal" else "function_call"] = "private"
    elif mutation == "bad_usage":
        body["usage"]["total_tokens"] = -1
    elif mutation == "oversized":
        choice["message"]["content"] = "é" * 129
    elif mutation == "unicode":
        choice["message"]["content"] = "\ud800"
    else:
        choice["finish_reason"] = "invented"
    stream = TrackingStream([encoded(body)])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    ) as client:
        result = await invoke(bridge(client))
    assert result.cause is (
        SelectorCause.OUTPUT_TOO_LARGE
        if mutation == "oversized"
        else SelectorCause.INVALID_RESPONSE
    )
    assert stream.closed == 1
    if mutation in ("empty", "length", "tool", "oversized"):
        assert result.usage.kind == "reported"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [httpx.ReadTimeout("private"), httpx.ConnectError("private"), httpx.ReadError("private")],
)
async def test_transport_failure_closes_response_and_sanitizes(error):
    stream = TrackingStream([], read_error=error)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    ) as client:
        result = await invoke(bridge(client))
    assert result.cause is SelectorCause.TRANSPORT_ERROR and "private" not in repr(result)
    assert stream.closed == 1


@pytest.mark.asyncio
async def test_primary_failure_survives_failed_cleanup(caplog):
    stream = TrackingStream([b"x" * 65537], close_error=RuntimeError("private cleanup details"))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    ) as client:
        result = await invoke(bridge(client))
    assert result.cause is SelectorCause.RESPONSE_TOO_LARGE and stream.closed == 1
    assert "bounded_chat_response_cleanup_failed" in caplog.text and "private" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_cancel_or_total_deadline_closes_without_detached_work(cancel):
    entered = asyncio.Event()
    stream = TrackingStream([], pause=asyncio.Event())

    def handler(request):
        entered.set()
        return httpx.Response(200, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:

        async def checked_invoke():
            with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
                await invoke(bridge(client), seconds=1 if cancel else 0.015)

        async with asyncio.TaskGroup() as group:
            task = group.create_task(checked_invoke())
            await entered.wait()
            if cancel:
                task.cancel()
            await task
        assert not client.is_closed and stream.closed == 1


@pytest.mark.asyncio
async def test_wrong_concrete_deployment_aborts_without_http():
    def unexpected(request):
        pytest.fail("must not send")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        with pytest.raises(SelectorInvariantError):
            await invoke(bridge(client), deployment_id="public-alias")


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["anthropic", "gemini", "bedrock"])
@pytest.mark.parametrize("mutation", ["role", "finish", "reasoning", "nontext"])
async def test_native_lossy_normalization_cannot_hide_an_unusable_result(provider, mutation):
    body = response_body(provider)
    if provider == "anthropic":
        content, role, finish = body["content"], body, "stop_reason"
    elif provider == "gemini":
        content, role = body["candidates"][0]["content"]["parts"], body["candidates"][0]["content"]
        finish = "finishReason"
    else:
        content, role, finish = (
            body["output"]["message"]["content"],
            body["output"]["message"],
            "stopReason",
        )
    if mutation == "role":
        role["role"] = "user"
    elif mutation == "finish":
        (body["candidates"][0] if provider == "gemini" else body)[finish] = "unknown"
    elif mutation == "reasoning":
        content.append({"type": "thinking", "thinking": "private", "thought": True})
    else:
        content[0]["text"] = {"unexpected": "object"}
    stream = TrackingStream([encoded(body)])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    ) as client:
        result = await invoke(bridge(client, provider))
    assert result.cause is SelectorCause.INVALID_RESPONSE and stream.closed == 1


@pytest.mark.asyncio
async def test_gemini_multiple_candidates_are_rejected_before_collapse():
    body = response_body("gemini")
    body["candidates"].append(body["candidates"][0].copy())
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=TrackingStream([encoded(body)]))
        )
    ) as client:
        assert (await invoke(bridge(client, "gemini"))).cause is SelectorCause.INVALID_RESPONSE


@pytest.mark.asyncio
async def test_close_failure_without_primary_failure_is_classified(caplog):
    stream = TrackingStream([encoded(response_body())], close_error=RuntimeError("private"))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    ) as client:
        result = await invoke(bridge(client))
    assert result.cause is SelectorCause.TRANSPORT_ERROR and stream.closed == 1
    assert "bounded_chat_response_cleanup_failed" in caplog.text and "private" not in caplog.text


@pytest.mark.asyncio
async def test_success_body_at_exact_wire_limit_is_accepted():
    data = encoded(response_body())
    data += b" " * (65536 - len(data))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=TrackingStream([data]))
        )
    ) as client:
        assert isinstance(await invoke(bridge(client)), SelectorHopSuccess)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [True, "soon", float("inf"), float("nan")])
async def test_invalid_trusted_deadline_is_a_sanitized_invariant(value):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("unexpected HTTP"))
    ) as client:
        with pytest.raises(SelectorInvariantError):
            await bridge(client).invoke(
                deployment_id="classifier-concrete", prompt=PROMPT, expires_at=value
            )
