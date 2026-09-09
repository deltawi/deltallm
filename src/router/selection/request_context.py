from __future__ import annotations

from src.router.candidates import candidate_plan_cache, invalidate_candidate_plan_cache
from src.router.selection.contracts import SelectorDecision, SelectorInvariantError
from src.router.selection.request_state import RequestSelectorState

_SELECTOR_STATE_KEY = "_deltallm_selector_state"


def set_request_selector_state(context: dict[str, object], state: RequestSelectorState) -> None:
    """The execution edge attaches one owner, outside caller-controlled metadata."""
    existing = context.get(_SELECTOR_STATE_KEY)
    if existing is state:
        return
    if existing is not None:
        raise SelectorInvariantError()
    context[_SELECTOR_STATE_KEY] = state
    invalidate_candidate_plan_cache(context)


def refresh_selector_candidate_plans(context: dict[str, object]) -> SelectorDecision | None:
    """Discard pre-decision lane plans, but never discard or rerun the decision."""
    state = context.get(_SELECTOR_STATE_KEY)
    if state is None:
        return None
    if not isinstance(state, RequestSelectorState):
        raise SelectorInvariantError()
    decision = state.decision_for_planning()
    minimum_rank = decision.minimum_rank if decision is not None else None
    cached = candidate_plan_cache(context)
    stale = [
        group for group, plan in cached.items() if plan.lanes and plan.minimum_rank != minimum_rank
    ]
    for group in stale:
        del cached[group]
    return decision
