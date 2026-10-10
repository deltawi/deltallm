from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import Mock

import pytest

from src.models.errors import InvalidRequestError, ServiceUnavailableError
from src.models.output_limits import validate_output_limit
from src.services.admission.limit_counter import LimitCounter
from src.services.admission.output_limit_types import (
    OutputPolicy,
    OutputSnapshot,
    OutputScope,
    complete_output_count,
)
from src.services.admission.output_token_context import OutputTokenContext
from src.services.admission.rate_limit_lease import RateLimitState
from src.models.requests import ChatCompletionRequest
from src.models.responses import UserAPIKeyAuth
from src.providers.openai import OpenAIAdapter
from src.providers.anthropic import AnthropicAdapter
from src.providers.gemini import GeminiAdapter
from src.providers.bedrock import BedrockAdapter
from src.services.admission.output_limit_types import output_scopes
import asyncio
import httpx
import anyio


@pytest.mark.parametrize("value", [False, True, 0, -1, "12", 1.5, 2**31, {}])
def test_output_policy_is_strict(value):
    with pytest.raises(InvalidRequestError):
        validate_output_limit(value)


@pytest.mark.parametrize("value", [None, 1, 2**31 - 1])
def test_output_policy_accepts_only_null_or_bounded_positive_integers(value):
    assert validate_output_limit(value) == value


@pytest.mark.parametrize(
    "payload", [{}, {"max_tokens": 10}, {"max_completion_tokens": 20}, {"n": 2}]
)
def test_policy_is_independent_of_generation_parameters(payload):
    from src.services.admission.output_admission import prepare_output_policy

    auth = UserAPIKeyAuth(api_key="key", key_output_tpm_limit=100)
    assert prepare_output_policy(auth) == OutputPolicy((OutputScope("key_output_tpm", "key", 100),))
    ChatCompletionRequest(model="test", messages=[{"role": "user", "content": "ok"}], **payload)


def test_completion_only_evidence_is_independent_of_input_and_total():
    assert complete_output_count({"completion_tokens": 12}) == 12
    assert complete_output_count({"prompt_tokens": 10}) is None
    assert complete_output_count({"completion_tokens": False}) is None


async def test_shared_coordination_never_falls_back_locally():
    output = OutputPolicy((OutputScope("key_output_tpm", "key", 10),))
    for limiter in (
        LimitCounter(),
        LimitCounter(redis_client=AsyncMock(), degraded_mode="fail_open"),
    ):
        with pytest.raises(ServiceUnavailableError):
            await limiter.check_rate_limits_atomic([], output=output)


def snapshot():
    return OutputSnapshot(
        OutputPolicy((OutputScope("key_output_tpm", "key", 100),)), 10, 660, (0,), (False,)
    )


async def test_unknown_usage_is_recorded_and_next_phase_does_not_readmit():
    initial = snapshot()
    limiter = SimpleNamespace(
        account_output=AsyncMock(return_value=initial), check_rate_limits_atomic=AsyncMock()
    )
    context = OutputTokenContext(limiter, initial, RateLimitState())
    await context.begin()
    context.mark_dispatched()
    await context.finish()
    assert limiter.account_output.call_args.args[0].actual is None
    first_id = limiter.account_output.call_args.args[0].event_id
    await context.begin()
    limiter.check_rate_limits_atomic.assert_not_awaited()
    context.mark_dispatched()
    await context.finish(120)
    await context.finish(120)
    assert limiter.account_output.await_count == 2
    assert limiter.account_output.call_args.args[0].actual == 120
    assert limiter.account_output.call_args.args[0].event_id != first_id


@pytest.mark.parametrize("dispatched", [False, True])
async def test_zero_output_has_no_accounting_write(dispatched):
    initial = snapshot()
    limiter = SimpleNamespace(account_output=AsyncMock())
    context = OutputTokenContext(limiter, initial, RateLimitState())
    if dispatched:
        context.mark_dispatched()
    await context.finish(0)
    assert context.closed
    limiter.account_output.assert_not_awaited()


async def test_accounting_failure_preserves_cleanup_and_unknown_remaining():
    limiter = SimpleNamespace(account_output=AsyncMock(side_effect=RuntimeError("Redis down")))
    state = RateLimitState(output_tpm_limit=100)
    context = OutputTokenContext(limiter, snapshot(), state)
    context.mark_dispatched()
    context.observe_output(20)
    await context.finish()
    await context.finish()
    assert context.closed and state.output_tpm_remaining is None
    assert limiter.account_output.await_count == 1
    assert limiter.account_output.call_args.args[0].actual == 20


