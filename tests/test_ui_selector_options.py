from uuid import UUID

import pytest

from src.models.platform_auth import PlatformAuthContext
from tests.test_ui_route_group_addresses import BASE, HEADERS
from tests import test_ui_route_group_addresses as addresses
from tests.test_ui_route_groups import _publish_test_model_registry, _selector_policy_payload

addressable_groups = addresses.addressable_groups


@pytest.fixture
async def options_path(client, test_app, addressable_groups):
    created = await client.post(BASE, headers=HEADERS, json={"group_key": "answers"})
    group_id = created.json()["route_group_id"]
    test_app.state.model_registry = {
        "backing": [
            {
                "deployment_id": f"provider/target-{index:02d}",
                "deltallm_params": {
                    "model": "openai/tiny",
                    "api_key": "never-return-credential",
                    "api_base": "https://private-internal.example",
                },
                "model_info": {"mode": "embedding" if index == 24 else "chat"},
            }
            for index in range(25)
        ]
    }
    _publish_test_model_registry(test_app)
    return f"{BASE}/by-id/{group_id}/selector-options"


async def test_selector_options_are_independent_bounded_and_secret_safe(client, options_path):
    response = await client.get(
        options_path,
        headers=HEADERS,
        params={"limit": 2, "offset": 1, "selected_id": "provider/target-23"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    page = response.json()
    assert [item["deployment_id"] for item in page["data"]] == [
        "provider/target-01",
        "provider/target-02",
    ]
    assert page["has_more"] and page["limit"] == 2 and page["offset"] == 1
    assert page["selected"]["deployment_id"] == "provider/target-23"
    assert page["selected"]["eligible"] is False
    assert page["selected"]["unavailable_reason"]
    assert set(page["selected"]) == {
        "deployment_id",
        "model_name",
        "provider",
        "mode",
        "eligible",
        "unavailable_reason",
    }
    assert "never-return" not in response.text and "private-internal" not in response.text
    filtered = await client.get(
        options_path, headers=HEADERS, params={"search": "TARGET-23", "selected_id": "missing"}
    )
    assert [item["deployment_id"] for item in filtered.json()["data"]] == ["provider/target-23"]
    assert filtered.json()["selected"] is None
    empty = await client.get(options_path, headers=HEADERS, params={"search": "target-24"})
    assert empty.json()["data"] == []  # Nonchat deployments never become options.


@pytest.mark.parametrize("params", [{"limit": 51}, {"offset": -1}, {"search": "x" * 129}])
async def test_selector_options_bound_inputs(client, options_path, params):
    assert (await client.get(options_path, headers=HEADERS, params=params)).status_code == 422


async def test_selector_options_deny_before_inventory_or_group_lookup(
    client, test_app, monkeypatch
):
    path = f"{BASE}/by-id/{UUID(int=99)}/selector-options"
    test_app.state.route_group_repository = None
    assert (await client.get(path)).status_code == 401
    context = PlatformAuthContext(
        account_id="tenant-owner",
        email="owner@example.test",
        role="org_user",
        organization_memberships=[{"organization_id": "org-a", "role": "org_owner"}],
    )
    monkeypatch.setattr("src.middleware.admin.get_platform_auth_context", lambda request: context)
    assert (await client.get(path)).status_code == 403


async def test_selector_options_unavailable_is_not_empty(client, options_path, monkeypatch):
    def unavailable(state):
        raise RuntimeError("private runtime details")

    monkeypatch.setattr(
        "src.api.admin.endpoints.route_group_selectors.require_routing_runtime_generation",
        unavailable,
    )
    response = await client.get(options_path, headers=HEADERS)
    assert response.status_code == 503
    assert response.json() == {"detail": "Routing inventory unavailable"}


@pytest.mark.parametrize("by_id", [False, True])
async def test_validate_external_selector_does_not_add_answer_members(
    client, test_app, addressable_groups, options_path, by_id
):
    path = options_path.removesuffix("/selector-options") if by_id else f"{BASE}/answers"
    for deployment_id in ("dep-a", "dep-b"):
        await client.post(path + "/members", headers=HEADERS, json={"deployment_id": deployment_id})
    test_app.state.route_group_repository.deployment_modes = {"dep-a": "chat", "dep-b": "chat"}
    test_app.state.model_registry["answers"] = [
        {"deployment_id": deployment_id, "deltallm_params": {}, "model_info": {"mode": "chat"}}
        for deployment_id in ("dep-a", "dep-b")
    ]
    _publish_test_model_registry(test_app)
    payload = _selector_policy_payload()
    payload["selector"]["classifier_deployment_id"] = "provider/target-23"
    result = await client.post(path + "/policy/validate", headers=HEADERS, json=payload)
    assert result.status_code == 200, result.text
    assert {item["deployment_id"] for item in result.json()["policy"]["members"]} == {
        "dep-a",
        "dep-b",
    }
