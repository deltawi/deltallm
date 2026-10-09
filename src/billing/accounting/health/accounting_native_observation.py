"""Check two bounded native observations under the monitor's one deadline."""

from __future__ import annotations

from typing import Protocol

from src.billing.accounting.health.accounting_health import AccountingBacklogProbe
from src.billing.accounting.health.accounting_presence import ProjectionPresence
from src.db.accounting_permit_results import invalid_result
from src.telemetry.lifecycle import WorkerState


class PresenceObservation(Protocol):
    async def snapshot(self, *, generation: int, expires_at: float) -> ProjectionPresence: ...


class NativeAccountingObservation:
    def __init__(
        self,
        probe: AccountingBacklogProbe,
        presence: PresenceObservation,
        *,
        generation: int,
    ) -> None:
        if type(generation) is not int or not 1 <= generation <= 2**63 - 1:
            raise ValueError("accounting observation generation is invalid")
        self._probe = probe
        self._presence = presence
        self._generation = generation

    async def observe_ready(self, *, expires_at: float) -> bool:
        if not await self._probe.refresh(expires_at=expires_at):
            return False
        value = await self._presence.snapshot(generation=self._generation, expires_at=expires_at)
        if type(value) is not ProjectionPresence:
            raise invalid_result()
        try:
            snapshot = ProjectionPresence(
                generation=value.generation,
                present_slots=value.present_slots,
                ready_slots=value.ready_slots,
            )
        except ValueError:
            raise invalid_result() from None
        if snapshot.generation != self._generation:
            raise invalid_result()
        backlog = self._probe.snapshot
        return (
            snapshot.ready_slots > 0
            and backlog is not None
            and backlog.generation == self._generation
            and backlog.protocol_state == "active"
            and self._probe.worker_health.state is WorkerState.READY
        )
