from dataclasses import replace

import pytest

from src.db.route_groups import RouteGroupRepository
from src.db.route_policy_lifecycle import RoutePolicyValidationContext, StoredRoutePolicyDocument
from src.router.policy_validation import PolicyMemberInventoryItem, validate_route_policy
from src.router.selection.activation import validate_selector_activation_inventory


def qualified_inventory(selector_policy):
    info = {
        "chat_capabilities": {},
        "max_tokens": 32768,
        "input_cost_per_token": 0.000001,
        "output_cost_per_token": 0.000002,
        "rpm_limit": 100,
        "tpm_limit": 1000000,
    }
    return {
        key: PolicyMemberInventoryItem(
            deployment_id=key,
            workload_mode="chat",
            model_info=info,
            provider_model="openai/mock",
        )
        for key in (selector_policy.classifier_deployment_id, "quality")
    }


def document(selector_policy):
    return {
        "selector": selector_policy.model_dump(mode="json"),
        "members": [
            {"deployment_id": selector_policy.classifier_deployment_id, "lane": "economy"},
            {"deployment_id": "quality", "lane": "quality"},
        ],
    }


@pytest.mark.parametrize("missing_model", [False, True])
def test_disabled_inventory_needs_neither_lane_nor_activation_metadata(
    selector_policy, missing_model
):
    inventory = qualified_inventory(selector_policy)
    inventory["disabled"] = PolicyMemberInventoryItem(
        deployment_id="disabled",
        enabled=False,
        workload_mode=None if missing_model else "chat",
    )
    policy = document(selector_policy)
    policy["members"].append({"deployment_id": "disabled"})
    normalized, _ = validate_route_policy(
        policy,
        available_members=inventory,
        workload_mode="chat",
        semantics_version=3,
    )
    validate_selector_activation_inventory(normalized, inventory)
    # The same gate still rejects disabling the classifier or emptying an active lane.
    for key in (selector_policy.classifier_deployment_id, "quality"):
        invalid = {**inventory, key: replace(inventory[key], enabled=False)}
        with pytest.raises(ValueError):
            validate_route_policy(
                policy,
                available_members=invalid,
                workload_mode="chat",
                semantics_version=3,
            )


def test_prepared_policy_write_separates_preserved_storage_and_validated_projection(
    selector_policy,
):
    inventory = qualified_inventory(selector_policy)
    stored = document(selector_policy)
    stored["members"][0]["server_extension"] = {"opaque": ["retained"]}
    context = RoutePolicyValidationContext("selected", "chat", inventory)
    repository = RouteGroupRepository(selector_activation_check=lambda: None)
    prepared = repository._prepare_policy_write(
        {"strategy": "weighted"},
        current=StoredRoutePolicyDocument(stored, 3),
        context=context,
    )
    assert prepared.document["members"][0]["server_extension"] == {"opaque": ["retained"]}
    assert "server_extension" not in prepared.normalized["members"][0]
    prepared.normalized["members"][0]["weight"] = 12
    assert "weight" not in prepared.document["members"][0]
    repository._require_selector_publication(prepared.normalized, context)
