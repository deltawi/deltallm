from __future__ import annotations

import pytest

from src.router.policy_validation import (
    LEGACY_POLICY_SEMANTICS_VERSION,
    PolicyMemberInventoryItem,
    merge_policy_document_for_write,
    merge_policy_members,
    validate_route_policy,
    validate_stored_route_policy,
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

    assert "mode" not in normalized
    assert normalized["members"][0]["weight"] == 3
    assert normalized["members"][0]["priority"] == 1
    assert warnings == [
        "Policy mode 'weighted' is deprecated; use strategy 'weighted'.",
        "Weighted mode is advisory when strategy is set explicitly; strategy takes precedence.",
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


def test_validate_route_policy_normalizes_context_schema():
    normalized, warnings = validate_route_policy(
        {
            "context": {
                "mode": "smallest-sufficient",
                "unknown_capacity": "exclude",
                "default_output_tokens": "2048",
                "safety_margin_tokens": "512",
            }
        },
        available_members=_inventory(("dep-a", True)),
        workload_mode="chat",
    )

    assert warnings == []
    assert normalized["context"] == {
        "mode": "smallest-sufficient",
        "unknown_capacity": "exclude",
        "default_output_tokens": 2048,
        "safety_margin_tokens": 512,
    }


def test_validate_route_policy_preserves_explicit_context_deletion_marker():
    normalized, warnings = validate_route_policy(
        {"context": None},
        available_members=_inventory(("dep-a", True)),
        workload_mode="rerank",
    )

    assert warnings == []
    assert normalized["context"] is None


@pytest.mark.parametrize(
    ("context", "message"),
    [
        ({"mode": "best-fit"}, "context.mode"),
        ({"unknown_capacity": "guess"}, "context.unknown_capacity"),
        ({"safety_margin_tokens": -1}, "context.safety_margin_tokens"),
        ({"default_output_tokens": 1.9}, "context.default_output_tokens"),
        ({"safety_margin_tokens": -0.9}, "context.safety_margin_tokens"),
    ],
)
def test_validate_route_policy_rejects_invalid_context_schema(context, message):
    with pytest.raises(ValueError, match=message):
        validate_route_policy(
            {"context": context},
            available_members=_inventory(("dep-a", True)),
            workload_mode="chat",
        )


@pytest.mark.parametrize("field_name", ["mode", "unknown_capacity"])
@pytest.mark.parametrize("value", [False, 0, "", None])
def test_validate_route_policy_rejects_supplied_falsy_context_choices(
    field_name: str,
    value: object,
):
    with pytest.raises(ValueError, match=f"context.{field_name}"):
        validate_route_policy(
            {"context": {field_name: value}},
            available_members=_inventory(("dep-a", True)),
            workload_mode="chat",
        )


@pytest.mark.parametrize(
    "workload_mode",
    ["image_generation", "audio_speech", "audio_transcription", "rerank"],
)
def test_validate_route_policy_rejects_context_for_unsupported_workload_modes(workload_mode):
    with pytest.raises(ValueError, match=f"route group mode '{workload_mode}'"):
        validate_route_policy(
            {"context": {"mode": "eligible-only"}},
            available_members=_inventory(("dep-a", True)),
            workload_mode=workload_mode,
        )


def test_validate_route_policy_requires_workload_mode_for_context_policy():
    with pytest.raises(ValueError, match="route group mode 'unknown'"):
        validate_route_policy(
            {"context": {"mode": "eligible-only"}},
            available_members=_inventory(("dep-a", True)),
        )


def test_validate_route_policy_maps_fallback_mode_to_priority_strategy():
    normalized, warnings = validate_route_policy(
        {
            "mode": "fallback",
            "members": [{"deployment_id": "dep-a"}, {"deployment_id": "dep-b"}],
        },
        available_members=_inventory(("dep-a", True), ("dep-b", True)),
    )

    assert warnings == [
        "Policy mode 'fallback' is deprecated; use strategy 'priority-based-routing'."
    ]
    assert normalized["strategy"] == "priority-based-routing"
    assert "mode" not in normalized
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
            "context": {"mode": "eligible-only", "server_margin_source": "catalog"},
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
            "context": {"mode": "smallest-sufficient"},
            "members": [{"deployment_id": "dep-a", "enabled": True}],
        },
    )

    assert merged == {
        "server_revision": 9,
        "strategy": "least-busy",
        "retry": {"server_classification": "strict", "max_attempts": 1},
        "context": {
            "server_margin_source": "catalog",
            "mode": "smallest-sufficient",
        },
        "members": [
            {
                "server_assignment": "stable",
                "deployment_id": "dep-a",
                "enabled": True,
            }
        ],
    }


