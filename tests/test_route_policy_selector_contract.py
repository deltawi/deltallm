from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from src.router.policy_validation import (
    CURRENT_POLICY_SEMANTICS_VERSION,
    PolicyMemberInventoryItem,
    merge_policy_members,
    validate_route_policy,
)
from src.router.selection.policy import (
    LLMTierSelectorPolicy,
    RouteSelectorActivationUnsupportedError,
    SELECTOR_POLICY_SEMANTICS_VERSION,
    build_routing_fingerprint,
    ensure_selector_activation_supported,
)


def _selector() -> dict[str, object]:
    return {
        "kind": "llm-tier",
        "classifier_deployment_id": "dep-mini",
        "timeout_ms": 750,
        "max_input_chars": 8_000,
        "lanes": [
            {"id": "quality", "rank": 1, "description": "Complex work"},
            {"id": "economy", "rank": 0, "description": "Routine work"},
        ],
    }


def _policy() -> dict[str, object]:
    return {
        "strategy": "least-busy",
        "selector": _selector(),
        "members": [
            {"deployment_id": "dep-mini", "lane": "economy"},
            {"deployment_id": "dep-large", "lane": "quality"},
        ],
    }


def _inventory() -> dict[str, PolicyMemberInventoryItem]:
    return {
        deployment_id: PolicyMemberInventoryItem(
            deployment_id=deployment_id,
            enabled=True,
            workload_mode="chat",
        )
        for deployment_id in ("dep-mini", "dep-large")
    }


def test_selector_contract_normalizes_lane_order_and_safe_default():
    payload = _selector()
    payload["classifier_deployment_id"] = " dep-mini "
    payload["default_lane"] = " quality "
    payload["lanes"][0]["id"] = " quality "
    payload["lanes"][0]["description"] = " Complex work "
    selector = LLMTierSelectorPolicy.model_validate(payload)

    assert [lane.id for lane in selector.lanes] == ["economy", "quality"]
    assert selector.default_lane == "quality"
    assert selector.classifier_deployment_id == "dep-mini"
    assert selector.lanes[1].description == "Complex work"


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("timeout_ms",), 99, "greater than or equal to 100"),
        (("timeout_ms",), 5_001, "less than or equal to 5000"),
        (("max_input_chars",), 255, "greater than or equal to 256"),
        (("max_input_chars",), 32_769, "less than or equal to 32768"),
        (("lanes", 0, "id"), "Quality", "String should match pattern"),
        (("lanes", 0, "description"), " ", "must not be blank"),
    ],
)
def test_selector_contract_rejects_invalid_bounds(
    path: tuple[str | int, ...], value: object, message: str
):
    payload = _selector()
    target: object = payload
    for part in path[:-1]:
        target = target[part]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]

    with pytest.raises(ValidationError, match=message):
        LLMTierSelectorPolicy.model_validate(payload)


@pytest.mark.parametrize(
    ("lanes", "message"),
    [
        ([{"id": "only", "rank": 0, "description": "Only lane"}], "at least 2 items"),
        (
            [
                {
                    "id": f"lane-{index}",
                    "rank": min(index, 7),
                    "description": f"Lane {index}",
                }
                for index in range(9)
            ],
            "at most 8 items",
        ),
    ],
)
def test_selector_contract_rejects_lane_count_outside_bounds(
    lanes: list[dict[str, object]], message: str
):
    payload = _selector()
    payload["lanes"] = lanes

    with pytest.raises(ValidationError, match=message):
        LLMTierSelectorPolicy.model_validate(payload)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda payload: payload["lanes"].append(payload["lanes"][0]), "lane ids must be unique"),
        (
            lambda payload: payload["lanes"][1].update({"rank": 1}),
            "lane ranks must be unique",
        ),
        (
            lambda payload: payload["lanes"][1].update({"rank": 2}),
            "lane ranks must be contiguous",
        ),
        (lambda payload: payload.update({"default_lane": "missing"}), "default_lane"),
        (lambda payload: payload.update({"extra": True}), "Extra inputs are not permitted"),
    ],
)
def test_selector_contract_rejects_invalid_lane_sets(mutate, message: str):
    payload = _selector()
    mutate(payload)

    with pytest.raises(ValidationError, match=message):
        LLMTierSelectorPolicy.model_validate(payload)


