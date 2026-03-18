from __future__ import annotations

import pytest

from src.router.policy_validation import validate_route_policy


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
    assert warnings == ["Weighted mode is advisory when strategy is set explicitly; strategy takes precedence."]


def test_validate_route_policy_rejects_unknown_fields():
    with pytest.raises(ValueError, match="unknown policy fields"):
        validate_route_policy({"strategy": "weighted", "unknown": True})


def test_validate_route_policy_validates_retry_and_timeouts_schema():
    normalized, warnings = validate_route_policy(
        {
            "strategy": "least-busy",
            "timeouts": {"global_ms": 1200},
            "retry": {"max_attempts": 2, "retryable_error_classes": ["timeout", "rate_limit"]},
            "members": [{"deployment_id": "dep-a", "enabled": True}],
        },
        available_member_ids={"dep-a"},
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
        available_member_ids={"dep-a", "dep-b"},
    )

    assert warnings == []
    assert normalized["strategy"] == "priority-based-routing"
    assert normalized["members"][0]["priority"] == 0
    assert normalized["members"][1]["priority"] == 1


def test_validate_route_policy_rejects_unsupported_modes():
    with pytest.raises(ValueError, match="mode 'adaptive' is not supported"):
        validate_route_policy({"mode": "adaptive", "members": [{"deployment_id": "dep-a"}]})


def test_validate_route_policy_rejects_runtime_unsupported_fields():
    with pytest.raises(ValueError, match="unknown policy fields"):
        validate_route_policy({"strategy": "weighted", "conditions": []})


def test_validate_route_policy_rejects_unknown_member_reference():
    with pytest.raises(ValueError, match="unknown members"):
        validate_route_policy(
            {"strategy": "weighted", "members": [{"deployment_id": "dep-b", "weight": 1}]},
            available_member_ids={"dep-a"},
        )
