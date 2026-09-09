from __future__ import annotations

from fastapi import FastAPI
import httpx
import pytest

from tests.test_ui_route_group_addresses import (
    BASE,
    HEADERS,
    addressable_groups,  # noqa: F401 -- shared addressing fixture
)
from tests.test_ui_route_groups import _selector_policy_payload, _set_selector_model_inventory


@pytest.fixture(params=["by-id", "legacy"])
async def selector_path(
    request: pytest.FixtureRequest,
    client: httpx.AsyncClient,
    test_app: FastAPI,
    addressable_groups: object,  # noqa: F811 -- request the imported shared fixture
) -> str:
    created = await client.post(BASE, headers=HEADERS, json={"group_key": "selector-route"})
    assert created.status_code == 200, created.text
    path = f"{BASE}/by-id/{created.json()['route_group_id']}"
    for deployment_id in ("dep-a", "dep-b"):
        member = await client.post(
            path + "/members", headers=HEADERS, json={"deployment_id": deployment_id}
        )
        assert member.status_code == 200, member.text
    _set_selector_model_inventory(test_app)
    return path if request.param == "by-id" else f"{BASE}/selector-route"


@pytest.mark.asyncio
async def test_selector_draft_retains_contract_across_addressing_routes(
    client: httpx.AsyncClient, selector_path: str
) -> None:
    validation = await client.post(
        selector_path + "/policy/validate", headers=HEADERS, json=_selector_policy_payload()
    )
    assert validation.status_code == 200, validation.text
    assert validation.json()["policy"]["selector"]["default_lane"] == "quality"
    assert "context" not in validation.json()["policy"]
    draft = await client.post(
        selector_path + "/policy/draft", headers=HEADERS, json=_selector_policy_payload()
    )
    assert draft.status_code == 200, draft.text
    assert draft.json()["policy"]["semantics_version"] == 3
    update = await client.post(
        selector_path + "/policy/draft",
        headers=HEADERS,
        json={"members": [{"deployment_id": "dep-a", "weight": 7}, {"deployment_id": "dep-b"}]},
    )
    assert update.status_code == 200, update.text
    assert update.json()["policy"]["policy_json"]["members"][0]["lane"] == "economy"
    history = await client.get(selector_path + "/policies", headers=HEADERS)
    assert history.status_code == 200, history.text
    assert history.json()["policies"][0]["policy_json"]["selector"]["kind"] == "llm-tier"
    for payload in ({}, _selector_policy_payload()):
        published = await client.post(
            selector_path + "/policy/publish", headers=HEADERS, json=payload
        )
        assert published.status_code == 400, published.text
    assert "unknown_capacity=exclude" in published.text
    current = await client.get(selector_path + "/policy", headers=HEADERS)
    assert current.status_code == 200, current.text
    assert current.json()["policy"]["status"] == "draft"
    removal = await client.post(
        selector_path + "/policy/draft", headers=HEADERS, json={"selector": None}
    )
    assert removal.status_code == 200, removal.text
    document = removal.json()["policy"]["policy_json"]
    assert "selector" not in document
    assert all("lane" not in member for member in document["members"])


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["validate", "draft", "publish"])
async def test_selector_validation_errors_do_not_echo_authored_input(
    client: httpx.AsyncClient, selector_path: str, operation: str
) -> None:
    payload = _selector_policy_payload()
    payload["selector"]["kind"] = "private-invalid-selector-value"
    response = await client.post(
        selector_path + "/policy/" + operation, headers=HEADERS, json=payload
    )
    assert response.status_code == 400, response.text
    assert response.json() == {"detail": "Invalid route policy request fields or types"}
    history = await client.get(selector_path + "/policies", headers=HEADERS)
    assert history.json()["policies"] == []


def test_id_policy_openapi_uses_canonical_selector_contract(test_app: FastAPI) -> None:
    paths = test_app.openapi()["paths"]
    for operation in ("validate", "draft", "publish"):
        legacy = paths[f"{BASE}/{{group_key}}/policy/{operation}"]["post"]
        by_id = paths[f"{BASE}/by-id/{{route_group_id}}/policy/{operation}"]["post"]
        assert by_id["requestBody"] == legacy["requestBody"]
        assert by_id["responses"]["200"] == legacy["responses"]["200"]
