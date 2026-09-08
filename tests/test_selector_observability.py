from unittest.mock import Mock

import pytest

from src.metrics import selector
from src.router.selection.contracts import (
    SelectorCause,
    SelectorDecision,
    SelectorPolicyIdentity,
    UnknownSelectorUsage,
)
from src.telemetry.selector_decision import ProtectedSelectorDecision


def decision():
    return SelectorDecision(
        lane="economy",
        minimum_rank=0,
        cause=SelectorCause.CLASSIFIED,
        latency_ms=12.0,
        policy_identity=SelectorPolicyIdentity(fingerprint="route-policy-v1:" + "a" * 64),
        usage=UnknownSelectorUsage(),
    )


def test_labels_are_fixed_enum_causes_and_bounded_ranks_only(monkeypatch):
    metric = Mock()
    monkeypatch.setattr(selector, "decisions", metric)
    selector.observe_selector_decision(decision())
    metric.labels.assert_called_once_with(cause="classified", rank="0")
    with pytest.raises(ValueError):
        selector.observe_selector_termination("secret/provider/url", seconds=1)
    with pytest.raises(ValueError):
        selector.observe_selector_answer(rank=100000, selector_seconds=1, streaming=True)


def test_protected_decision_is_allowlisted_and_has_no_content_usage_or_topology():
    payload = ProtectedSelectorDecision.from_decision(decision()).model_dump(mode="json")
    assert set(payload) == {
        "kind",
        "lane",
        "minimum_rank",
        "cause",
        "latency_ms",
        "used_default",
        "policy_identity",
    }
    assert payload["lane"] == "economy"
    assert not payload["used_default"]
    assert "usage" not in payload and "deployment_id" not in payload
