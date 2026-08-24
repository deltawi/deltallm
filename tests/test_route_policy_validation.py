from __future__ import annotations

import pytest

from src.router.policy_validation import (
    LEGACY_POLICY_SEMANTICS_VERSION,
    PolicyMemberInventoryItem,
    merge_policy_document_for_write,
    merge_policy_members,
    validate_route_policy,
)


def _inventory(*items: tuple[str, bool]) -> dict[str, PolicyMemberInventoryItem]:
    return {
        deployment_id: PolicyMemberInventoryItem(deployment_id, enabled=enabled)
        for deployment_id, enabled in items
    }


def test_validate_route_policy_normalizes_members():
    normalized, warnings = validate_route_policy(
        {
            "mode": "weighted",
            "strategy": "least-busy",
            "members": [{"deployment_id": "dep-a", "weight": "3", "priority": "1"}],
        }
    )

    assert normalized["mode"] == "weighted"
    assert normalized["members"][0]["weight"] == 3
    assert normalized["members"][0]["priority"] == 1
    assert warnings == [
        "Weighted mode is advisory when strategy is set explicitly; strategy takes precedence."
    ]


def test_validate_route_policy_ignores_opaque_fields_with_warning():
    normalized, warnings = validate_route_policy(
        {"strategy": "weighted", "server_revision": 9},
        available_members=_inventory(("dep-a", True)),
    )

    assert normalized == {"strategy": "weighted"}
    assert warnings == ["Ignored opaque policy fields: server_revision"]


def test_validate_route_policy_validates_retry_and_timeouts_schema():
    normalized, warnings = validate_route_policy(
        {
            "strategy": "least-busy",
            "timeouts": {"global_ms": 1200},
            "retry": {"max_attempts": 2, "retryable_error_classes": ["timeout", "rate_limit"]},
            "members": [{"deployment_id": "dep-a", "enabled": True}],
        },
        available_members=_inventory(("dep-a", True)),
    )

    assert warnings == []
    assert normalized["timeouts"]["global_ms"] == 1200
    assert normalized["retry"]["max_attempts"] == 2
    assert normalized["retry"]["retryable_error_classes"] == ["timeout", "rate_limit"]


def test_validate_route_policy_maps_fallback_mode_to_priority_strategy():
    normalized, warnings = validate_route_policy(
        {
            "mode": "fallback",
            "members": [{"deployment_id": "dep-a"}, {"deployment_id": "dep-b"}],
        },
        available_members=_inventory(("dep-a", True), ("dep-b", True)),
    )

    assert warnings == []
    assert normalized["strategy"] == "priority-based-routing"
    assert normalized["members"][0]["priority"] == 0
    assert normalized["members"][1]["priority"] == 1


def test_validate_route_policy_rejects_unsupported_modes():
    with pytest.raises(ValueError, match="mode 'adaptive' is not supported"):
        validate_route_policy({"mode": "adaptive", "members": [{"deployment_id": "dep-a"}]})


def test_validate_route_policy_ignores_runtime_unsupported_opaque_fields():
    normalized, warnings = validate_route_policy(
        {"strategy": "weighted", "conditions": []},
        available_members=_inventory(("dep-a", True)),
    )

    assert normalized == {"strategy": "weighted"}
    assert warnings == ["Ignored opaque policy fields: conditions"]


def test_validate_route_policy_rejects_unknown_member_reference():
    with pytest.raises(ValueError, match="unknown members"):
        validate_route_policy(
            {"strategy": "weighted", "members": [{"deployment_id": "dep-b", "weight": 1}]},
            available_members=_inventory(("dep-a", True)),
        )


def test_validate_route_policy_rejects_member_when_group_has_no_members():
    with pytest.raises(ValueError, match="unknown members"):
        validate_route_policy(
            {"members": [{"deployment_id": "dep-a"}]},
            available_members={},
        )


def test_validate_route_policy_rejects_duplicate_member_reference():
    with pytest.raises(ValueError, match="deployment_id is duplicated"):
        validate_route_policy(
            {
                "members": [
                    {"deployment_id": "dep-a"},
                    {"deployment_id": "dep-a"},
                ]
            },
            available_members=_inventory(("dep-a", True)),
        )


def test_explicit_policy_members_are_authoritative():
    resolved = merge_policy_members(
        [
            {"deployment_id": "dep-a", "enabled": True, "weight": 1},
            {"deployment_id": "dep-b", "enabled": True, "weight": 2},
        ],
        [{"deployment_id": "dep-b", "weight": 7}],
    )

    assert resolved == [{"deployment_id": "dep-b", "enabled": True, "weight": 7}]


def test_omitted_policy_members_inherit_base_members():
    base = [{"deployment_id": "dep-a", "enabled": True}]

    assert merge_policy_members(base, None) == base
    assert merge_policy_members(base, None) is not base


def test_legacy_explicit_policy_members_keep_widening_behavior():
    resolved = merge_policy_members(
        [
            {"deployment_id": "dep-a", "enabled": True},
            {"deployment_id": "dep-b", "enabled": True},
        ],
        [{"deployment_id": "dep-b", "priority": 0}],
        semantics_version=LEGACY_POLICY_SEMANTICS_VERSION,
    )

    assert [member["deployment_id"] for member in resolved] == ["dep-b", "dep-a"]


def test_policy_cannot_reenable_disabled_group_member():
    resolved = merge_policy_members(
        [{"deployment_id": "dep-a", "enabled": False}],
        [{"deployment_id": "dep-a", "enabled": True}],
    )

    assert resolved == [{"deployment_id": "dep-a", "enabled": False}]


def test_validate_route_policy_rejects_pool_with_only_disabled_group_members():
    with pytest.raises(ValueError, match="empty active member pool"):
        validate_route_policy(
            {"members": [{"deployment_id": "dep-a", "enabled": True}]},
            available_members=_inventory(("dep-a", False)),
        )


def test_policy_write_preserves_opaque_stored_fields():
    merged = merge_policy_document_for_write(
        {
            "strategy": "weighted",
            "server_revision": 9,
            "retry": {"max_attempts": 3, "server_classification": "strict"},
            "members": [
                {
                    "deployment_id": "dep-a",
                    "weight": 2,
                    "server_assignment": "stable",
                },
                {"deployment_id": "dep-b", "server_assignment": "removed"},
            ],
        },
        {
            "strategy": "least-busy",
            "retry": {"max_attempts": 1},
            "members": [{"deployment_id": "dep-a", "enabled": True}],
        },
    )

    assert merged == {
        "server_revision": 9,
        "strategy": "least-busy",
        "retry": {"server_classification": "strict", "max_attempts": 1},
        "members": [
            {
                "server_assignment": "stable",
                "deployment_id": "dep-a",
                "enabled": True,
            }
        ],
    }
