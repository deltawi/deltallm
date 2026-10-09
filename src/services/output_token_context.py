"""Own output accounting across an admitted request's sequential model attempts."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from uuid import uuid4

import anyio

from src.metrics.output_tpm import record_output_tpm
from src.services.output_limit_types import OutputAccountingEvent, OutputSnapshot
from src.services.output_limit_redis import OUTPUT_COORDINATION_TIMEOUT_SECONDS
from src.services.rate_limit_lease import RateLimitState

if TYPE_CHECKING:
    from src.services.limit_counter import LimitCounter

logger = logging.getLogger(__name__)


class OutputTokenContext:
    def __init__(
        self, limiter: LimitCounter, snapshot: OutputSnapshot, state: RateLimitState
    ) -> None:
        self.limiter = limiter
        self.policy = snapshot.policy
        self.state = state
        self.reported_output: int | None = None
        self.event_id: str | None = None
        self.dispatched = False
        self.closed = False

    async def begin(self) -> None:
        if self.closed:
            self.closed = False
            self.dispatched = False
            self.reported_output = None
            self.event_id = None
        elif self.dispatched:
            raise RuntimeError("Output attempt is already active")

    def observe_output(self, actual: int | None) -> None:
        self.reported_output = actual

    def mark_dispatched(self) -> None:
        if self.closed or self.dispatched:
            raise RuntimeError("Output attempt cannot be dispatched twice")
        self.event_id = uuid4().hex
        self.dispatched = True

    async def finish(self, actual: int | None = None) -> None:
        if self.closed:
            return
        # Close before I/O. Accounting failure must never replay upstream work.
        self.closed = True
        if actual is None:
            actual = self.reported_output if self.dispatched else 0
        if actual == 0:
            return
        if self.event_id is None:
            raise RuntimeError("Output accounting has no dispatched attempt")
        if actual is None:
            record_output_tpm("unknown_usage")
        try:
            # Disconnect uses level cancellation. Keep this request-owned cleanup
            # alive for at most the existing Redis coordination budget.
            with anyio.move_on_after(OUTPUT_COORDINATION_TIMEOUT_SECONDS, shield=True) as cleanup:
                snapshot = await self.limiter.account_output(
                    OutputAccountingEvent(self.policy, self.event_id, actual)
                )
            if cleanup.cancel_called:
                self._accounting_failed()
                return
        except asyncio.CancelledError:
            # Direct Task.cancel() can interrupt even a shielded AnyIO scope.
            # Allow the outer finalizer to retry the same idempotent event.
            self.reported_output = actual
            self.closed = False
            self._accounting_failed()
            raise
        except Exception:
            self._accounting_failed()
            return
        record_output_tpm("accounted")
        if self.state.warning == "output_tpm_accounting_failed":
            self.state.warning = None
        self._update_state(snapshot)

    def _accounting_failed(self) -> None:
        self.state.output_tpm_remaining = None
        self.state.warning = "output_tpm_accounting_failed"
        record_output_tpm("accounting_failed")
        logger.warning("output_tpm_accounting_unavailable")

    def _update_state(self, snapshot: OutputSnapshot) -> None:
        candidates = list(
            zip(snapshot.policy.scopes, snapshot.current_values, snapshot.unknown, strict=True)
        )
        scope, value, unknown = next(
            (candidate for candidate in candidates if candidate[2]),
            max(candidates, key=lambda candidate: candidate[1] / candidate[0].limit),
        )
        self.state.output_tpm_limit = scope.limit
        self.state.output_tpm_remaining = None if unknown else max(0, scope.limit - value)
        self.state.output_tpm_reset = snapshot.reset_at
        self.state.output_tpm_scope = scope.scope
