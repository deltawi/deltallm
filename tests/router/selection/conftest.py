import pytest

from src.route_policy_contract import LLMTierSelectorPolicy, SelectorLane
from src.router.selection.contracts import SelectorPolicyIdentity


@pytest.fixture
def selector_policy() -> LLMTierSelectorPolicy:
    return LLMTierSelectorPolicy(
        kind="llm-tier",
        classifier_deployment_id="classifier-concrete",
        lanes=(
            SelectorLane(id="economy", rank=0, description="Routine work"),
            SelectorLane(id="quality", rank=1, description="Complex work"),
        ),
    )


@pytest.fixture
def policy_identity() -> SelectorPolicyIdentity:
    return SelectorPolicyIdentity(fingerprint="route-policy-v1:" + "a" * 64, policy_version=7)
