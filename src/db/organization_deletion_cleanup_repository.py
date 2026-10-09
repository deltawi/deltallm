from __future__ import annotations

from typing import Any

from src.db.organization_deletion_asset_cleanup import OrganizationDeletionAssetCleanup
from src.db.organization_deletion_cleanup_types import CleanupPageResult
from src.db.organization_deletion_invitation_cleanup import (
    OrganizationDeletionInvitationCleanup,
)
from src.db.organization_deletion_scope_cleanup import OrganizationDeletionScopeCleanup
from src.db.organization_deletion_tenant_cleanup import OrganizationDeletionTenantCleanup


class OrganizationDeletionCleanupRepository:
    """Coordinates specialized cleanup modules under one transaction row budget."""

    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client
        self.invitation_cleanup = (
            OrganizationDeletionInvitationCleanup(prisma_client)
            if prisma_client is not None
            else None
        )
        self.scope_cleanup = (
            OrganizationDeletionScopeCleanup(prisma_client) if prisma_client is not None else None
        )
        self.asset_cleanup = (
            OrganizationDeletionAssetCleanup(prisma_client) if prisma_client is not None else None
        )
        self.tenant_cleanup = (
            OrganizationDeletionTenantCleanup(prisma_client) if prisma_client is not None else None
        )

    def with_db(self, prisma_client: Any) -> OrganizationDeletionCleanupRepository:
        return OrganizationDeletionCleanupRepository(prisma_client)

    async def cancel_pending_page(
        self,
        organization_id: str,
        *,
        page_size: int,
    ) -> CleanupPageResult:
        scope = self._require_scope_cleanup()
        tenant = self._require_tenant_cleanup()
        processed = await scope.reject_pending_approvals(
            organization_id,
            page_size=page_size,
        )
        if processed < page_size:
            processed += await tenant.expire_create_sessions(
                organization_id,
                limit=page_size - processed,
            )
        if processed < page_size:
            processed += await tenant.cancel_webhook_deliveries(
                organization_id,
                limit=page_size - processed,
            )
        if processed < page_size:
            processed += await self._clean_invitation_page(
                organization_id,
                page_size=page_size - processed,
            )
        return CleanupPageResult(processed=processed, remaining=processed >= page_size)

    async def request_batch_cancellation_page(
        self,
        organization_id: str,
        *,
        page_size: int,
    ) -> CleanupPageResult:
        return await self._require_tenant_cleanup().request_batch_cancellation_page(
            organization_id,
            page_size=page_size,
        )

    async def active_batch_count(self, organization_id: str) -> int:
        return await self._require_tenant_cleanup().active_batch_count(organization_id)

    async def delete_owned_assets_page(
        self,
        organization_id: str,
        *,
        page_size: int,
    ) -> CleanupPageResult:
        return await self._require_asset_cleanup().delete_owned_assets_page(
            organization_id,
            page_size=page_size,
        )

    async def has_owned_assets(self, organization_id: str) -> bool:
        return await self._require_asset_cleanup().has_owned_assets(organization_id)

    async def delete_sensitive_history_page(
        self,
        organization_id: str,
        *,
        page_size: int,
    ) -> CleanupPageResult:
        return await self._require_scope_cleanup().delete_sensitive_history_page(
            organization_id,
            page_size=page_size,
        )

    async def remove_scoped_access_page(
        self,
        organization_id: str,
        *,
        page_size: int,
    ) -> CleanupPageResult:
        return await self._require_scope_cleanup().delete_scoped_access_page(
            organization_id,
            page_size=page_size,
        )

    async def revoke_credentials_page(
        self,
        organization_id: str,
        *,
        page_size: int,
    ) -> CleanupPageResult:
        return await self._require_tenant_cleanup().revoke_credentials_page(
            organization_id,
            page_size=page_size,
        )

    async def remove_tenant_state_page(
        self,
        organization_id: str,
        *,
        deletion_job_id: str,
        page_size: int,
    ) -> CleanupPageResult:
        processed = await self._clean_invitation_page(
            organization_id,
            page_size=page_size,
        )
        if processed < page_size:
            tenant_result = await self._require_tenant_cleanup().remove_tenant_state_page(
                organization_id,
                deletion_job_id=deletion_job_id,
                page_size=page_size - processed,
            )
            processed += tenant_result.processed
        return CleanupPageResult(processed=processed, remaining=processed >= page_size)

    async def has_removable_state(self, organization_id: str) -> bool:
        if await self._require_tenant_cleanup().has_removable_state(organization_id):
            return True
        rows = await self._require_prisma().query_raw(
            """
            SELECT EXISTS (
                SELECT 1 FROM deltallm_platforminvitation i
                WHERE i.status IN ('pending', 'sent') AND (
                    EXISTS (
                        SELECT 1 FROM jsonb_array_elements(
                            COALESCE(i.metadata->'organization_invites', '[]'::jsonb)
                        ) item WHERE item->>'organization_id' = $1
                    ) OR EXISTS (
                        SELECT 1 FROM jsonb_array_elements(
                            COALESCE(i.metadata->'team_invites', '[]'::jsonb)
                        ) item WHERE item->>'organization_id' = $1
                    )
                )
            ) AS has_invitations
            """,
            organization_id,
        )
        return bool(rows and rows[0].get("has_invitations"))

    async def has_sensitive_history(self, organization_id: str) -> bool:
        return await self._require_scope_cleanup().has_sensitive_history(organization_id)

    async def has_ambiguous_sensitive_records(self, organization_id: str) -> bool:
        return await self._require_scope_cleanup().has_ambiguous_sensitive_records(organization_id)

    async def has_scoped_access(self, organization_id: str) -> bool:
        return await self._require_scope_cleanup().has_scoped_access(organization_id)

    async def _clean_invitation_page(self, organization_id: str, *, page_size: int) -> int:
        if self.invitation_cleanup is None:
            raise RuntimeError("organization invitation cleanup is unavailable")
        return await self.invitation_cleanup.clean_page(
            organization_id,
            page_size=page_size,
        )

    def _require_prisma(self) -> Any:
        if self.prisma is None:
            raise RuntimeError("organization deletion cleanup repository is unavailable")
        return self.prisma

    def _require_scope_cleanup(self) -> OrganizationDeletionScopeCleanup:
        if self.scope_cleanup is None:
            raise RuntimeError("organization scope cleanup is unavailable")
        return self.scope_cleanup

    def _require_asset_cleanup(self) -> OrganizationDeletionAssetCleanup:
        if self.asset_cleanup is None:
            raise RuntimeError("organization asset cleanup is unavailable")
        return self.asset_cleanup

    def _require_tenant_cleanup(self) -> OrganizationDeletionTenantCleanup:
        if self.tenant_cleanup is None:
            raise RuntimeError("organization tenant cleanup is unavailable")
        return self.tenant_cleanup


__all__ = ["CleanupPageResult", "OrganizationDeletionCleanupRepository"]
