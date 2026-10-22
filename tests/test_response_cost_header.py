from __future__ import annotations

import pytest

from src.billing.pricing.cost import BillingResult
from src.routers.response_cost import RESPONSE_COST_HEADER, record_response_cost
from tests.test_cache import _enable_cache


def _auth(test_app) -> dict[str, str]:
    return {"Authorization": f"Bearer {test_app.state._test_key}"}


def _price(test_app, model: str, **prices: float) -> None:
    deployment = test_app.state.router.deployment_registry[model][0]
    deployment.model_info = {**dict(deployment.model_info or {}), **prices}


@pytest.mark.asyncio
async def test_chat_answer_carries_the_recorded_cost(client, test_app):
    _price(test_app, "gpt-4o-mini", input_cost_per_token=0.000002, output_cost_per_token=0.00001)

    response = await client.post(
        "/v1/chat/completions",
        headers=_auth(test_app),
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 200, response.text
    # 1 prompt token and 1 completion token in the mock answer.
    assert response.headers[RESPONSE_COST_HEADER] == "0.000012"


@pytest.mark.asyncio
async def test_embeddings_answer_carries_the_recorded_cost(client, test_app):
    _price(test_app, "text-embedding-3-small", input_cost_per_token=0.5)

    response = await client.post(
        "/v1/embeddings",
        headers=_auth(test_app),
        json={"model": "text-embedding-3-small", "input": "hello"},
    )

    assert response.status_code == 200, response.text
    assert response.headers[RESPONSE_COST_HEADER] == "1"


@pytest.mark.asyncio
async def test_cache_hit_carries_the_cost_charged_for_the_hit(client, test_app):
    _enable_cache(test_app)
    _price(test_app, "gpt-4o-mini", input_cost_per_token=1.0, output_cost_per_token=2.0)
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "cache me"}]}

    miss = await client.post("/v1/chat/completions", headers=_auth(test_app), json=body)
    hit = await client.post("/v1/chat/completions", headers=_auth(test_app), json=body)

    assert miss.headers["x-deltallm-cache-hit"] == "false"
    assert hit.headers["x-deltallm-cache-hit"] == "true"
    assert miss.headers[RESPONSE_COST_HEADER] == "3"
    assert RESPONSE_COST_HEADER in hit.headers


def test_unpriced_requests_get_no_header():
    class _State:
        pass

    class _Request:
        state = _State()

    request = _Request()
    record_response_cost(request, BillingResult(cost=0.0, unpriced_reason="missing_pricing"))
    assert not hasattr(request.state, "response_cost_header")
    record_response_cost(request, BillingResult(cost=1e-7))
    assert request.state.response_cost_header == "0.0000001"
