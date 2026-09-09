from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, cast

from src.router.candidates import RouteCandidateLane
from src.router.context_policy import (
    ContextRoutingPolicy,
    RequestTokenDemand,
    order_context_candidates,
)
from src.router.selection.contracts import SelectorDecision, SelectorInvariantError
from src.router.selection.lanes import SelectorLaneRouting
from src.router.strategies import RoutingStrategyImpl, StrategyStateSnapshot

if TYPE_CHECKING:
    from src.router.router import Deployment


@dataclass(frozen=True, slots=True)
class CandidateOrdering:
    deployments: tuple[Deployment, ...]
    lanes: tuple[RouteCandidateLane, ...] = ()
    minimum_rank: int | None = None
    rejection_reason: str | None = None


def selector_context_policy(
    inherited: ContextRoutingPolicy | None, own: ContextRoutingPolicy | None
) -> ContextRoutingPolicy | None:
    """A selector fallback cannot weaken either group's deterministic fit limits."""
    if inherited is None or own is None:
        return own or inherited
    return replace(
        inherited,
        unknown_capacity="exclude",
        default_output_tokens=max(inherited.default_output_tokens, own.default_output_tokens),
        safety_margin_tokens=max(inherited.safety_margin_tokens, own.safety_margin_tokens),
    )


async def order_candidates(
    *,
    eligible: list[Deployment],
    strategy: RoutingStrategyImpl,
    context: dict[str, object],
    snapshot: StrategyStateSnapshot,
    demand: RequestTokenDemand | None,
    context_policy: ContextRoutingPolicy | None,
    selector: SelectorLaneRouting | None,
    decision: SelectorDecision | None,
) -> CandidateOrdering:
    """Order only hard-filtered candidates; this boundary never executes a classifier.

    Stateful strategies use the router's one batched snapshot for every lane, so
    partitioning adds no SQL/Redis/provider work. Without a selector the original
    strategy-then-context order is unchanged, including random-choice semantics.
    """
    if selector is None:
        ordered = await strategy.order(eligible, context, snapshot)
        return CandidateOrdering(
            tuple(
                order_context_candidates(cast("list[Deployment]", ordered), demand, context_policy)
            )
        )

    members = {member.deployment_id: member for member in selector.members}
    partition: dict[str, list[Deployment]] = {lane.id: [] for lane in selector.policy.lanes}
    for deployment in eligible:
        member = members.get(deployment.deployment_id)
        if member is None:
            raise SelectorInvariantError()
        if member.enabled:
            if member.lane not in partition:
                raise SelectorInvariantError()
            partition[member.lane].append(deployment)

    minimum_rank = decision.minimum_rank if decision is not None else None
    lanes: list[RouteCandidateLane] = []
    for lane in selector.policy.lanes:
        candidates = partition[lane.id]
        if minimum_rank is not None:
            candidates = candidates if lane.rank >= minimum_rank else []
            if candidates:
                ordered = await strategy.order(candidates, context, snapshot)
                candidates = order_context_candidates(
                    cast("list[Deployment]", ordered), demand, context_policy
                )
        lanes.append(RouteCandidateLane(lane.id, lane.rank, tuple(candidates)))
    if decision is None:
        # A pending plan carries eligibility, never executable answer candidates.
        return CandidateOrdering((), tuple(lanes), rejection_reason="selector_decision_required")
    deployments = tuple(deployment for lane in lanes for deployment in lane.deployments)
    return CandidateOrdering(
        deployments,
        tuple(lanes),
        minimum_rank,
        None if deployments else "no_eligible_selector_lane",
    )
