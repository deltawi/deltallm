"""Publish native scaling pressure from the two existing bounded observations."""

from src.billing.accounting_health import AccountingBacklogProbe
from src.billing.accounting_read_model_health import ReadModelHealth
from src.billing.accounting_recovery import RecoveryObserver
from src.metrics.accounting_read_models import (
    observe_native_work_age,
    unavailable_native_work_observation,
)
from src.telemetry.lifecycle import WorkerState


class NativeProjectionObservation:
    def __init__(
        self,
        presence: RecoveryObserver,
        backlog: AccountingBacklogProbe,
        reporting: ReadModelHealth,
    ) -> None:
        if presence.generation != reporting.generation:
            raise ValueError("native observations must share a generation")
        self.generation = presence.generation
        self._presence = presence
        self._backlog = backlog
        self._reporting = reporting

    async def observe(self, *, expires_at: float) -> None:
        self._observe_pressure()
        await self._presence.observe(expires_at=expires_at)

    def _observe_pressure(self) -> None:
        terminal, report = self._backlog.snapshot, self._reporting.progress
        terminal_health, report_health = (
            self._backlog.worker_health,
            self._reporting.worker_health,
        )
        terminal_known = terminal_health.state is WorkerState.READY or terminal_health.detail in {
            "terminal_age_limit",
            "terminal_backlog_limit",
            "terminal_capacity",
        }
        report_known = (
            report_health.state is WorkerState.READY
            or report_health.detail == "read_model_age_limit"
        )
        if (
            not terminal_known
            or not report_known
            or terminal is None
            or report is None
            or terminal.generation != self.generation
            or report.generation != self.generation
        ):
            unavailable_native_work_observation()
            return
        observe_native_work_age(
            terminal_seconds=terminal.oldest_age_seconds,
            report_seconds=report.oldest_head_age_seconds,
        )
