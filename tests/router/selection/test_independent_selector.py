from dataclasses import replace
from copy import deepcopy

import pytest

from src.route_group_config import RouteGroupConfig
from src.router import build_deployment_registry, build_route_group_policies
from src.router.policy_validation import validate_route_policy
from src.router.registry import DeploymentRegistryStore
from src.router.selection.activation import validate_selector_activation_inventory
from src.router.selection.qualification import qualify_selector_groups
from src.router.routing_identity import build_runtime_routing_fingerprints
from src.router import FallbackConfig, RoutingStrategy
from tests.router.selection.independent_fixtures import (
    independent_group,
    independent_inventory,
    independent_models,
    independent_policy,
)


def test_file_policy_allows_a_selector_outside_answer_members():
    group = RouteGroupConfig.model_validate(independent_group())
    assert group.selector.classifier_deployment_id == "tiny"
    assert {member.deployment_id for member in group.members} == {"economy", "quality"}


def test_validation_keeps_physical_inventory_separate_from_answer_members():
    inventory = independent_inventory()
    members = {key: value for key, value in inventory.items() if key != "tiny"}
    normalized, _ = validate_route_policy(
        independent_policy(),
        available_members=members,
        available_deployments=inventory,
        workload_mode="chat",
    )
    validate_selector_activation_inventory(normalized, members, inventory)
    assert {member["deployment_id"] for member in normalized["members"]} == members.keys()
    invalid = independent_policy()
    invalid["members"].append({"deployment_id": "tiny", "lane": "economy"})
    with pytest.raises(ValueError, match="unknown members"):
        validate_route_policy(
            invalid,
            available_members=members,
            available_deployments=inventory,
            workload_mode="chat",
        )


@pytest.mark.parametrize("mode", [None, "embedding"])
def test_external_classifier_still_requires_a_concrete_chat_target(mode):
    inventory = independent_inventory()
    inventory["tiny"] = replace(inventory["tiny"], workload_mode=mode)
    with pytest.raises(ValueError, match="classifier must reference a chat deployment"):
        validate_route_policy(
            independent_policy(),
            available_members={key: value for key, value in inventory.items() if key != "tiny"},
            available_deployments=inventory,
            workload_mode="chat",
        )


def test_physical_selector_survives_callable_key_shadowing_and_registry_wrapping():
    groups = [
        independent_group(),
        {
            "key": "tiny",
            "mode": "chat",
            "members": [{"deployment_id": "quality"}],
        },
    ]
    registry = DeploymentRegistryStore(build_deployment_registry(independent_models(), groups))
    assert registry["tiny"][0].deployment_id == "quality"
    assert registry.physical_deployments["tiny"].deployment_id == "tiny"
    assert registry.physical_deployments["tiny"].route_group_key is None
    qualified = qualify_selector_groups(
        build_route_group_policies(groups),
        registry.snapshot(),
        registry.physical_deployments,
    )["selected"]
    assert qualified.target.deployment_id == "tiny"
    assert qualified.capabilities.keys() == {"economy", "quality"}
    assert qualified.capacity.health_ref == registry.physical_deployments["tiny"].health_ref


def test_duplicate_physical_ids_fail_before_group_overlay():
    models = independent_models()
    models["duplicate"] = models["tiny"]
    with pytest.raises(ValueError, match="duplicate.*deployment"):
        build_deployment_registry(models, [independent_group()])


@pytest.mark.parametrize(
    "field",
    [
        "upstream",
        "credential",
        "tags",
        "context",
        "price",
        "cached-price",
        "capacity",
        "capabilities",
    ],
)
def test_external_selector_identity_changes_dependents_but_not_checkpoint_or_unrelated(field):
    models = independent_models()
    group = independent_group()
    sibling = {**deepcopy(group), "key": "sibling"}
    unrelated = {"key": "unrelated", "members": [{"deployment_id": "quality"}]}
    origin = {"key": "origin", "members": [{"deployment_id": "quality"}]}
    groups = [group, sibling, unrelated, origin]

    def identities():
        registry = build_deployment_registry(models, groups)
        policies = build_route_group_policies(groups)
        qualified = qualify_selector_groups(
            policies, registry.snapshot(), registry.physical_deployments
        )
        fingerprints = build_runtime_routing_fingerprints(
            groups=groups,
            policies=policies,
            deployments=registry,
            physical_deployments=registry.physical_deployments,
            default_strategy=RoutingStrategy.SIMPLE_SHUFFLE,
            failover_config=FallbackConfig(fallbacks={"origin": ["selected"]}),
        )
        return fingerprints, qualified["selected"].identity.fingerprint

    before, checkpoint = identities()
    assert identities() == (before, checkpoint)
    target = models["tiny"][0]
    if field == "upstream":
        target["deltallm_params"]["model"] = "openai/another-tiny"
    elif field == "credential":
        target["named_credential_id"] = "new-reference"
    else:
        key, value = {
            "tags": ("tags", ["region-eu"]),
            "context": ("max_tokens", 4096),
            "price": ("input_cost_per_token", "0.000002"),
            "cached-price": ("input_cost_per_token_cache_hit", "0.0000005"),
            "capacity": ("rpm_limit", 50),
            "capabilities": ("chat_capabilities", {"tools": True}),
        }[field]
        target["model_info"][key] = value
    after, restored_checkpoint = identities()
    for key in ("selected", "sibling", "origin"):
        assert after[key] != before[key]
    assert after["unrelated"] == before["unrelated"]
    assert restored_checkpoint == checkpoint
    target["deltallm_params"]["api_key"] = "rotated-secret-never-in-cache"
    assert identities() == (after, checkpoint)
