from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from src.batch.accounting_execution import _receipt_counts
from src.billing.pricing.pricing import pricing_from_model_info, with_reported_prompt_cache
from src.billing.pricing.tier_pricing import PricingResolution, resolve_token_billing_result
from src.cache.pricing import provider_cache_miss_usage
from src.providers.anthropic import AnthropicAdapter
from src.providers.bedrock import BedrockAdapter
from src.providers.gemini import GeminiAdapter
from src.providers.openai import OpenAIAdapter
from tests.test_provider_compat import (
    _byte_stream,
    _encode_eventstream_message,
    _stream_json_payloads,
)

PRICES = {
    "input_cost_per_token": "0.000001",
    "output_cost_per_token": "0.000002",
    "input_cost_per_token_cache_hit": "0.0000001",
}
# vLLM, SGLang, OpenAI and Azure report cache reads only in the details object.
OPENAI_USAGE = {
    "prompt_tokens": 1_000,
    "completion_tokens": 10,
    "total_tokens": 1_010,
    "prompt_tokens_details": {"cached_tokens": 800},
}


def _resolution(info: dict[str, object] = PRICES) -> PricingResolution:
    return PricingResolution(
        callable_model="m",
        provider_model="m",
        requested_mode="sync",
        source="deployment",
        customer_model_info=dict(info),
        provider_model_info=dict(info),
        customer_token_pricing=pricing_from_model_info(info),
        provider_token_pricing=pricing_from_model_info(info),
        provider_pricing_fields=tuple(sorted(info)),
        catalog_pricing_frozen=True,
    )


def _charge(usage: dict[str, object], *, cache_hit: bool = False):
    return resolve_token_billing_result(
        _resolution(), model="m", usage=usage, cache_hit=cache_hit
    ).billing


def test_reported_cache_counts_are_added():
    usage = {
        **OPENAI_USAGE,
        "prompt_tokens_details": {"cached_tokens": 800, "cache_write_tokens": 150},
    }

    data = with_reported_prompt_cache(usage)

    assert data["prompt_tokens_cached"] == 800
    assert data["prompt_tokens_cache_write"] == 150
    assert "prompt_tokens_cached" not in usage


def test_explicit_cache_counts_win():
    usage = {
        **OPENAI_USAGE,
        "prompt_tokens_cached": 5,
        "prompt_tokens_cache_write": 0,
        "prompt_tokens_details": {"cached_tokens": 800, "cache_write_tokens": 100},
    }

    data = with_reported_prompt_cache(usage)

    assert data["prompt_tokens_cached"] == 5
    assert data["prompt_tokens_cache_write"] == 0


@pytest.mark.parametrize(
    "details",
    [
        {"cached_tokens": 1_001},
        {"cached_tokens": -1},
        {"cached_tokens": 0},
        {"cached_tokens": True},
        {"cached_tokens": "800"},
        {"cached_tokens": 800.0},
        {"cached_tokens": None},
        ["bad"],
        None,
    ],
)
def test_bad_cache_read_counts_are_ignored(details):
    data = with_reported_prompt_cache({**OPENAI_USAGE, "prompt_tokens_details": details})

    assert "prompt_tokens_cached" not in data


def test_write_count_must_fit_beside_the_read_count():
    usage = {
        **OPENAI_USAGE,
        "prompt_tokens_details": {"cached_tokens": 800, "cache_write_tokens": 201},
    }

    data = with_reported_prompt_cache(usage)

    assert data["prompt_tokens_cached"] == 800
    assert "prompt_tokens_cache_write" not in data


def test_no_prompt_count_adds_nothing():
    usage = {"prompt_tokens_details": {"cached_tokens": 3}}

    assert with_reported_prompt_cache(usage) == usage
    assert with_reported_prompt_cache(None) == {}


