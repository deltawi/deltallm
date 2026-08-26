from __future__ import annotations

import pytest

from src.db.organization_deletion_cleanup_repository import (
    CleanupPageResult,
    OrganizationDeletionCleanupRepository,
)


class _ScopeCleanup:
    def __init__(self, available: int) -> None:
        self.available = available
        self.limits: list[int] = []

    async def reject_pending_approvals(
        self,
        organization_id: str,
        *,
        page_size: int,
    ) -> int:
        del organization_id
        self.limits.append(page_size)
        processed = min(self.available, page_size)
        self.available -= processed
        return processed


class _TenantCleanup:
    def __init__(self, available: int) -> None:
        self.available = available
        self.limits: list[int] = []

    async def expire_create_sessions(self, organization_id: str, *, limit: int) -> int:
        del organization_id
        self.limits.append(limit)
        processed = min(self.available, limit)
        self.available -= processed
        return processed

    async def cancel_webhook_deliveries(self, organization_id: str, *, limit: int) -> int:
        del organization_id
        self.limits.append(limit)
        processed = min(self.available, limit)
        self.available -= processed
        return processed

    async def remove_tenant_state_page(
        self,
        organization_id: str,
        *,
        deletion_job_id: str,
        page_size: int,
    ) -> CleanupPageResult:
        del organization_id, deletion_job_id
        self.limits.append(page_size)
        processed = min(self.available, page_size)
        self.available -= processed
        return CleanupPageResult(processed, processed >= page_size)


class _InvitationCleanup:
    def __init__(self, available: int) -> None:
        self.available = available
        self.limits: list[int] = []

    async def clean_page(self, organization_id: str, *, page_size: int) -> int:
        del organization_id
        self.limits.append(page_size)
        processed = min(self.available, page_size)
        self.available -= processed
        return processed


@pytest.mark.asyncio
async def test_cancel_pending_uses_one_total_page_budget() -> None:
    repository = OrganizationDeletionCleanupRepository()
    scope = _ScopeCleanup(available=3)
    tenant = _TenantCleanup(available=3)
    invitations = _InvitationCleanup(available=3)
    repository.scope_cleanup = scope  # type: ignore[assignment]
    repository.tenant_cleanup = tenant  # type: ignore[assignment]
    repository.invitation_cleanup = invitations  # type: ignore[assignment]

    result = await repository.cancel_pending_page("org-1", page_size=5)

    assert result == CleanupPageResult(processed=5, remaining=True)
    assert scope.limits == [5]
    assert tenant.limits == [2]
    assert invitations.limits == []


@pytest.mark.asyncio
async def test_remove_tenant_state_passes_only_remaining_budget() -> None:
    repository = OrganizationDeletionCleanupRepository()
    invitations = _InvitationCleanup(available=2)
    tenant = _TenantCleanup(available=10)
    repository.invitation_cleanup = invitations  # type: ignore[assignment]
    repository.tenant_cleanup = tenant  # type: ignore[assignment]

    result = await repository.remove_tenant_state_page(
        "org-1",
        deletion_job_id="job-1",
        page_size=5,
    )

    assert result == CleanupPageResult(processed=5, remaining=True)
    assert invitations.limits == [5]
    assert tenant.limits == [3]
