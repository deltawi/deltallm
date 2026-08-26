from __future__ import annotations

from typing import Any

from src.db.organization_deletion_cleanup_types import CleanupPageResult


class OrganizationDeletionTenantCleanup:
    """Removes tenant-owned rows with one strict row budget per call."""

    def __init__(self, prisma_client: Any) -> None:
        self.prisma = prisma_client

    async def expire_create_sessions(self, organization_id: str, *, limit: int) -> int:
        rows = await self.prisma.query_raw(
            """
            WITH candidates AS (
                SELECT s.session_id
                FROM deltallm_batch_create_session s
                LEFT JOIN deltallm_teamtable t ON t.team_id = s.created_by_team_id
                WHERE COALESCE(s.created_by_organization_id, t.organization_id) = $1
                  AND s.status IN ('staged', 'failed_retryable', 'failed_permanent')
                ORDER BY s.created_at ASC, s.session_id ASC
                LIMIT $2
            )
            UPDATE deltallm_batch_create_session s
            SET status = 'expired', expires_at = NOW(),
                last_error_code = 'organization_deletion_requested',
                last_error_message = 'Organization deletion requested',
                last_attempt_at = NOW()
            FROM candidates c
            WHERE s.session_id = c.session_id
            RETURNING s.session_id
            """,
            organization_id,
            limit,
        )
        return len(rows)

    async def request_batch_cancellation_page(
        self,
        organization_id: str,
        *,
        page_size: int,
    ) -> CleanupPageResult:
        rows = await self.prisma.query_raw(
            """
            WITH candidates AS (
                SELECT j.batch_id
                FROM deltallm_batch_job j
                LEFT JOIN deltallm_teamtable t ON t.team_id = j.created_by_team_id
                WHERE COALESCE(j.created_by_organization_id, t.organization_id) = $1
                  AND j.status IN ('queued', 'in_progress', 'finalizing')
                  AND j.cancel_requested_at IS NULL
                ORDER BY j.created_at ASC, j.batch_id ASC
                LIMIT $2
            )
            UPDATE deltallm_batch_job j
            SET cancel_requested_at = NOW(), status_last_updated_at = NOW()
            FROM candidates c
            WHERE j.batch_id = c.batch_id
            RETURNING j.batch_id
            """,
            organization_id,
            page_size,
        )
        count = len(rows)
        return CleanupPageResult(processed=count, remaining=count >= page_size)

    async def active_batch_count(self, organization_id: str) -> int:
        rows = await self.prisma.query_raw(
            """
            SELECT COUNT(*)::int AS active_batches
            FROM deltallm_batch_job j
            LEFT JOIN deltallm_teamtable t ON t.team_id = j.created_by_team_id
            WHERE COALESCE(j.created_by_organization_id, t.organization_id) = $1
              AND j.status IN ('queued', 'in_progress', 'finalizing')
            """,
            organization_id,
        )
        return int(rows[0].get("active_batches") or 0) if rows else 0

    async def revoke_credentials_page(
        self,
        organization_id: str,
        *,
        page_size: int,
    ) -> CleanupPageResult:
        processed = await self._delete_api_key_scheduler_flows(
            organization_id,
            limit=page_size,
        )
        if processed < page_size:
            processed += await self._delete_keys(
                organization_id,
                limit=page_size - processed,
            )
        if processed < page_size:
            processed += await self._delete_service_accounts(
                organization_id,
                limit=page_size - processed,
            )
        return CleanupPageResult(processed=processed, remaining=processed >= page_size)

    async def remove_tenant_state_page(
        self,
        organization_id: str,
        *,
        deletion_job_id: str,
        page_size: int,
    ) -> CleanupPageResult:
        processed = await self.cancel_webhook_deliveries(
            organization_id,
            limit=page_size,
        )
        if processed < page_size:
            memberships = await self._delete_memberships(
                organization_id,
                page_size=page_size - processed,
            )
            processed += memberships.processed
        if processed < page_size:
            teams = await self._delete_team_state(
                organization_id,
                deletion_job_id=deletion_job_id,
                page_size=page_size - processed,
            )
            processed += teams.processed
        return CleanupPageResult(processed=processed, remaining=processed >= page_size)

    async def has_removable_state(self, organization_id: str) -> bool:
        rows = await self.prisma.query_raw(
            """
            SELECT (
                EXISTS (SELECT 1 FROM deltallm_teamtable WHERE organization_id = $1) OR
                EXISTS (
                    SELECT 1 FROM deltallm_batch_create_session s
                    LEFT JOIN deltallm_teamtable t ON t.team_id = s.created_by_team_id
                    WHERE COALESCE(s.created_by_organization_id, t.organization_id) = $1
                      AND s.status IN ('staged', 'failed_retryable', 'failed_permanent')
                ) OR
                EXISTS (
                    SELECT 1 FROM deltallm_batch_webhook_outbox w
                    WHERE (
                        w.created_by_organization_id = $1
                        OR (
                            w.created_by_organization_id IS NULL
                            AND w.created_by_team_id IN (
                                SELECT team_id FROM deltallm_teamtombstone
                                WHERE organization_id = $1
                            )
                        )
                    ) AND w.status IN ('queued', 'retrying', 'processing')
                ) OR
                EXISTS (
                    SELECT 1 FROM deltallm_teammodelspend counter
                    WHERE counter.team_id IN (
                        SELECT team_id FROM deltallm_teamtable WHERE organization_id = $1
                        UNION
                        SELECT team_id FROM deltallm_teamtombstone WHERE organization_id = $1
                    )
                ) OR
                EXISTS (
                    SELECT 1 FROM deltallm_verificationtoken v
                    LEFT JOIN deltallm_usertable u ON u.user_id = v.user_id
                    LEFT JOIN deltallm_serviceaccount s
                      ON s.service_account_id = v.owner_service_account_id
                    JOIN deltallm_teamtable t
                      ON t.team_id = COALESCE(v.team_id, u.team_id, s.team_id)
                    WHERE t.organization_id = $1
                ) OR
                EXISTS (
                    SELECT 1 FROM deltallm_serviceaccount s
                    JOIN deltallm_teamtable t ON t.team_id = s.team_id
                    WHERE t.organization_id = $1
                ) OR
                EXISTS (
                    SELECT 1 FROM deltallm_organizationmembership
                    WHERE organization_id = $1
                ) OR
                EXISTS (
                    SELECT 1 FROM deltallm_teammembership m
                    JOIN deltallm_teamtable t ON t.team_id = m.team_id
                    WHERE t.organization_id = $1
                )
            ) AS has_state
            """,
            organization_id,
        )
        return bool(rows and rows[0].get("has_state"))

    async def _delete_keys(self, organization_id: str, *, limit: int) -> int:
        rows = await self.prisma.query_raw(
            """
            WITH candidates AS MATERIALIZED (
                SELECT v.id, v.user_id
                FROM deltallm_verificationtoken v
                LEFT JOIN deltallm_usertable u ON u.user_id = v.user_id
                LEFT JOIN deltallm_serviceaccount s
                  ON s.service_account_id = v.owner_service_account_id
                LEFT JOIN deltallm_teamtable t
                  ON t.team_id = COALESCE(v.team_id, u.team_id, s.team_id)
                WHERE t.organization_id = $1
                ORDER BY v.created_at ASC, v.id ASC
                LIMIT $2
            ), principal_tombstones AS (
                INSERT INTO deltallm_organizationprincipaltombstone (
                    principal_id, organization_id, deletion_job_id, recorded_at
                )
                SELECT DISTINCT c.user_id, $1, o.deletion_job_id, NOW()
                FROM candidates c
                JOIN deltallm_organizationtable o ON o.organization_id = $1
                WHERE c.user_id IS NOT NULL AND o.deletion_job_id IS NOT NULL
                ON CONFLICT (principal_id, organization_id) DO NOTHING
            )
            DELETE FROM deltallm_verificationtoken v
            USING candidates c
            WHERE v.id = c.id
            RETURNING v.id
            """,
            organization_id,
            limit,
        )
        return len(rows)

    async def _delete_service_accounts(self, organization_id: str, *, limit: int) -> int:
        return await self._delete_by_id_page(
            table="deltallm_serviceaccount",
            id_column="service_account_id",
            predicate=(
                "team_id IN (SELECT team_id FROM deltallm_teamtable WHERE organization_id = $1)"
            ),
            organization_id=organization_id,
            limit=limit,
        )

    async def cancel_webhook_deliveries(self, organization_id: str, *, limit: int) -> int:
        rows = await self.prisma.query_raw(
            """
            WITH candidates AS (
                SELECT event_id FROM deltallm_batch_webhook_outbox
                WHERE (
                    created_by_organization_id = $1
                    OR (
                        created_by_organization_id IS NULL
                        AND created_by_team_id IN (
                            SELECT team_id FROM deltallm_teamtable
                            WHERE organization_id = $1
                            UNION
                            SELECT team_id FROM deltallm_teamtombstone
                            WHERE organization_id = $1
                        )
                    )
                )
                  AND (
                      status IN ('queued', 'retrying')
                      OR (
                          status = 'processing'
                          AND COALESCE(lease_expires_at, '-infinity'::timestamptz)
                              <= clock_timestamp()
                      )
                  )
                ORDER BY created_at ASC, event_id ASC
                LIMIT $2
            )
            UPDATE deltallm_batch_webhook_outbox w
            SET status = 'failed',
                last_error = 'organization_deletion_requested',
                locked_by = NULL, lease_expires_at = NULL, updated_at = NOW()
            FROM candidates c
            WHERE w.event_id = c.event_id
            RETURNING w.event_id
            """,
            organization_id,
            limit,
        )
        return len(rows)

    async def _delete_memberships(
        self,
        organization_id: str,
        *,
        page_size: int,
    ) -> CleanupPageResult:
        processed = await self._delete_organization_memberships_page(
            organization_id=organization_id,
            limit=page_size,
        )
        if processed < page_size:
            processed += await self._delete_team_memberships_page(
                organization_id=organization_id,
                limit=page_size - processed,
            )
        return CleanupPageResult(processed=processed, remaining=processed >= page_size)

    async def _delete_organization_memberships_page(
        self,
        *,
        organization_id: str,
        limit: int,
    ) -> int:
        rows = await self.prisma.query_raw(
            """
            WITH candidates AS MATERIALIZED (
                SELECT ctid, account_id
                FROM deltallm_organizationmembership
                WHERE organization_id = $1
                LIMIT $2
            ), principal_tombstones AS (
                INSERT INTO deltallm_organizationprincipaltombstone (
                    principal_id, organization_id, deletion_job_id, recorded_at
                )
                SELECT DISTINCT c.account_id, $1, o.deletion_job_id, NOW()
                FROM candidates c
                JOIN deltallm_organizationtable o ON o.organization_id = $1
                WHERE o.deletion_job_id IS NOT NULL
                ON CONFLICT (principal_id, organization_id) DO NOTHING
            )
            DELETE FROM deltallm_organizationmembership membership
            USING candidates candidate
            WHERE membership.ctid = candidate.ctid
            RETURNING membership.membership_id
            """,
            organization_id,
            limit,
        )
        return len(rows)

    async def _delete_team_memberships_page(
        self,
        *,
        organization_id: str,
        limit: int,
    ) -> int:
        rows = await self.prisma.query_raw(
            """
            WITH candidates AS MATERIALIZED (
                SELECT membership.ctid, membership.account_id
                FROM deltallm_teammembership membership
                JOIN deltallm_teamtable team ON team.team_id = membership.team_id
                WHERE team.organization_id = $1
                LIMIT $2
            ), principal_tombstones AS (
                INSERT INTO deltallm_organizationprincipaltombstone (
                    principal_id, organization_id, deletion_job_id, recorded_at
                )
                SELECT DISTINCT c.account_id, $1, o.deletion_job_id, NOW()
                FROM candidates c
                JOIN deltallm_organizationtable o ON o.organization_id = $1
                WHERE o.deletion_job_id IS NOT NULL
                ON CONFLICT (principal_id, organization_id) DO NOTHING
            )
            DELETE FROM deltallm_teammembership membership
            USING candidates candidate
            WHERE membership.ctid = candidate.ctid
            RETURNING membership.membership_id
            """,
            organization_id,
            limit,
        )
        return len(rows)

    async def _delete_team_state(
        self,
        organization_id: str,
        *,
        deletion_job_id: str,
        page_size: int,
    ) -> CleanupPageResult:
        processed = await self._detach_users(organization_id, limit=page_size)
        if processed < page_size:
            processed += await self._delete_ctid_page(
                table="deltallm_teammodelspend",
                predicate=(
                    "team_id IN ("
                    "SELECT team_id FROM deltallm_teamtable WHERE organization_id = $1 "
                    "UNION SELECT team_id FROM deltallm_teamtombstone WHERE organization_id = $1)"
                ),
                organization_id=organization_id,
                limit=page_size - processed,
            )
        if processed < page_size:
            processed += await self._delete_scheduler_flows(
                organization_id,
                limit=page_size - processed,
            )
        if processed < page_size:
            processed += await self._delete_teams_page(
                organization_id,
                deletion_job_id=deletion_job_id,
                limit=page_size - processed,
            )
        return CleanupPageResult(processed=processed, remaining=processed >= page_size)

    async def _delete_teams_page(
        self,
        organization_id: str,
        *,
        deletion_job_id: str,
        limit: int,
    ) -> int:
        conflicting = await self.prisma.query_raw(
            """
            SELECT t.team_id
            FROM deltallm_teamtable t
            JOIN deltallm_teamtombstone tombstone ON tombstone.team_id = t.team_id
            WHERE t.organization_id = $1
              AND (
                  tombstone.organization_id <> $1
                  OR tombstone.deletion_job_id <> $2
              )
            LIMIT 1
            """,
            organization_id,
            deletion_job_id,
        )
        if conflicting:
            raise RuntimeError("team tombstone ownership mismatch")
        rows = await self.prisma.query_raw(
            """
            WITH candidates AS MATERIALIZED (
                SELECT team_id
                FROM deltallm_teamtable
                WHERE organization_id = $1
                ORDER BY team_id ASC
                LIMIT $3
                FOR UPDATE
            ), tombstoned AS (
                INSERT INTO deltallm_teamtombstone (
                    team_id, organization_id, deletion_job_id, deleted_at
                )
                SELECT team_id, $1, $2, NOW()
                FROM candidates
                ON CONFLICT (team_id) DO UPDATE
                SET deleted_at = deltallm_teamtombstone.deleted_at
                WHERE deltallm_teamtombstone.organization_id = EXCLUDED.organization_id
                  AND deltallm_teamtombstone.deletion_job_id = EXCLUDED.deletion_job_id
                RETURNING team_id
            )
            DELETE FROM deltallm_teamtable t
            USING candidates c, tombstoned tombstone
            WHERE t.team_id = c.team_id
              AND tombstone.team_id = c.team_id
            RETURNING t.team_id
            """,
            organization_id,
            deletion_job_id,
            limit,
        )
        return len(rows)

    async def _detach_users(self, organization_id: str, *, limit: int) -> int:
        rows = await self.prisma.query_raw(
            """
            WITH candidates AS MATERIALIZED (
                SELECT u.user_id FROM deltallm_usertable u
                WHERE u.team_id IN (
                    SELECT team_id FROM deltallm_teamtable WHERE organization_id = $1
                    UNION
                    SELECT team_id FROM deltallm_teamtombstone WHERE organization_id = $1
                )
                ORDER BY u.user_id ASC
                LIMIT $2
            ), principal_tombstones AS (
                INSERT INTO deltallm_organizationprincipaltombstone (
                    principal_id, organization_id, deletion_job_id, recorded_at
                )
                SELECT c.user_id, $1, o.deletion_job_id, NOW()
                FROM candidates c
                JOIN deltallm_organizationtable o ON o.organization_id = $1
                WHERE o.deletion_job_id IS NOT NULL
                ON CONFLICT (principal_id, organization_id) DO NOTHING
            )
            UPDATE deltallm_usertable u
            SET team_id = NULL, updated_at = NOW()
            FROM candidates c
            WHERE u.user_id = c.user_id
            RETURNING u.user_id
            """,
            organization_id,
            limit,
        )
        return len(rows)

    async def _delete_scheduler_flows(self, organization_id: str, *, limit: int) -> int:
        return await self._delete_ctid_page(
            table="deltallm_batch_scheduler_flow",
            predicate=(
                "(tenant_scope_type = 'organization' AND tenant_scope_id = $1) OR "
                "(tenant_scope_type = 'team' AND tenant_scope_id IN "
                "(SELECT team_id FROM deltallm_teamtable WHERE organization_id = $1 "
                "UNION SELECT team_id FROM deltallm_teamtombstone "
                "WHERE organization_id = $1)) OR "
                "(tenant_scope_type = 'user' AND tenant_scope_id IN "
                "(SELECT principal_id FROM deltallm_organizationprincipaltombstone "
                "WHERE organization_id = $1)) OR EXISTS ("
                "SELECT 1 FROM deltallm_batch_job j "
                "WHERE j.tenant_scope_type = target.tenant_scope_type "
                "AND j.tenant_scope_id = target.tenant_scope_id "
                "AND (j.created_by_organization_id = $1 OR ("
                "j.created_by_organization_id IS NULL AND j.created_by_team_id IN ("
                "SELECT team_id FROM deltallm_teamtable WHERE organization_id = $1 "
                "UNION SELECT team_id FROM deltallm_teamtombstone "
                "WHERE organization_id = $1))))"
            ),
            organization_id=organization_id,
            limit=limit,
        )

    async def _delete_api_key_scheduler_flows(
        self,
        organization_id: str,
        *,
        limit: int,
    ) -> int:
        rows = await self.prisma.query_raw(
            """
            WITH target_keys AS MATERIALIZED (
                SELECT v.token
                FROM deltallm_verificationtoken v
                LEFT JOIN deltallm_usertable u ON u.user_id = v.user_id
                LEFT JOIN deltallm_serviceaccount s
                  ON s.service_account_id = v.owner_service_account_id
                JOIN deltallm_teamtable t
                  ON t.team_id = COALESCE(v.team_id, u.team_id, s.team_id)
                WHERE t.organization_id = $1
            ), candidates AS (
                SELECT flow.ctid
                FROM deltallm_batch_scheduler_flow flow
                WHERE (
                    flow.tenant_scope_type = 'api_key'
                    AND flow.tenant_scope_id IN (SELECT token FROM target_keys)
                ) OR EXISTS (
                    SELECT 1
                    FROM deltallm_batch_job j
                    WHERE j.tenant_scope_type = flow.tenant_scope_type
                      AND j.tenant_scope_id = flow.tenant_scope_id
                      AND j.created_by_api_key IN (SELECT token FROM target_keys)
                )
                LIMIT $2
            )
            DELETE FROM deltallm_batch_scheduler_flow flow
            USING candidates candidate
            WHERE flow.ctid = candidate.ctid
            RETURNING 1
            """,
            organization_id,
            limit,
        )
        return len(rows)

    async def _delete_by_id_page(
        self,
        *,
        table: str,
        id_column: str,
        predicate: str,
        organization_id: str,
        limit: int,
    ) -> int:
        allowed_targets = {("deltallm_serviceaccount", "service_account_id")}
        if (table, id_column) not in allowed_targets:
            raise ValueError("unsupported organization tenant cleanup target")
        rows = await self.prisma.query_raw(
            f"""
            WITH candidates AS (
                SELECT {id_column} FROM {table}
                WHERE {predicate}
                ORDER BY {id_column} ASC
                LIMIT $2
            )
            DELETE FROM {table} target
            USING candidates c
            WHERE target.{id_column} = c.{id_column}
            RETURNING target.{id_column}
            """,
            organization_id,
            limit,
        )
        return len(rows)

    async def _delete_ctid_page(
        self,
        *,
        table: str,
        predicate: str,
        organization_id: str,
        limit: int,
    ) -> int:
        allowed_tables = {
            "deltallm_organizationmembership",
            "deltallm_teammembership",
            "deltallm_teammodelspend",
            "deltallm_batch_scheduler_flow",
        }
        if table not in allowed_tables:
            raise ValueError("unsupported organization tenant cleanup target")
        rows = await self.prisma.query_raw(
            f"""
            WITH candidates AS (
                SELECT target.ctid FROM {table} target WHERE {predicate} LIMIT $2
            )
            DELETE FROM {table} target
            USING candidates c
            WHERE target.ctid = c.ctid
            RETURNING 1
            """,
            organization_id,
            limit,
        )
        return len(rows)


__all__ = ["OrganizationDeletionTenantCleanup"]
