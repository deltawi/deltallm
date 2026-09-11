from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

from src.providers.chat_upstream import ChatGenerationProfile
from src.router.selection.contracts import (
    SelectorCause,
    SelectorDecision,
    SelectorHopFailure,
    SelectorHopSuccess,
    SelectorRequestFeatures,
    SelectorSnippet,
    SelectorPolicyIdentity,
    UnknownSelectorUsage,
)
from src.router.selection.provider import ConcreteSelectorTarget, _bounded_timeout
import httpx


def test_nested_contracts_are_frozen_and_repr_omits_content(policy_identity):
    features = SelectorRequestFeatures(
        newest_user="private prompt",
        token_estimate=1,
        tool_count=0,
        recent_context=(SelectorSnippet(role="user", text="private context"),),
        modalities=frozenset({"text"}),
    )
    decision = SelectorDecision(
        lane="quality",
        minimum_rank=1,
        cause=SelectorCause.CLASSIFIED,
        latency_ms=0,
        policy_identity=policy_identity,
        usage=UnknownSelectorUsage(),
    )
    for value, attribute, replacement in (
        (features, "newest_user", "new"),
        (features.recent_context[0], "text", "new"),
        (decision, "lane", "economy"),
        (decision.policy_identity, "policy_version", 8),
        (decision.usage, "kind", "reported"),
    ):
        with pytest.raises(ValidationError):
            setattr(value, attribute, replacement)
    assert "private" not in repr(features)
    assert "private" not in repr(
        SelectorHopSuccess(text="private completion", usage=UnknownSelectorUsage())
    )
    with pytest.raises(ValidationError):
        SelectorHopFailure(cause=SelectorCause.CLASSIFIED, usage=UnknownSelectorUsage())


@pytest.mark.parametrize("value", [True, "1", -1, 2**63])
def test_counts_are_strict_and_bounded(value):
    with pytest.raises(ValidationError):
        SelectorRequestFeatures(token_estimate=value, tool_count=0)


def test_concrete_target_copies_only_bounded_scalars_and_hides_secrets():
    params = {
        "model": "openai/small",
        "api_key": "private-key",
        "default_params": {"tools": ["private"]},
    }
    target = ConcreteSelectorTarget.from_config("concrete", params)
    params["model"] = "changed"
    assert dict(target.parameters) == {"model": "openai/small", "api_key": "private-key"}
    assert "private" not in repr(target)
    with pytest.raises(FrozenInstanceError):
        target.deployment_id = "changed"


def test_generation_proof_is_not_a_coerced_boolean():
    with pytest.raises(ValueError):
        ChatGenerationProfile(zero_temperature=True)


def test_phase_timeouts_respect_all_three_limits():
    timeout = _bounded_timeout(
        httpx.Timeout(connect=1, read=2, write=3, pool=0.5), deployment=50, remaining=4
    )
    assert timeout.as_dict() == {"connect": 1, "read": 2, "write": 3, "pool": 0.5}
    assert _bounded_timeout(
        httpx.Timeout(None), deployment=None, remaining=0.1
    ).as_dict() == dict.fromkeys(timeout.as_dict(), 0.1)


def test_file_policy_identity_has_no_invented_database_revision():
    identity = SelectorPolicyIdentity(fingerprint="route-policy-v1:" + "b" * 64)
    assert identity.policy_version is None and identity.prompt_version == 1


@pytest.mark.parametrize("latency", [-1, float("inf"), float("nan")])
def test_decision_latency_must_be_finite_and_nonnegative(policy_identity, latency):
    with pytest.raises(ValidationError):
        SelectorDecision(
            lane="quality",
            minimum_rank=1,
            cause=SelectorCause.CLASSIFIED,
            latency_ms=latency,
            policy_identity=policy_identity,
            usage=UnknownSelectorUsage(),
        )
