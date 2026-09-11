from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from src.route_policy_contract import (
    LLMTierSelectorPolicy,
    RoutePolicyMember,
    SELECTOR_POLICY_SEMANTICS_VERSION,
    validate_selector_assignments,
)


@dataclass(frozen=True, slots=True)
class SelectorLaneRouting:
    """Immutable projection of the canonical authored policy, not a second config."""

    policy: LLMTierSelectorPolicy
    members: tuple[RoutePolicyMember, ...]

    def __post_init__(self) -> None:
        validate_selector_assignments(self.policy, self.members, group_mode="chat")


def parse_selector_lane_routing(group: Mapping[str, object]) -> SelectorLaneRouting | None:
    selector = group.get("selector")
    if selector is None:
        return None
    version = group.get("policy_semantics_version")
    semantics = (
        int(version) if isinstance(version, (int, str)) else SELECTOR_POLICY_SEMANTICS_VERSION
    )
    if semantics < SELECTOR_POLICY_SEMANTICS_VERSION:
        # Historical opaque fields must not acquire current selector semantics.
        return None
    if group.get("mode") != "chat":
        raise ValueError("selector requires route group mode 'chat'")
    members = group.get("members")
    if not isinstance(members, (list, tuple)):
        raise ValueError("selector requires route group members")
    return SelectorLaneRouting(
        policy=LLMTierSelectorPolicy.model_validate(selector),
        members=tuple(RoutePolicyMember.model_validate(member) for member in members),
    )