def test_policy_write_distinguishes_omitted_context_from_explicit_deletion():
    existing = {
        "strategy": "weighted",
        "context": {
            "mode": "smallest-sufficient",
            "unknown_capacity": "exclude",
            "server_margin_source": "catalog",
        },
    }

    omitted = merge_policy_document_for_write(existing, {"strategy": "least-busy"})
    deleted = merge_policy_document_for_write(
        existing,
        {"strategy": "least-busy", "context": None},
    )

    assert omitted["context"] == existing["context"]
    assert "context" not in deleted


def test_policy_write_distinguishes_omitted_selector_from_explicit_deletion():
    selector = {"kind": "llm-tier", "classifier_deployment_id": "dep-a"}
    members = [
        {
            "deployment_id": "dep-a",
            "enabled": True,
            "weight": 2,
            "priority": 1,
            "lane": "economy",
            "server_assignment": "stable",
        }
    ]
    existing = {"strategy": "weighted", "selector": selector, "members": members}

    omitted = merge_policy_document_for_write(existing, {"strategy": "least-busy"})
    deleted = merge_policy_document_for_write(
        existing,
        {"strategy": "least-busy", "selector": None},
    )

    assert omitted["selector"] == selector
    assert omitted["members"] == members
    assert "selector" not in deleted
    assert deleted["members"] == [
        {
            "deployment_id": "dep-a",
            "enabled": True,
            "weight": 2,
            "priority": 1,
            "server_assignment": "stable",
        }
    ]


def test_policy_write_preserves_lanes_omitted_from_replacement_members():
    selector = {"kind": "llm-tier", "classifier_deployment_id": "dep-a"}
    existing = {
        "strategy": "least-busy",
        "selector": selector,
        "members": [
            {
                "deployment_id": "dep-a",
                "weight": 2,
                "lane": "economy",
                "server_assignment": "stable",
            },
            {"deployment_id": "dep-b", "lane": "quality"},
        ],
    }

    merged = merge_policy_document_for_write(
        existing,
        {
            "members": [
                {"deployment_id": "dep-a", "weight": 7},
                {"deployment_id": "dep-b"},
            ]
        },
    )

    assert merged["selector"] == selector
    assert merged["members"] == [
        {
            "server_assignment": "stable",
            "deployment_id": "dep-a",
            "weight": 7,
            "lane": "economy",
        },
        {"deployment_id": "dep-b", "lane": "quality"},
    ]


def test_policy_write_does_not_restore_removed_or_new_selector_members():
    existing = {
        "selector": {"kind": "llm-tier", "classifier_deployment_id": "dep-a"},
        "members": [
            {"deployment_id": "dep-a", "lane": "economy"},
            {"deployment_id": "dep-b", "lane": "quality"},
        ],
    }

    merged = merge_policy_document_for_write(
        existing,
        {
            "members": [
                {"deployment_id": "dep-a"},
                {"deployment_id": "dep-new"},
            ]
        },
    )

    assert merged["members"] == [
        {"deployment_id": "dep-a", "lane": "economy"},
        {"deployment_id": "dep-new"},
    ]


def test_policy_write_selector_removal_keeps_submitted_members_authoritative():
    existing = {
        "selector": {"kind": "llm-tier", "classifier_deployment_id": "dep-a"},
        "members": [
            {"deployment_id": "dep-a", "lane": "economy"},
            {
                "deployment_id": "dep-b",
                "lane": "quality",
                "server_assignment": "stable",
            },
        ],
    }

    merged = merge_policy_document_for_write(
        existing,
        {
            "selector": None,
            "members": [{"deployment_id": "dep-b", "weight": 9, "lane": "economy"}],
        },
    )

    assert "selector" not in merged
    assert merged["members"] == [
        {
            "server_assignment": "stable",
            "deployment_id": "dep-b",
            "weight": 9,
        }
    ]