@pytest.mark.parametrize("actual", [None, 20])
async def test_accounting_survives_level_cancellation_and_duplicate_cleanup(actual):
    events = []

    async def account(event):
        await anyio.sleep(0)
        events.append(event)
        return snapshot()

    context = OutputTokenContext(
        SimpleNamespace(account_output=account), snapshot(), RateLimitState()
    )
    context.mark_dispatched()
    context.observe_output(actual)
    with anyio.CancelScope() as scope:
        scope.cancel()
        await context.finish()
    await context.finish()
    assert context.closed
    assert [event.actual for event in events] == [actual]


async def test_direct_cancellation_during_accounting_retries_the_same_event():
    started = asyncio.Event()
    events = []

    async def account(event):
        events.append(event)
        if len(events) == 1:
            started.set()
            await asyncio.Event().wait()
        return snapshot()

    state = RateLimitState(output_tpm_remaining=100)
    context = OutputTokenContext(SimpleNamespace(account_output=account), snapshot(), state)
    context.mark_dispatched()
    pending = asyncio.create_task(context.finish(20))
    await started.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert not context.closed and state.output_tpm_remaining is None
    await context.finish()
    assert context.closed
    assert len(events) == 2 and events[0] == events[1]
    assert state.warning is None


async def test_shielded_accounting_has_a_bounded_cleanup_timeout(monkeypatch):
    monkeypatch.setattr(
        "src.services.admission.output_token_context.OUTPUT_COORDINATION_TIMEOUT_SECONDS", 0.01
    )

    async def account(event):
        await anyio.sleep_forever()

    state = RateLimitState(output_tpm_remaining=100)
    context = OutputTokenContext(SimpleNamespace(account_output=account), snapshot(), state)
    context.mark_dispatched()
    await asyncio.wait_for(context.finish(20), 0.5)
    assert context.closed
    assert state.output_tpm_remaining is None
    assert state.warning == "output_tpm_accounting_failed"


async def test_local_bedrock_signing_failure_does_not_account_undispatched_output(monkeypatch):
    from src.chat.executor import execute_chat
    from src.providers.chat_upstream import ChatUpstream
    from src.router.router import Deployment

    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    limiter = SimpleNamespace(account_output=AsyncMock(return_value=snapshot()))
    context = OutputTokenContext(limiter, snapshot(), RateLimitState())
    calls = []

    async def upstream_response(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "output": {"message": {"content": [{"text": "ok"}]}},
                "stopReason": "end_turn",
                "usage": {"inputTokens": 2, "outputTokens": 3, "totalTokens": 5},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream_response)) as client:
        upstream = ChatUpstream(
            BedrockAdapter(client), "https://bedrock.test", "/model/test/converse", {}, 10
        )
        monkeypatch.setattr(
            "src.chat.executor.resolve_chat_upstream", lambda *args, **kwargs: upstream
        )
        request = SimpleNamespace(
            state=SimpleNamespace(output_token_context=context),
            app=SimpleNamespace(state=SimpleNamespace(http_client=client)),
        )
        deployment = Deployment(
            deployment_id="bedrock",
            model_name="test",
            deltallm_params={"model": "bedrock/test"},
            model_info={},
        )
        payload = ChatCompletionRequest(
            model="test", messages=[{"role": "user", "content": "hello"}]
        )
        with pytest.raises(InvalidRequestError, match="AWS credentials"):
            await execute_chat(request, payload, deployment, record_usage=False)
        assert not context.dispatched and context.event_id is None
        assert calls == []
        limiter.account_output.assert_not_awaited()
        deployment.deltallm_params.update(aws_access_key_id="test", aws_secret_access_key="test")
        await execute_chat(request, payload, deployment, record_usage=False)
        assert len(calls) == 1
        limiter.account_output.assert_awaited_once()
        assert limiter.account_output.call_args.args[0].actual == 3


async def test_modern_cap_round_trips_through_existing_adapters():
    request = ChatCompletionRequest(
        model="test", messages=[{"role": "user", "content": "hello"}], max_completion_tokens=20
    )
    async with httpx.AsyncClient() as client:
        for adapter, params, field in (
            (OpenAIAdapter(client), {"model": "openai/gpt-4o-mini"}, "max_completion_tokens"),
            (AnthropicAdapter(client), {"model": "anthropic/claude-sonnet-4-5"}, "max_tokens"),
        ):
            payload = await adapter.translate_request(request, params)
            assert payload[field] == 20