def test_validate_selector_policy_normalizes_strict_contract():
    normalized, warnings = validate_route_policy(
        _policy(),
        available_members=_inventory(),
        workload_mode="chat",
    )

    assert warnings == []
    assert normalized["selector"]["default_lane"] == "quality"
    assert [lane["id"] for lane in normalized["selector"]["lanes"]] == [
        "economy",
        "quality",
    ]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda policy, inventory: policy.update({"server_revision": 1}),
            "unknown fields: server_revision",
        ),
        (
            lambda policy, inventory: policy["members"][0].update({"server_assignment": 1}),
            "Extra inputs are not permitted",
        ),
        (
            lambda policy, inventory: policy.pop("members"),
            "must provide an explicit members list",
        ),
        (
            lambda policy, inventory: policy["selector"].update(
                {"classifier_deployment_id": "dep-other"}
            ),
            "classifier must be a member",
        ),
        (
            lambda policy, inventory: inventory.update(
                {
                    "dep-mini": PolicyMemberInventoryItem(
                        "dep-mini", enabled=False, workload_mode="chat"
                    )
                }
            ),
            "classifier must be an enabled route-group member",
        ),
        (
            lambda policy, inventory: inventory.update(
                {
                    "dep-mini": PolicyMemberInventoryItem(
                        "dep-mini", enabled=True, workload_mode="embedding"
                    )
                }
            ),
            "classifier must reference a chat deployment",
        ),
        (
            lambda policy, inventory: policy["members"][0].pop("lane"),
            "must have exactly one lane",
        ),
        (
            lambda policy, inventory: policy["members"][0].update({"lane": "unknown"}),
            "references unknown lane",
        ),
        (
            lambda policy, inventory: policy["members"][1].update({"enabled": False}),
            "lanes have no enabled members: quality",
        ),
    ],
)
def test_validate_selector_policy_rejects_invalid_relationships(mutate, message: str):
    policy = _policy()
    inventory = _inventory()
    mutate(policy, inventory)

    with pytest.raises(ValueError, match=message):
        validate_route_policy(policy, available_members=inventory, workload_mode="chat")


def test_validate_selector_policy_rejects_non_chat_group():
    with pytest.raises(ValueError, match="requires route group mode 'chat'"):
        validate_route_policy(
            _policy(),
            available_members=_inventory(),
            workload_mode="embedding",
        )


def test_validate_selector_policy_rejects_classifier_with_unknown_workload_mode():
    inventory = _inventory()
    inventory["dep-mini"] = PolicyMemberInventoryItem(
        "dep-mini",
        enabled=True,
        workload_mode=None,
    )

    with pytest.raises(ValueError, match="classifier must reference a chat deployment"):
        validate_route_policy(
            _policy(),
            available_members=inventory,
            workload_mode="chat",
        )


def test_validate_selector_policy_rejects_enabled_member_missing_from_inventory():
    inventory = _inventory()
    inventory.pop("dep-large")

    with pytest.raises(ValueError, match="unknown members: dep-large"):
        validate_route_policy(
            _policy(),
            available_members=inventory,
            workload_mode="chat",
        )


def test_v2_selector_and_lane_fields_remain_inert_opaque_data():
    policy = _policy()

    normalized, warnings = validate_route_policy(
        policy,
        available_members=_inventory(),
        semantics_version=2,
    )

    assert "selector" not in normalized
    assert all("lane" not in member for member in normalized["members"])
    assert warnings == [
        "Ignored opaque policy fields: selector",
        "Ignored opaque members[0] fields: lane",
        "Ignored opaque members[1] fields: lane",
    ]


def test_member_lane_merge_is_version_aware():
    base = [{"deployment_id": "dep-mini", "enabled": True}]
    policy_members = [{"deployment_id": "dep-mini", "lane": "economy"}]

    assert (
        "lane"
        not in merge_policy_members(
            base,
            policy_members,
            semantics_version=2,
        )[0]
    )
    assert (
        merge_policy_members(
            base,
            policy_members,
            semantics_version=SELECTOR_POLICY_SEMANTICS_VERSION,
        )[0]["lane"]
        == "economy"
    )


