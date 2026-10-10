from __future__ import annotations

from src.db.catalog.managed_assets import (
    ManagedAssetLinkHealth,
    ManagedAssetReconciliationResult,
)
from src.services.access.managed_asset_reconciliation import ManagedAssetReconciliationService


class _Repository:
    def __init__(self, reports: list[ManagedAssetLinkHealth]) -> None:
        self.reports = list(reports)
        self.reconcile_calls = 0

    async def get_link_health(self) -> ManagedAssetLinkHealth:
        if len(self.reports) > 1:
            return self.reports.pop(0)
        return self.reports[0]

    async def reconcile_missing_links(
        self,
        *,
        batch_size: int,
    ) -> ManagedAssetReconciliationResult:
        assert batch_size == 10
        self.reconcile_calls += 1
        return ManagedAssetReconciliationResult(named_credentials_linked=2)


async def test_reconciliation_drains_missing_links_before_reporting_ready() -> None:
    repository = _Repository(
        [
            ManagedAssetLinkHealth(missing_named_credentials=2),
            ManagedAssetLinkHealth(),
        ]
    )
    service = ManagedAssetReconciliationService(
        repository,
        batch_size=10,
    )

    health = await service.reconcile_now()

    assert health.ready is True
    assert health.state == "ready"
    assert health.last_repaired == 2
    assert repository.reconcile_calls == 1


async def test_reconciliation_reports_structural_mismatch_as_degraded() -> None:
    repository = _Repository([ManagedAssetLinkHealth(kind_mismatches=1, orphaned_policies=2)])
    service = ManagedAssetReconciliationService(repository)

    health = await service.reconcile_now()

    assert health.ready is False
    assert health.state == "degraded"
    assert health.kind_mismatches == 1
    assert health.orphaned_policies == 2
    assert health.detail == (
        "1 link type mismatches require review; 2 orphaned policies require review"
    )


async def test_orphaned_policy_is_observable_but_not_readiness_fatal() -> None:
    repository = _Repository([ManagedAssetLinkHealth(orphaned_policies=1)])
    service = ManagedAssetReconciliationService(repository)

    health = await service.reconcile_now()

    assert health.ready is True
    assert health.state == "ready"
    assert health.detail == "1 orphaned policies require review"


async def test_reconciliation_failure_is_fail_closed_in_health() -> None:
    class _FailingRepository:
        async def get_link_health(self) -> ManagedAssetLinkHealth:
            raise RuntimeError("database unavailable")

    service = ManagedAssetReconciliationService(_FailingRepository())

    health = await service.reconcile_now()

    assert health.ready is False
    assert health.state == "failed"
    assert health.detail == "database unavailable"
