from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from decimal import Decimal

import httpx
import pytest

from src.billing.selector_charge import SelectorPriceSnapshot, SelectorTokenReceipt
from src.providers.chat_upstream import resolve_chat_upstream_from_registry
from src.router.selection.contracts import SelectorHopSuccess
from src.router.selection.provider import ConcreteSelectorTarget, SelectorProviderHop
from tests.router.selection.provider_fixtures import PROMPT, registry


@pytest.mark.parametrize("api_base", [None, "https://regional.example/v1/"])
async def test_selector_and_answer_share_profile_endpoint_and_adapter(contract, api_base):
    requests = []
    body = deepcopy(contract["success"])
    body["choices"][0]["message"]["content"] = '{"lane":"economy"}'

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        adapters = registry(client)
        params = {
            "provider": contract["provider"],
            "model": contract["model"],
            "api_key": "test-selector-key",
        }
        if api_base is not None:
            params["api_base"] = api_base
        upstream = resolve_chat_upstream_from_registry(
            adapters, params, default_openai_base_url="https://unrelated-openai.example/v1"
        )
        expected_base = (api_base or contract["api_base"]).rstrip("/")
        assert upstream.api_base == expected_base
        assert upstream.adapter is adapters.compatible_chat[contract["provider"]]
        hop = SelectorProviderHop(
            client=client,
            adapters=adapters,
            target=ConcreteSelectorTarget.from_config("selector", params),
            default_openai_base_url="https://unrelated-openai.example/v1",
        )
        result = await hop.invoke(
            deployment_id="selector",
            prompt=PROMPT,
            expires_at=asyncio.get_running_loop().time() + 2,
        )
    assert isinstance(result, SelectorHopSuccess)
    assert result.text == '{"lane":"economy"}'
    assert len(requests) == 1
    assert str(requests[0].url) == expected_base + "/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer test-selector-key"
    wire = json.loads(requests[0].content)
    assert wire["max_tokens"] == 64 and wire["stream"] is False
    assert "tools" not in wire and "temperature" not in wire
    assert result.usage.cached_input_tokens == 6
    receipt = SelectorTokenReceipt(
        prompt_tokens=result.usage.prompt_tokens,
        completion_tokens=result.usage.completion_tokens,
        total_tokens=result.usage.total_tokens,
        cached_input_tokens=result.usage.cached_input_tokens,
    )
    prices = SelectorPriceSnapshot(
        source="test",
        version="1",
        currency="USD",
        input_cost_per_token=Decimal("0.01"),
        input_cost_per_token_cache_hit=Decimal("0.001"),
        output_cost_per_token=Decimal("0.02"),
        cost_per_request=Decimal("0"),
    )
    assert prices.cost(receipt) == Decimal("0.126")


@pytest.mark.parametrize(
    "extra,expected",
    [
        ({}, None),
        ({"prompt_tokens_cached": 0}, 0),
        ({"prompt_cache_hit_tokens": 6}, 6),
        ({"prompt_tokens_details": {"cached_tokens": 6}}, 6),
        ({"prompt_tokens_cached": 6, "prompt_cache_hit_tokens": 6}, 6),
    ],
)
async def test_selector_receipts_preserve_cache_aliases_and_unknown(contract, extra, expected):
    body = deepcopy(contract["success"])
    body["usage"] = {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14, **extra}
    async with httpx.AsyncClient() as client:
        receipt = registry(client).resolve(contract["provider"]).reported_token_receipt(body)
    assert receipt is not None
    assert receipt.cached_input_tokens == expected


@pytest.mark.parametrize(
    "extra",
    [
        {"prompt_tokens_cached": 6, "prompt_cache_hit_tokens": 5},
        {"prompt_cache_hit_tokens": True},
        {"prompt_cache_hit_tokens": 11},
        {"prompt_tokens_details": []},
        {"prompt_tokens": 2**31, "total_tokens": 2**31 + 4},
    ],
)
async def test_selector_receipts_reject_ambiguous_or_invalid_usage(contract, extra):
    body = deepcopy(contract["success"])
    body["usage"] = {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14, **extra}
    async with httpx.AsyncClient() as client:
        assert registry(client).resolve(contract["provider"]).reported_token_receipt(body) is None