def test_reported_cache_reads_use_the_cache_hit_price():
    billing = _charge(OPENAI_USAGE)

    # 200 x $1/M + 800 x $0.10/M + 10 x $2/M
    assert billing.exact_cost == Decimal("0.0003")
    assert billing.cost == pytest.approx(0.0003)
    assert "input_cost_per_token_cache_hit" in billing.pricing_fields_used
    assert billing.usage_snapshot["prompt_tokens_cached"] == 800


def test_reported_cache_reads_without_a_cache_price_use_the_input_price():
    info = {k: v for k, v in PRICES.items() if k != "input_cost_per_token_cache_hit"}
    billing = resolve_token_billing_result(_resolution(info), model="m", usage=OPENAI_USAGE).billing

    assert billing.exact_cost == Decimal("0.00102")


def test_impossible_reported_count_keeps_the_full_input_price():
    usage = {**OPENAI_USAGE, "prompt_tokens_details": {"cached_tokens": 5_000}}

    assert _charge(usage).exact_cost == Decimal("0.00102")


def test_gateway_cache_hit_still_bills_the_whole_prompt_as_cached():
    hit = _charge(OPENAI_USAGE, cache_hit=True)
    plain = _charge(
        {k: v for k, v in OPENAI_USAGE.items() if k != "prompt_tokens_details"}, cache_hit=True
    )

    # 1,000 x $0.10/M + 10 x $2/M
    assert hit.exact_cost == plain.exact_cost == Decimal("0.00012")


def test_provider_miss_estimate_ignores_the_provider_cache_counts():
    billing = resolve_token_billing_result(
        _resolution(),
        model="m",
        usage=provider_cache_miss_usage(OPENAI_USAGE),
        pricing_view="provider",
    ).billing

    assert billing.exact_cost == Decimal("0.00102")


def test_batch_receipt_reads_the_reported_cache_count():
    assert _receipt_counts(OPENAI_USAGE) == {
        "prompt_tokens": 1_000,
        "completion_tokens": 10,
        "prompt_tokens_cached": 800,
    }


async def test_openai_answer_keeps_the_cached_count_for_billing():
    adapter = OpenAIAdapter(httpx.AsyncClient())
    try:
        canonical = await adapter.translate_response(
            {
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 1,
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": dict(OPENAI_USAGE),
            },
            model_name="m",
        )
    finally:
        await adapter.http_client.aclose()

    usage = canonical.model_dump(mode="json")["usage"]
    assert usage["prompt_tokens_details"] == {"cached_tokens": 800}
    assert _charge(usage).exact_cost == Decimal("0.0003")


async def test_anthropic_chat_answer_counts_cache_tokens_in_the_prompt():
    adapter = AnthropicAdapter(httpx.AsyncClient())
    try:
        canonical = await adapter.translate_response(
            {
                "id": "msg_1",
                "model": "claude",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 7,
                    "cache_read_input_tokens": 1_000,
                    "cache_creation_input_tokens": 100,
                },
            },
            model_name="anthropic/claude",
        )
        plain = await adapter.translate_response(
            {
                "id": "msg_2",
                "model": "claude",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 7},
            },
            model_name="anthropic/claude",
        )
    finally:
        await adapter.http_client.aclose()

    assert canonical.model_dump(mode="json")["usage"] == {
        "prompt_tokens": 1_110,
        "completion_tokens": 7,
        "total_tokens": 1_117,
        "prompt_tokens_details": {"cached_tokens": 1_000, "cache_write_tokens": 100},
    }
    assert plain.model_dump(mode="json")["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 7,
        "total_tokens": 17,
    }


def _bedrock_answer(usage: dict[str, int]) -> dict[str, object]:
    return {
        "output": {"message": {"content": [{"text": "ok"}]}},
        "stopReason": "end_turn",
        "usage": usage,
    }


