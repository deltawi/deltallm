from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from typing import Protocol

from src.db.catalog.managed_assets import (
    ManagedAssetLinkHealth,
    ManagedAssetReconciliationResult,
)

logger = logging.getLogger(__name__)


class ManagedAssetReconciliationRepository(Protocol):
    async def get_link_health(self) -> ManagedAssetLinkHealth: ...

    async def reconcile_missing_links(
        self,
        *,
        batch_size: int,
    ) -> ManagedAssetReconciliationResult: ...


@dataclass(frozen=True, slots=True)
class ManagedAssetReconciliationHealth:
    state: str
    ready: bool
    missing_links: int = 0
    kind_mismatches: int = 0
    orphaned_policies: int = 0
    last_repaired: int = 0
    last_checked_at: datetime | None = None
    detail: str | None = None


class ManagedAssetReconciliationService:
    """Continuously closes nullable-link gaps during an expand/contract rollout."""

    def __init__(
        self,
        repository: ManagedAssetReconciliationRepository,
        *,
        interval_seconds: float = 30.0,
        batch_size: int = 250,
        max_batches_per_run: int = 20,
    ) -> None:
        self.repository = repository
        self.interval_seconds = max(1.0, float(interval_seconds))
        self.batch_size = max(1, min(int(batch_size), 10_000))
        self.max_batches_per_run = max(1, min(int(max_batches_per_run), 1_000))
        self._health = ManagedAssetReconciliationHealth(
            state="starting",
            ready=False,
            detail="initial managed-asset link check has not completed",
        )
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._stopping = False

    def health_snapshot(self) -> ManagedAssetReconciliationHealth:
        return self._health

    async def start(self) -> None:
        if self._task is not None:
            return
        await self.reconcile_now()
        self._task = asyncio.create_task(
            self._run(),
            name="managed-asset-reconciliation",
        )

    async def close(self) -> None:
        self._stopping = True
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def reconcile_now(self) -> ManagedAssetReconciliationHealth:
        async with self._lock:
            repaired = 0
            try:
                report = await self.repository.get_link_health()
                for _ in range(self.max_batches_per_run):
                    if report.missing_links == 0:
                        break
                    result = await self.repository.reconcile_missing_links(
                        batch_size=self.batch_size
                    )
                    repaired += result.total_changes
                    report = await self.repository.get_link_health()
                    if result.total_changes == 0:
                        break
                self._health = self._health_from_report(report, repaired=repaired)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("managed-asset link reconciliation failed")
                self._health = ManagedAssetReconciliationHealth(
                    state="failed",
                    ready=False,
                    last_repaired=repaired,
                    last_checked_at=datetime.now(UTC),
                    detail=str(exc),
                )
            return self._health

    async def _run(self) -> None:
        while not self._stopping:
            try:
                await asyncio.sleep(self.interval_seconds)
                await self.reconcile_now()
            except asyncio.CancelledError:
                raise

    @staticmethod
    def _health_from_report(
        report: ManagedAssetLinkHealth,
        *,
        repaired: int,
    ) -> ManagedAssetReconciliationHealth:
        details: list[str] = []
        if report.missing_links:
            details.append(f"{report.missing_links} missing links remain")
        if report.kind_mismatches:
            details.append(f"{report.kind_mismatches} link type mismatches require review")
        if report.orphaned_policies:
            # Orphans do not grant access to a live resource and are therefore observable
            # cleanup debt, not a reason to make the serving process unavailable.
            details.append(f"{report.orphaned_policies} orphaned policies require review")
        return ManagedAssetReconciliationHealth(
            state="ready" if report.ready else "degraded",
            ready=report.ready,
            missing_links=report.missing_links,
            kind_mismatches=report.kind_mismatches,
            orphaned_policies=report.orphaned_policies,
            last_repaired=repaired,
            last_checked_at=datetime.now(UTC),
            detail="; ".join(details) or None,
        )