def test_policy_write_replaced_selector_does_not_inherit_old_lanes():
    existing = {
        "selector": {"kind": "llm-tier", "classifier_deployment_id": "dep-a"},
        "members": [{"deployment_id": "dep-a", "lane": "economy"}],
    }
    replacement_selector = {"kind": "llm-tier", "classifier_deployment_id": "dep-a"}

    merged = merge_policy_document_for_write(
        existing,
        {
            "selector": replacement_selector,
            "members": [{"deployment_id": "dep-a"}],
        },
    )

    assert merged["selector"] == replacement_selector
    assert merged["members"] == [{"deployment_id": "dep-a"}]

    incomplete_replacement = merge_policy_document_for_write(
        existing,
        {"selector": replacement_selector},
    )

    assert "members" not in incomplete_replacement


def test_stored_selector_policy_validates_routing_projection_and_preserves_trust_boundary():
    payload = {
        "strategy": "least-busy",
        "server_revision": 9,
        "selector": {
            "kind": "llm-tier",
            "classifier_deployment_id": "dep-a",
            "lanes": [
                {"id": "economy", "rank": 0, "description": "Routine work"},
                {"id": "quality", "rank": 1, "description": "Complex work"},
            ],
        },
        "members": [
            {
                "deployment_id": "dep-a",
                "lane": "economy",
                "server_assignment": "stable",
            },
            {"deployment_id": "dep-b", "lane": "quality"},
        ],
    }
    inventory = {
        deployment_id: PolicyMemberInventoryItem(
            deployment_id,
            enabled=True,
            workload_mode="chat",
        )
        for deployment_id in ("dep-a", "dep-b")
    }

    with pytest.raises(ValueError, match="unknown fields: server_revision"):
        validate_route_policy(payload, available_members=inventory, workload_mode="chat")

    normalized, warnings = validate_stored_route_policy(
        payload,
        available_members=inventory,
        workload_mode="chat",
    )

    assert "server_revision" not in normalized
    assert "server_assignment" not in normalized["members"][0]
    assert normalized["selector"]["default_lane"] == "quality"
    assert warnings == [
        "Ignored opaque policy fields: server_revision",
        "Ignored opaque members[0] fields: server_assignment",
    ]


def test_stored_selector_policy_keeps_nested_selector_contract_strict():
    payload = {
        "selector": {
            "kind": "llm-tier",
            "classifier_deployment_id": "dep-a",
            "server_selector_field": True,
            "lanes": [
                {"id": "economy", "rank": 0, "description": "Routine work"},
                {"id": "quality", "rank": 1, "description": "Complex work"},
            ],
        },
        "members": [
            {"deployment_id": "dep-a", "lane": "economy"},
            {"deployment_id": "dep-b", "lane": "quality"},
        ],
    }
    inventory = {
        deployment_id: PolicyMemberInventoryItem(
            deployment_id,
            enabled=True,
            workload_mode="chat",
        )
        for deployment_id in ("dep-a", "dep-b")
    }

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        validate_stored_route_policy(
            payload,
            available_members=inventory,
            workload_mode="chat",
        )


def test_v3_write_does_not_promote_legacy_selector_shaped_opaque_fields():
    merged = merge_policy_document_for_write(
        {
            "strategy": "weighted",
            "selector": {"kind": "future-selector", "opaque": True},
            "members": [
                {
                    "deployment_id": "dep-a",
                    "lane": "legacy-opaque-lane",
                    "server_assignment": "stable",
                }
            ],
        },
        {
            "strategy": "least-busy",
            "members": [{"deployment_id": "dep-a", "enabled": True}],
        },
        existing_semantics_version=2,
    )

    assert merged == {
        "strategy": "least-busy",
        "members": [
            {
                "deployment_id": "dep-a",
                "enabled": True,
                "server_assignment": "stable",
            }
        ],
    }