def test_member_lane_without_selector_is_rejected_for_v3():
    with pytest.raises(ValueError, match="member lanes require a selector"):
        validate_route_policy(
            {"members": [{"deployment_id": "dep-mini", "lane": "economy"}]},
            available_members=_inventory(),
            workload_mode="chat",
        )


def test_selector_activation_gate_is_version_aware():
    policy = _policy()

    ensure_selector_activation_supported(policy, semantics_version=2)
    with pytest.raises(RouteSelectorActivationUnsupportedError, match="cannot be activated"):
        ensure_selector_activation_supported(
            policy,
            semantics_version=SELECTOR_POLICY_SEMANTICS_VERSION,
        )


def test_routing_fingerprint_is_stable_for_equivalent_normalized_policy():
    normalized, _ = validate_route_policy(
        _policy(),
        available_members=_inventory(),
        workload_mode="chat",
    )
    reordered = deepcopy(normalized)
    reordered["selector"]["lanes"].reverse()
    members = normalized["members"]

    left = build_routing_fingerprint(
        workload_mode="chat",
        strategy="least-busy",
        semantics_version=CURRENT_POLICY_SEMANTICS_VERSION,
        timeout_seconds=1.5,
        retry_max_attempts=2,
        retryable_error_classes={"timeout", "rate_limit"},
        selector=normalized["selector"],
        effective_members=members,
    )
    right = build_routing_fingerprint(
        workload_mode="chat",
        strategy="least-busy",
        semantics_version=CURRENT_POLICY_SEMANTICS_VERSION,
        timeout_seconds=1.5,
        retry_max_attempts=2,
        retryable_error_classes=["rate_limit", "timeout", "timeout"],
        selector=reordered["selector"],
        effective_members=deepcopy(members),
    )

    assert left == right
    assert left.startswith("route-policy-v1:")


def test_routing_fingerprint_changes_for_response_affecting_semantics():
    normalized, _ = validate_route_policy(
        _policy(),
        available_members=_inventory(),
        workload_mode="chat",
    )
    members = normalized["members"]
    baseline = build_routing_fingerprint(
        workload_mode="chat",
        strategy="least-busy",
        semantics_version=CURRENT_POLICY_SEMANTICS_VERSION,
        selector=normalized["selector"],
        effective_members=members,
    )

    changed_members = deepcopy(members)
    changed_members[0]["lane"] = "quality"
    changed = build_routing_fingerprint(
        workload_mode="chat",
        strategy="least-busy",
        semantics_version=CURRENT_POLICY_SEMANTICS_VERSION,
        selector=normalized["selector"],
        effective_members=changed_members,
    )

    assert changed != baseline


def test_routing_fingerprint_has_no_revision_or_opaque_document_inputs():
    members = [{"deployment_id": "dep-mini", "enabled": True, "weight": 1}]

    baseline = build_routing_fingerprint(
        workload_mode="chat",
        strategy="weighted",
        semantics_version=2,
        timeout_seconds=1,
        retry_max_attempts=2,
        retryable_error_classes=["timeout", "rate_limit"],
        effective_members=members,
    )
    equivalent = build_routing_fingerprint(
        workload_mode="chat",
        strategy="weighted",
        semantics_version=2,
        timeout_seconds=1.0,
        retry_max_attempts=2,
        retryable_error_classes=["rate_limit", "timeout", "timeout"],
        effective_members=members,
    )

    assert equivalent == baseline


@pytest.mark.parametrize(
    ("override", "value"),
    [
        ("timeout_seconds", 2.0),
        ("retry_max_attempts", 3),
        ("retryable_error_classes", ["unavailable"]),
    ],
)
def test_routing_fingerprint_changes_for_effective_timeout_and_retry_semantics(
    override: str,
    value: object,
):
    members = [{"deployment_id": "dep-mini", "enabled": True}]
    inputs = {
        "workload_mode": "chat",
        "strategy": "weighted",
        "semantics_version": 2,
        "timeout_seconds": 1.0,
        "retry_max_attempts": 2,
        "retryable_error_classes": ["timeout"],
        "effective_members": members,
    }
    baseline = build_routing_fingerprint(**inputs)
    inputs[override] = value

    assert build_routing_fingerprint(**inputs) != baseline
