from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from src.router.candidates import RouteCandidatePlanner
from src.router.execution import RequestDeadline
from src.router.selection.contracts import SelectorInvariantError

if TYPE_CHECKING:
    from src.router.router import Deployment

_PREPARATION_KEY = "_deltallm_group_preparation"


class GroupPreparation(Protocol):
    @property
    def deadline(self) -> RequestDeadline: ...

    async def prepare(self, model_group: str) -> None: ...


@dataclass(frozen=True, slots=True)
class DeferredRouteGroup:
    model_group: str


@dataclass(frozen=True, slots=True)
class _PreparationBinding:
    owner: GroupPreparation


def set_group_preparation(context: dict[str, object], owner: GroupPreparation) -> None:
    binding = context.get(_PREPARATION_KEY)
    if binding is not None and (
        not isinstance(binding, _PreparationBinding) or binding.owner is not owner
    ):
        raise SelectorInvariantError()
    context[_PREPARATION_KEY] = _PreparationBinding(owner)


async def prepare_group(context: dict[str, object], group: str) -> None:
    binding = context.get(_PREPARATION_KEY)
    if binding is not None:
        if not isinstance(binding, _PreparationBinding):
            raise SelectorInvariantError()
        # This is a server-only root context field, never caller metadata.
        await binding.owner.prepare(group)


def group_preparation_deadline(context: dict[str, object]) -> RequestDeadline | None:
    binding = context.get(_PREPARATION_KEY)
    if binding is None:
        return None
    if not isinstance(binding, _PreparationBinding):
        raise SelectorInvariantError()
    return binding.owner.deadline


async def prepared_deployments(
    chain: Sequence[Deployment | DeferredRouteGroup],
    *,
    planner: RouteCandidatePlanner,
    context: dict[str, object],
) -> AsyncIterator[tuple[int, Deployment]]:
    """Resolve deferred groups only as execution reaches them, without recursion.

    Failover retains attempt, retry and visited-deployment ownership. The planner
    stays pure with respect to providers; only the injected application owner may
    prepare a decision. Selector-free chains perform no additional dependency work.
    """
    seen_groups: set[str] = set()
    index = 0
    for entry in chain:
        if isinstance(entry, DeferredRouteGroup):
            if entry.model_group in seen_groups:
                continue
            seen_groups.add(entry.model_group)
            await prepare_group(context, entry.model_group)
            deadline = group_preparation_deadline(context)
            planning = planner.plan_deployments([entry.model_group], context)
            plans = await deadline.wait_for(planning) if deadline is not None else await planning
            plan = plans.get(entry.model_group)
            if plan is not None:
                if plan.minimum_rank is None:
                    if any(lane.deployments for lane in plan.lanes):
                        raise SelectorInvariantError()
                    continue
                for deployment in plan.deployments:
                    yield index, deployment
                    index += 1
        else:
            yield index, entry
            index += 1
