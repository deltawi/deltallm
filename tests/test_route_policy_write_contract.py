from copy import deepcopy

import pytest

from src.api.admin.route_group_contracts import RoutePolicyDocumentRequest
from src.router.policy_validation import validate_route_policy
from tests.test_ui_route_groups import (
    RouteGroupRecord,
    RouteGroupMemberRecord,
    _FakeRouteGroupRepository,
    _selector_policy_payload,
)


def test_transport_preserves_authored_values_and_presence():
    document = {
        "members": [{"deployment_id": " dep-a ", "enabled": "false", "lane": None}],
        "context": {"mode": " ELIGIBLE-ONLY ", "default_output_tokens": "0012"},
        "selector": None,
        "unknown": {"preserve": True},
    }
    original = deepcopy(document)
    request = RoutePolicyDocumentRequest.model_validate(document)
    assert request.to_policy_document() == original
    document["members"].clear()
    assert request.to_policy_document() == original
    assert RoutePolicyDocumentRequest().to_policy_document() == {}


def test_domain_rejects_explicit_null_members():
    with pytest.raises(ValueError, match="members must be a list"):
        validate_route_policy({"members": None})


def test_request_schema_members_is_optional_but_not_nullable():
    schema = RoutePolicyDocumentRequest.model_json_schema()
    assert "members" not in schema.get("required", [])
    assert schema["properties"]["members"]["type"] == "array"


@pytest.fixture
def policy_repository(test_app):
    test_app.state.settings.master_key = "mk-test"
    repository = _FakeRouteGroupRepository()
    repository.groups["contract-route"] = RouteGroupRecord(
        route_group_id="rg-contract", group_key="contract-route", mode="chat"
    )
    repository.members["contract-route"] = [
        RouteGroupMemberRecord(
            membership_id="member-contract", route_group_id="rg-contract", deployment_id="dep-a"
        )
    ]
    test_app.state.route_group_repository = repository
    return repository


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,suffix",
    [
        ("post", "/validate"),
        ("post", "/draft"),
        ("post", "/publish"),
        ("put", ""),
    ],
)
@pytest.mark.parametrize(
    "document",
    [
        {"members": None},
        {"context": {"mode": ["invalid"]}},
        {"context": {"default_output_tokens": True}},
        {"selector": {**_selector_policy_payload()["selector"], "timeout_ms": "private-input"}},
    ],
)
async def test_invalid_policy_inputs_are_redacted_400_without_writes(
    client, policy_repository, method, suffix, document
):
    response = await client.request(
        method,
        f"/ui/api/route-groups/contract-route/policy{suffix}",
        headers={"Authorization": "Bearer mk-test"},
        json=document,
    )
    assert response.status_code == 400, response.text
    assert "private-input" not in response.text
    assert policy_repository.policies == {}


@pytest.mark.asyncio
async def test_selector_free_context_keeps_legacy_normalization(client, policy_repository):
    response = await client.post(
        "/ui/api/route-groups/contract-route/policy/validate",
        headers={"Authorization": "Bearer mk-test"},
        json={"context": {"mode": " ELIGIBLE-ONLY ", "default_output_tokens": "0012"}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["policy"]["context"]["default_output_tokens"] == 12


@pytest.mark.asyncio
async def test_simulation_rejects_null_members_as_bad_input(client, policy_repository):
    response = await client.post(
        "/ui/api/route-groups/contract-route/policy/simulate",
        headers={"Authorization": "Bearer mk-test"},
        json={"policy": {"members": None}},
    )
    assert response.status_code == 400, response.text
    assert "members must be a list" in response.text
    assert not policy_repository.policies


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["/validate", "/draft", "/publish"])
async def test_policy_routes_still_require_authorization(client, policy_repository, suffix):
    response = await client.post(
        f"/ui/api/route-groups/contract-route/policy{suffix}",
        json={"strategy": "weighted"},
    )
    assert response.status_code == 401, response.text
    assert not policy_repository.policies
