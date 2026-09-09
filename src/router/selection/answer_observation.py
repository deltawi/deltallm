from __future__ import annotations

from collections.abc import Mapping

from src.metrics.selector import (
    SelectorEscalation,
    observe_selector_answer,
    observe_selector_escalation,
)
from src.router.selection.contracts import SelectorDecision
from src.router.selection.qualification import QualifiedSelector


class SelectorAnswerObservation:
    """One terminal observation per external operation, not per MCP/provider hop."""

    def __init__(self, selectors: Mapping[str, QualifiedSelector]) -> None:
        self._selectors = selectors
        self.decision: SelectorDecision | None = None
        self._lowest_attempt: int | None = None
        self._finished = False

    def _rank(self, group: str, deployment_id: str) -> int | None:
        qualified = self._selectors.get(group)
        if qualified is None:
            return None
        lane = next(
            (m.lane for m in qualified.routing.members if m.deployment_id == deployment_id), None
        )
        return next((item.rank for item in qualified.routing.policy.lanes if item.id == lane), None)

    def attempted(self, group: str, deployment_id: str) -> None:
        rank = self._rank(group, deployment_id)
        if rank is not None:
            self._lowest_attempt = (
                rank if self._lowest_attempt is None else min(rank, self._lowest_attempt)
            )

    def answered(self, group: str, deployment_id: str, *, streaming: bool) -> None:
        decision = self.decision
        rank = self._rank(group, deployment_id)
        if self._finished or decision is None or rank is None:
            return
        self._finished = True
        observe_selector_answer(
            rank=rank, selector_seconds=decision.latency_ms / 1000, streaming=streaming
        )
        if rank > decision.minimum_rank:
            cause = (
                SelectorEscalation.PROVIDER_FAILURE
                if self._lowest_attempt is not None and self._lowest_attempt < rank
                else SelectorEscalation.NO_ELIGIBLE_MEMBER
            )
            observe_selector_escalation(cause, rank=rank)