@pytest.mark.parametrize(
    "adapter_type,params,container,field",
    [
        (
            GeminiAdapter,
            {"model": "gemini/gemini-2.5-flash"},
            "generationConfig",
            "maxOutputTokens",
        ),
        (BedrockAdapter, {"model": "bedrock/anthropic.claude-v2"}, "inferenceConfig", "maxTokens"),
    ],
)
async def test_normal_translation_preserves_modern_cap(adapter_type, params, container, field):
    request = ChatCompletionRequest(
        model="test", messages=[{"role": "user", "content": "hello"}], max_completion_tokens=20
    )
    adapter = adapter_type(None)
    payload = await adapter.translate_request(request, params)
    assert payload[container][field] == 20


@pytest.mark.parametrize("single_result", [False, True])
async def test_complete_output_evidence_reuses_one_validated_json_parse(single_result):
    response = httpx.Response(
        200,
        json={
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
        },
    )
    response.json = Mock(wraps=response.json)
    observer = Mock()
    adapter = OpenAIAdapter(None)
    translate = (
        adapter.translate_single_success_response
        if single_result
        else adapter.translate_success_response
    )
    await translate(response, "test", output_observer=observer)
    response.json.assert_called_once_with()
    observer.assert_called_once_with(3)


@pytest.mark.parametrize("single_result", [False, True])
async def test_raw_usage_is_observed_before_invalid_response_translation(single_result):
    response = httpx.Response(200, json={"choices": [], "usage": {"completion_tokens": 3}})
    observer = Mock()
    adapter = OpenAIAdapter(None)
    translate = (
        adapter.translate_single_success_response
        if single_result
        else adapter.translate_success_response
    )
    from src.models.errors import ProxyError

    with pytest.raises(ProxyError):
        await translate(response, "test", output_observer=observer)
    observer.assert_called_once_with(3)


def test_enabled_policy_with_missing_identity_fails_closed():
    with pytest.raises(ServiceUnavailableError):
        output_scopes(UserAPIKeyAuth(api_key="key", team_output_tpm_limit=10))


async def test_null_policy_uses_original_admission_command():
    redis = SimpleNamespace(eval=AsyncMock(return_value=[1, 0, 1]), evalsha=AsyncMock())
    from src.services.admission.limit_counter import RateLimitCheck

    await LimitCounter(redis_client=redis).check_rate_limits_atomic(
        [RateLimitCheck("key_rpm", "key", 10, 1)]
    )
    redis.eval.assert_awaited_once()
    redis.evalsha.assert_not_awaited()
    assert "output_admission_v2" not in redis.eval.call_args.args[0]


async def test_coordination_timeout_is_bounded_and_never_penalizes_provider():
    async def unavailable(*args):
        await asyncio.Event().wait()

    redis = SimpleNamespace(eval=unavailable)
    limiter = LimitCounter(redis_client=redis, degraded_mode="fail_closed")
    admission = OutputPolicy((OutputScope("key_output_tpm", "key", 10),))
    with pytest.raises(ServiceUnavailableError) as error:
        await asyncio.wait_for(limiter.check_rate_limits_atomic([], output=admission), 2)
    assert not error.value.affects_deployment_health
    assert error.value.code == "output_tpm_unavailable"


def test_unknown_usage_retry_after_survives_http_dialects_and_batch():
    from src.services.admission.output_limit_redis import OutputUsageUnknownError
    from src.middleware.errors import proxy_error_response, anthropic_proxy_error_response
    from src.middleware.rate_limit import build_rate_limit_headers
    from src.batch.retry import classify_batch_retry

    error = OutputUsageUnknownError(snapshot().policy, 0, 660, 650)
    assert proxy_error_response(error).headers["Retry-After"] == "10"
    assert anthropic_proxy_error_response(error).headers["Retry-After"] == "10"
    assert classify_batch_retry(error).retry_after_seconds == 10
    headers = build_rate_limit_headers(
        RateLimitState(output_tpm_limit=100, output_tpm_remaining=None, output_tpm_reset=660)
    )
    assert "x-ratelimit-remaining-output-tokens" not in headers
    assert headers["x-ratelimit-limit-output-tokens"] == "100"