async def test_bedrock_answer_counts_cache_tokens_in_the_prompt():
    adapter = BedrockAdapter(httpx.AsyncClient())
    try:
        cached = await adapter.translate_response(
            _bedrock_answer(
                {
                    "inputTokens": 4,
                    "outputTokens": 2,
                    "totalTokens": 1_106,
                    "cacheReadInputTokens": 1_000,
                    "cacheWriteInputTokens": 100,
                }
            ),
            model_name="bedrock/claude",
        )
        plain = await adapter.translate_response(
            _bedrock_answer({"inputTokens": 4, "outputTokens": 2, "totalTokens": 9}),
            model_name="bedrock/claude",
        )
    finally:
        await adapter.http_client.aclose()

    assert cached.model_dump(mode="json")["usage"] == {
        "prompt_tokens": 1_104,
        "completion_tokens": 2,
        "total_tokens": 1_106,
        "prompt_tokens_details": {"cached_tokens": 1_000, "cache_write_tokens": 100},
    }
    # Without cache tokens the provider total stays as it was.
    assert plain.model_dump(mode="json")["usage"] == {
        "prompt_tokens": 4,
        "completion_tokens": 2,
        "total_tokens": 9,
    }


async def test_bedrock_stream_counts_cache_tokens_in_the_prompt():
    adapter = BedrockAdapter(httpx.AsyncClient())
    frames = b"".join(
        [
            _encode_eventstream_message(
                {":message-type": "event", ":event-type": "messageStart"},
                {"role": "assistant"},
            ),
            _encode_eventstream_message(
                {":message-type": "event", ":event-type": "contentBlockDelta"},
                {"contentBlockIndex": 0, "delta": {"text": "ok"}},
            ),
            _encode_eventstream_message(
                {":message-type": "event", ":event-type": "messageStop"},
                {"stopReason": "end_turn"},
            ),
            _encode_eventstream_message(
                {":message-type": "event", ":event-type": "metadata"},
                {
                    "usage": {
                        "inputTokens": 4,
                        "outputTokens": 2,
                        "totalTokens": 1_106,
                        "cacheReadInputTokens": 1_000,
                        "cacheWriteInputTokens": 100,
                    }
                },
            ),
        ]
    )
    try:
        out = [
            line
            async for line in adapter.translate_stream(
                _byte_stream([frames]), model_name="bedrock/claude"
            )
        ]
    finally:
        await adapter.http_client.aclose()

    usages = [payload["usage"] for payload in _stream_json_payloads(out) if payload.get("usage")]
    assert usages == [
        {
            "prompt_tokens": 1_104,
            "completion_tokens": 2,
            "total_tokens": 1_106,
            "prompt_tokens_details": {"cached_tokens": 1_000, "cache_write_tokens": 100},
        }
    ]


@pytest.mark.parametrize(
    ("cached", "details"),
    [
        (3, {"cached_tokens": 3}),
        (0, None),
        (5, None),
        ("3", None),
    ],
)
async def test_gemini_answer_keeps_the_cached_content_count(cached, details):
    adapter = GeminiAdapter(httpx.AsyncClient())
    try:
        canonical = await adapter.translate_response(
            {
                "candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}],
                "usageMetadata": {
                    "promptTokenCount": 4,
                    "candidatesTokenCount": 2,
                    "totalTokenCount": 6,
                    "cachedContentTokenCount": cached,
                },
            },
            model_name="gemini/gemini-2.5-flash",
        )
    finally:
        await adapter.http_client.aclose()

    usage = canonical.model_dump(mode="json")["usage"]
    assert usage.get("prompt_tokens_details") == details
    assert usage["prompt_tokens"] == 4


@pytest.mark.parametrize("bad", [-1, "10", 1.5, True])
async def test_bedrock_rejects_a_bad_cache_count(bad):
    from src.models.errors import ProxyError

    adapter = BedrockAdapter(httpx.AsyncClient())
    try:
        with pytest.raises(ProxyError):
            await adapter.translate_response(
                _bedrock_answer(
                    {
                        "inputTokens": 4,
                        "outputTokens": 2,
                        "totalTokens": 6,
                        "cacheReadInputTokens": bad,
                    }
                ),
                model_name="bedrock/claude",
            )
    finally:
        await adapter.http_client.aclose()
