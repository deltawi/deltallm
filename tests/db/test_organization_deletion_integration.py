from __future__ import annotations

from uuid import uuid4

import pytest

from src.audit.actions import AuditAction
from src.db.organization_deletion_cleanup_repository import (
    CleanupPageResult,
    OrganizationDeletionCleanupRepository,
)
from src.db.organization_deletion_final_inventory import (
    ORGANIZATION_DELETION_FINAL_INVENTORY_SQL,
)
from src.db.organization_deletion_records import OrganizationDeletionJobRecord
from src.db.organization_deletion_repository import OrganizationDeletionRepository
from src.db.organization_deletion_worker_repository import (
    OrganizationDeletionClaimLost,
    OrganizationDeletionWorkerRepository,
)
from src.services.organization_deletion import OrganizationDeletionService
from src.services.organization_deletion_worker import (
    OrganizationDeletionWorker,
    OrganizationDeletionWorkerConfig,
)
from tests.db.tier_migration_helpers import connect_prisma


async def _seed_tenant(
    db,  # noqa: ANN001
    *,
    organization_id: str,
    team_id: str,
    account_id: str,
    service_account_id: str,
) -> None:
    await db.execute_raw(
        """
        INSERT INTO deltallm_organizationtable (
            organization_id, organization_name, created_at, updated_at
        ) VALUES ($1, 'Deletion integration tenant', NOW(), NOW())
        """,
        organization_id,
    )
    await db.execute_raw(
        """
        INSERT INTO deltallm_teamtable (
            team_id, team_alias, organization_id, models, created_at, updated_at
        ) VALUES ($1, 'Deletion team', $2, ARRAY[]::text[], NOW(), NOW())
        """,
        team_id,
        organization_id,
    )
    await db.execute_raw(
        """
        INSERT INTO deltallm_platformaccount (
            account_id, email, created_at, updated_at
        ) VALUES ($1, $2, NOW(), NOW())
        """,
        account_id,
        f"{account_id}@example.com",
    )
    await db.execute_raw(
        """
        INSERT INTO deltallm_organizationmembership (
            account_id, organization_id, role, created_at, updated_at
        ) VALUES ($1, $2, 'org_owner', NOW(), NOW())
        """,
        account_id,
        organization_id,
    )
    await db.execute_raw(
        """
        INSERT INTO deltallm_teammembership (
            account_id, team_id, role, created_at, updated_at
        ) VALUES ($1, $2, 'team_admin', NOW(), NOW())
        """,
        account_id,
        team_id,
    )
    await db.execute_raw(
        """
        INSERT INTO deltallm_serviceaccount (
            service_account_id, team_id, name, created_at, updated_at
        ) VALUES ($1, $2, 'integration-service', NOW(), NOW())
        """,
        service_account_id,
        team_id,
    )
    await db.execute_raw(
        """
        INSERT INTO deltallm_verificationtoken (
            token, key_name, team_id, models, created_at, updated_at
        ) VALUES ($1, 'integration-key', $2, ARRAY[]::text[], NOW(), NOW())
        """,
        f"token-{uuid4().hex}",
        team_id,
    )


async def _cleanup(
    db,  # noqa: ANN001
    *,
    organization_id: str,
    account_id: str,
) -> None:
    await db.execute_raw(
        "DELETE FROM deltallm_cacheinvalidationoutbox WHERE scope_type = 'organization' AND scope_id = $1",
        organization_id,
    )
    await db.execute_raw(
        "DELETE FROM deltallm_auditevent WHERE organization_id = $1",
        organization_id,
    )
    await db.execute_raw(
        "DELETE FROM deltallm_organizationdeletionjob WHERE organization_id = $1",
        organization_id,
    )
    await db.execute_raw(
        "DELETE FROM deltallm_organizationtombstone WHERE organization_id = $1",
        organization_id,
    )
    await db.execute_raw(
        "DELETE FROM deltallm_teamtombstone WHERE organization_id = $1",
        organization_id,
    )
    await db.execute_raw(
        "DELETE FROM deltallm_organizationprincipaltombstone WHERE organization_id = $1",
        organization_id,
    )
    await db.execute_raw(
        "DELETE FROM deltallm_organizationtable WHERE organization_id = $1",
        organization_id,
    )
    await db.execute_raw(
        "DELETE FROM deltallm_platformaccount WHERE account_id = $1",
        account_id,
    )


@pytest.mark.asyncio
async def test_durable_worker_removes_tenant_state_and_retains_history() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    organization_id = f"org-delete-{suffix}"
    team_id = f"team-delete-{suffix}"
    account_id = str(uuid4())
    service_account_id = str(uuid4())
    legacy_user_id = f"legacy-user-{suffix}"
    late_user_id = f"late-user-{suffix}"
    scheduler_flow_id = str(uuid4())
    external_mcp_server_id = str(uuid4())

    try:
        await _seed_tenant(
            db,
            organization_id=organization_id,
            team_id=team_id,
            account_id=account_id,
            service_account_id=service_account_id,
        )
        token_rows = await db.query_raw(
            "SELECT token FROM deltallm_verificationtoken WHERE team_id = $1 LIMIT 1",
            team_id,
        )
        api_key_scope_id = str(token_rows[0]["token"])
        await db.execute_raw(
            """
            INSERT INTO deltallm_usertable (
                user_id, team_id, models, created_at, updated_at
            ) VALUES ($1, $2, ARRAY[]::text[], NOW(), NOW())
            """,
            legacy_user_id,
            team_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_batch_scheduler_flow (
                flow_id, service_tier, model_group, tenant_scope_type,
                tenant_scope_id, created_at, updated_at
            ) VALUES ($1, 'standard', 'model-a', 'user', $2, NOW(), NOW())
            """,
            scheduler_flow_id,
            legacy_user_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_mcpserver (
                mcp_server_id, server_key, name, owner_scope_type,
                transport, base_url, forwarded_headers_allowlist, created_at, updated_at
            ) VALUES (
                $1, $2, 'External MCP', 'global',
                'streamable_http', 'https://mcp.example.com', ARRAY[]::text[], NOW(), NOW()
            )
            """,
            external_mcp_server_id,
            f"external-mcp-{suffix}",
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_mcpapprovalrequest (
                mcp_approval_request_id, mcp_server_id, tool_name,
                scope_type, scope_id, status, request_fingerprint,
                organization_id, arguments_json, created_at, updated_at
            ) VALUES (
                $1, $2, 'example_tool', 'organization', $3, 'pending',
                $4, $3, '{}'::jsonb, NOW(), NOW()
            )
            """,
            str(uuid4()),
            external_mcp_server_id,
            organization_id,
            f"approval-{suffix}",
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_mcpapprovalrequest (
                mcp_approval_request_id, mcp_server_id, tool_name,
                scope_type, scope_id, status, request_fingerprint,
                organization_id, requested_by_api_key, arguments_json,
                created_at, updated_at
            ) VALUES
                ($1, $3, 'team_tool', 'team', $4, 'pending', $5,
                 NULL, NULL, '{}'::jsonb, NOW(), NOW()),
                ($2, $3, 'key_tool', 'api_key', $6, 'pending', $7,
                 NULL, $6, '{}'::jsonb, NOW(), NOW())
            """,
            str(uuid4()),
            str(uuid4()),
            external_mcp_server_id,
            team_id,
            f"team-approval-{suffix}",
            api_key_scope_id,
            f"key-approval-{suffix}",
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_callabletargetbinding (
                callable_target_binding_id, callable_key, scope_type, scope_id,
                enabled, created_at, updated_at
            ) VALUES ($1, 'gpt-4o-mini', 'api_key', $2, TRUE, NOW(), NOW())
            """,
            str(uuid4()),
            api_key_scope_id,
        )
        repository = OrganizationDeletionRepository(db)
        service = OrganizationDeletionService(
            repository=repository,
            cache_invalidation_service=None,
            recovery_window_hours=1,
            requests_enabled=True,
        )
        preview = await service.preview(organization_id)
        assert preview.record.counts.teams == 1
        assert preview.record.counts.api_keys == 1
        assert preview.record.counts.service_accounts == 1
        assert preview.record.counts.pending_mcp_approvals == 3
        assert preview.record.counts.scope_bindings == 1

        await db.execute_raw(
            """
            INSERT INTO deltallm_promptrenderlog (
                prompt_render_log_id, team_id, organization_id, api_key, user_id,
                status, variables, created_at
            ) VALUES
                ($1, $4, $5, NULL, NULL, 'success', '{"secret":"value"}'::jsonb, NOW()),
                ($2, NULL, NULL, $6, NULL, 'success', '{"secret":"value"}'::jsonb, NOW()),
                ($3, $4, NULL, NULL, $7, 'success', '{"secret":"value"}'::jsonb, NOW())
            """,
            str(uuid4()),
            str(uuid4()),
            str(uuid4()),
            team_id,
            organization_id,
            api_key_scope_id,
            legacy_user_id,
        )
        refreshed_preview = await service.preview(organization_id)
        assert refreshed_preview.record.counts.prompt_render_logs == 3

        requested = await service.request_deletion(
            organization_id=organization_id,
            confirmation_name="Deletion integration tenant",
            plan_token=refreshed_preview.plan_token,
            idempotency_key=f"delete-{suffix}",
            requested_by_account_id=account_id,
        )
        with pytest.raises(Exception, match="organization is not active"):
            await db.execute_raw(
                """
                INSERT INTO deltallm_teamtable (
                    team_id, team_alias, organization_id, models, created_at, updated_at
                ) VALUES ($1, 'Late team', $2, ARRAY[]::text[], NOW(), NOW())
                """,
                f"late-{team_id}",
                organization_id,
            )
        with pytest.raises(Exception, match="organization is not active"):
            await db.execute_raw(
                "UPDATE deltallm_teamtable SET team_alias = 'Late mutation' WHERE team_id = $1",
                team_id,
            )
        with pytest.raises(Exception, match="organization is not active"):
            await db.execute_raw(
                """
                INSERT INTO deltallm_usertable (
                    user_id, team_id, models, created_at, updated_at
                ) VALUES ($1, $2, ARRAY[]::text[], NOW(), NOW())
                """,
                late_user_id,
                team_id,
            )
        with pytest.raises(Exception, match="organization is not active"):
            await db.execute_raw(
                "UPDATE deltallm_teamtable SET organization_id = NULL WHERE team_id = $1",
                team_id,
            )
        with pytest.raises(Exception, match="organization is not active"):
            await db.execute_raw(
                "UPDATE deltallm_verificationtoken SET team_id = NULL WHERE team_id = $1",
                team_id,
            )
        with pytest.raises(Exception, match="inactive organization cannot be modified"):
            await db.execute_raw(
                "UPDATE deltallm_organizationtable SET organization_name = 'Late mutation' WHERE organization_id = $1",
                organization_id,
            )
        with pytest.raises(Exception, match="organization is not active"):
            await db.execute_raw(
                """
                INSERT INTO deltallm_mcpapprovalrequest (
                    mcp_approval_request_id, mcp_server_id, tool_name,
                    scope_type, scope_id, status, request_fingerprint,
                    organization_id, arguments_json, created_at, updated_at
                ) VALUES (
                    $1, $2, 'late_tool', 'organization', $3, 'pending',
                    $4, $3, '{}'::jsonb, NOW(), NOW()
                )
                """,
                str(uuid4()),
                external_mcp_server_id,
                organization_id,
                f"late-approval-{suffix}",
            )
        await db.execute_raw(
            """
            UPDATE deltallm_mcpapprovalrequest
            SET status = 'rejected',
                decision_comment = 'Lifecycle decision',
                decided_at = NOW(),
                updated_at = NOW()
            WHERE request_fingerprint = $1
            """,
            f"approval-{suffix}",
        )
        with pytest.raises(Exception, match="organization is not active"):
            await db.execute_raw(
                """
                UPDATE deltallm_mcpapprovalrequest
                SET arguments_json = '{"changed":true}'::jsonb, updated_at = NOW()
                WHERE request_fingerprint = $1
                """,
                f"approval-{suffix}",
            )
        with pytest.raises(Exception, match="organization is not active"):
            await db.execute_raw(
                """
                INSERT INTO deltallm_callabletargetbinding (
                    callable_target_binding_id, callable_key, scope_type, scope_id,
                    enabled, created_at, updated_at
                ) VALUES ($1, 'late-target', 'api_key', $2, TRUE, NOW(), NOW())
                """,
                str(uuid4()),
                api_key_scope_id,
            )
        await db.execute_raw(
            """
            UPDATE deltallm_organizationdeletionjob
            SET status = 'failed', last_error_code = 'integration_failure'
            WHERE deletion_job_id = $1
            """,
            requested.job.deletion_job_id,
        )
        await db.execute_raw(
            """
            UPDATE deltallm_organizationtable
            SET lifecycle_state = 'deletion_failed', lifecycle_version = lifecycle_version + 1
            WHERE organization_id = $1
            """,
            organization_id,
        )
        worker_repository = OrganizationDeletionWorkerRepository(db)
        assert await worker_repository.retry_failed(
            organization_id=organization_id,
            deletion_job_id=requested.job.deletion_job_id,
            retried_by_account_id=account_id,
        )
        await db.execute_raw(
            """
            UPDATE deltallm_organizationdeletionjob
            SET not_before_at = NOW() - INTERVAL '1 second', next_attempt_at = NOW()
            WHERE deletion_job_id = $1
            """,
            requested.job.deletion_job_id,
        )
        worker = OrganizationDeletionWorker(
            repository=worker_repository,
            cleanup_repository=OrganizationDeletionCleanupRepository(db),
            worker_id="integration-worker",
            config=OrganizationDeletionWorkerConfig(
                page_size=1,
                max_pages_per_claim=1,
                waiting_poll_seconds=0.01,
            ),
        )

        for _ in range(80):
            await worker.process_once()
            job = await repository.get_job(
                organization_id=organization_id,
                deletion_job_id=requested.job.deletion_job_id,
            )
            if job is not None and job.status == "completed":
                break

        assert job is not None
        assert job.status == "completed"
        assert await repository.tombstone_exists(organization_id) is True
        assert await repository.organization_lifecycle_state(organization_id) is None
        assert not await db.query_raw(
            "SELECT 1 FROM deltallm_teamtable WHERE organization_id = $1",
            organization_id,
        )
        assert not await db.query_raw(
            "SELECT 1 FROM deltallm_verificationtoken WHERE team_id = $1",
            team_id,
        )
        assert not await db.query_raw(
            """
            SELECT 1 FROM deltallm_promptrenderlog
            WHERE organization_id = $1 OR api_key = $2 OR user_id = $3
            """,
            organization_id,
            api_key_scope_id,
            legacy_user_id,
        )
        assert not await db.query_raw(
            "SELECT 1 FROM deltallm_mcpapprovalrequest WHERE organization_id = $1",
            organization_id,
        )
        assert not await db.query_raw(
            "SELECT 1 FROM deltallm_mcpapprovalrequest WHERE scope_id IN ($1, $2)",
            team_id,
            api_key_scope_id,
        )
        assert not await db.query_raw(
            "SELECT 1 FROM deltallm_callabletargetbinding WHERE scope_id = $1",
            api_key_scope_id,
        )
        assert not await db.query_raw(
            "SELECT 1 FROM deltallm_batch_scheduler_flow WHERE flow_id = $1",
            scheduler_flow_id,
        )
        audit_rows = await db.query_raw(
            "SELECT action FROM deltallm_auditevent WHERE organization_id = $1 ORDER BY occurred_at",
            organization_id,
        )
        assert [row["action"] for row in audit_rows] == [
            AuditAction.ADMIN_ORGANIZATION_DELETION_REQUEST.value,
            AuditAction.ADMIN_ORGANIZATION_DELETION_RETRY.value,
            AuditAction.SYSTEM_ORGANIZATION_DELETION_COMPLETE.value,
        ]
        with pytest.raises(Exception, match="permanently tombstoned"):
            await db.execute_raw(
                """
                INSERT INTO deltallm_organizationtable (
                    organization_id, organization_name, created_at, updated_at
                ) VALUES ($1, 'Reused organization', NOW(), NOW())
                """,
                organization_id,
            )
        with pytest.raises(Exception, match="permanently tombstoned"):
            await db.execute_raw(
                """
                INSERT INTO deltallm_teamtable (
                    team_id, team_alias, organization_id, models, created_at, updated_at
                ) VALUES ($1, 'Reused team', NULL, ARRAY[]::text[], NOW(), NOW())
                """,
                team_id,
            )
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_usertable WHERE user_id = $1",
            late_user_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_batch_scheduler_flow WHERE flow_id = $1",
            scheduler_flow_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_usertable WHERE user_id = $1",
            legacy_user_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_mcpserver WHERE mcp_server_id = $1",
            external_mcp_server_id,
        )
        await _cleanup(
            db,
            organization_id=organization_id,
            account_id=account_id,
        )
        await db.disconnect()


@pytest.mark.asyncio
async def test_cleanup_page_rolls_back_when_lease_expires_before_progress_commit() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    organization_id = f"org-fence-{suffix}"
    deletion_job_id = str(uuid4())
    prompt_render_log_id = str(uuid4())
    try:
        await db.execute_raw(
            """
            INSERT INTO deltallm_organizationtable (
                organization_id, organization_name, created_at, updated_at
            ) VALUES ($1, 'Fence fixture', NOW(), NOW())
            """,
            organization_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_promptrenderlog (
                prompt_render_log_id, organization_id, status, variables, created_at
            ) VALUES ($1, $2, 'success', '{"secret":"value"}'::jsonb, NOW())
            """,
            prompt_render_log_id,
            organization_id,
        )
        await db.execute_raw(
            """
            UPDATE deltallm_organizationtable
            SET lifecycle_state = 'purging', deletion_job_id = $2,
                lifecycle_version = lifecycle_version + 1, updated_at = NOW()
            WHERE organization_id = $1
            """,
            organization_id,
            deletion_job_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_organizationdeletionjob (
                deletion_job_id, organization_id, status, phase,
                idempotency_key, request_hash, plan_token, plan_snapshot,
                options, progress, not_before_at, next_attempt_at,
                locked_by, lease_expires_at, claim_epoch, created_at, updated_at
            ) VALUES (
                $1, $2, 'processing', 'purge_sensitive_history',
                $3, 'request-hash', 'plan-token', '{}'::jsonb,
                '{}'::jsonb, '{}'::jsonb, NOW(), NOW(),
                'fence-worker', NOW() + INTERVAL '1 minute', 7, NOW(), NOW()
            )
            """,
            deletion_job_id,
            organization_id,
            f"fence-{suffix}",
        )
        job = OrganizationDeletionJobRecord(
            deletion_job_id=deletion_job_id,
            organization_id=organization_id,
            status="processing",
            phase="purge_sensitive_history",
            requested_by_account_id=None,
            idempotency_key=f"fence-{suffix}",
            request_hash="request-hash",
            plan_token="plan-token",
            locked_by="fence-worker",
            claim_epoch=7,
        )
        cleanup_repository = OrganizationDeletionCleanupRepository(db)

        async def _delete_then_expire(tx) -> CleanupPageResult:  # noqa: ANN001
            result = await cleanup_repository.with_db(tx).delete_sensitive_history_page(
                organization_id,
                page_size=1,
            )
            await tx.execute_raw(
                """
                UPDATE deltallm_organizationdeletionjob
                SET lease_expires_at = clock_timestamp() - INTERVAL '1 second'
                WHERE deletion_job_id = $1
                """,
                deletion_job_id,
            )
            return result

        with pytest.raises(OrganizationDeletionClaimLost):
            await OrganizationDeletionWorkerRepository(db).run_cleanup_page(
                job,
                worker_id="fence-worker",
                lease_seconds=60,
                cleanup=_delete_then_expire,
                next_phase="remove_scoped_access",
                progress_key="deleted_sensitive_records",
                release_claim=True,
            )

        assert await db.query_raw(
            "SELECT 1 FROM deltallm_promptrenderlog WHERE prompt_render_log_id = $1",
            prompt_render_log_id,
        )
        progress_rows = await db.query_raw(
            "SELECT progress FROM deltallm_organizationdeletionjob WHERE deletion_job_id = $1",
            deletion_job_id,
        )
        assert dict(progress_rows[0]["progress"]) == {}
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_promptrenderlog WHERE prompt_render_log_id = $1",
            prompt_render_log_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_organizationdeletionjob WHERE deletion_job_id = $1",
            deletion_job_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id = $1",
            organization_id,
        )
        await db.disconnect()


@pytest.mark.asyncio
async def test_finalization_inventory_atomically_reroutes_late_tenant_state() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    organization_id = f"org-final-inventory-{suffix}"
    team_id = f"team-final-inventory-{suffix}"
    deletion_job_id = str(uuid4())
    input_file_id = str(uuid4())
    session_id = str(uuid4())
    webhook_event_id = str(uuid4())
    flow_id = str(uuid4())
    batch_id = str(uuid4())
    try:
        await db.execute_raw(
            """
            INSERT INTO deltallm_organizationtable (
                organization_id, organization_name, created_at, updated_at
            ) VALUES ($1, 'Final inventory fixture', NOW(), NOW())
            """,
            organization_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_teamtable (
                team_id, organization_id, models, created_at, updated_at
            ) VALUES ($1, $2, ARRAY[]::text[], NOW(), NOW())
            """,
            team_id,
            organization_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_batch_file (
                file_id, purpose, filename, bytes, status,
                storage_backend, storage_key, created_by_organization_id, created_at
            ) VALUES (
                $1, 'batch', 'input.jsonl', 2, 'processed',
                'local', $2, $3, NOW()
            )
            """,
            input_file_id,
            f"final-inventory/{suffix}/input.jsonl",
            organization_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_batch_create_session (
                session_id, target_batch_id, status, endpoint, input_file_id,
                staged_storage_backend, staged_storage_key, staged_bytes,
                expected_item_count, created_by_organization_id, created_at
            ) VALUES (
                $1, $2, 'staged', '/v1/chat/completions', $3,
                'local', $4, 2, 1, $5, NOW()
            )
            """,
            session_id,
            batch_id,
            input_file_id,
            f"final-inventory/{suffix}/stage.jsonl",
            organization_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_batch_webhook_outbox (
                event_id, batch_id, event_type, created_by_organization_id,
                target_config_ciphertext, payload_json, payload_sha256,
                status, created_at, updated_at
            ) VALUES (
                $1, $2, 'batch.completed', $3, 'ciphertext', '{}'::jsonb,
                $4, 'queued', NOW(), NOW()
            )
            """,
            webhook_event_id,
            batch_id,
            organization_id,
            "0" * 64,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_batch_scheduler_flow (
                flow_id, service_tier, model_group, tenant_scope_type,
                tenant_scope_id, created_at, updated_at
            ) VALUES ($1, 'standard', 'model-a', 'organization', $2, NOW(), NOW())
            """,
            flow_id,
            organization_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_teammodelspend (team_id, model, spend, updated_at)
            VALUES ($1, 'model-a', 1.0, NOW())
            """,
            team_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_organizationdeletionjob (
                deletion_job_id, organization_id, status, phase,
                idempotency_key, request_hash, plan_token, plan_snapshot,
                options, progress, not_before_at, next_attempt_at,
                locked_by, lease_expires_at, claim_epoch, created_at, updated_at
            ) VALUES (
                $1, $2, 'processing', 'finalize', $3, 'request-hash',
                'plan-token', '{}'::jsonb, '{}'::jsonb, '{}'::jsonb,
                NOW(), NOW(), 'final-worker', NOW() + INTERVAL '1 minute',
                3, NOW(), NOW()
            )
            """,
            deletion_job_id,
            organization_id,
            f"final-inventory-{suffix}",
        )
        await db.execute_raw(
            """
            UPDATE deltallm_organizationtable
            SET lifecycle_state = 'purging', deletion_job_id = $2,
                lifecycle_version = lifecycle_version + 1, updated_at = NOW()
            WHERE organization_id = $1
            """,
            organization_id,
            deletion_job_id,
        )

        inventory = await db.query_raw(
            ORGANIZATION_DELETION_FINAL_INVENTORY_SQL,
            organization_id,
        )
        assert inventory[0]["staged_batch_sessions"] is True
        assert inventory[0]["webhook_deliveries"] is True
        assert inventory[0]["scheduler_flows"] is True
        assert inventory[0]["team_model_counters"] is True

        result = await OrganizationDeletionWorkerRepository(db).finalize(
            OrganizationDeletionJobRecord(
                deletion_job_id=deletion_job_id,
                organization_id=organization_id,
                status="processing",
                phase="finalize",
                requested_by_account_id=None,
                idempotency_key=f"final-inventory-{suffix}",
                request_hash="request-hash",
                plan_token="plan-token",
                locked_by="final-worker",
                claim_epoch=3,
            ),
            worker_id="final-worker",
        )

        assert result.outcome == "retry_cleanup"
        assert result.next_phase == "cancel_pending"
        job_rows = await db.query_raw(
            """
            SELECT status, phase FROM deltallm_organizationdeletionjob
            WHERE deletion_job_id = $1
            """,
            deletion_job_id,
        )
        assert dict(job_rows[0]) == {"status": "pending", "phase": "cancel_pending"}
        assert await db.query_raw(
            "SELECT 1 FROM deltallm_organizationtable WHERE organization_id = $1",
            organization_id,
        )
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_batch_create_session WHERE session_id = $1",
            session_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_batch_webhook_outbox WHERE event_id = $1",
            webhook_event_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_batch_scheduler_flow WHERE flow_id = $1",
            flow_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_teammodelspend WHERE team_id = $1",
            team_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_batch_file WHERE file_id = $1",
            input_file_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_organizationdeletionjob WHERE deletion_job_id = $1",
            deletion_job_id,
        )
        await db.execute_raw("DELETE FROM deltallm_teamtable WHERE team_id = $1", team_id)
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id = $1",
            organization_id,
        )
        await db.disconnect()


@pytest.mark.asyncio
async def test_preview_blocks_ambiguous_legacy_user_sensitive_history() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    organization_ids = (f"org-ambiguous-a-{suffix}", f"org-ambiguous-b-{suffix}")
    team_id = f"team-ambiguous-{suffix}"
    account_id = str(uuid4())
    prompt_log_id = str(uuid4())
    try:
        await db.execute_raw(
            """
            INSERT INTO deltallm_organizationtable (
                organization_id, organization_name, created_at, updated_at
            ) VALUES
                ($1, 'Ambiguous owner A', NOW(), NOW()),
                ($2, 'Ambiguous owner B', NOW(), NOW())
            """,
            *organization_ids,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_teamtable (
                team_id, team_alias, organization_id, models, created_at, updated_at
            ) VALUES ($1, 'Ambiguous team', $2, ARRAY[]::text[], NOW(), NOW())
            """,
            team_id,
            organization_ids[0],
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_platformaccount (
                account_id, email, created_at, updated_at
            ) VALUES ($1, $2, NOW(), NOW())
            """,
            account_id,
            f"ambiguous-{suffix}@example.com",
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_promptrenderlog (
                prompt_render_log_id, user_id, status, variables, created_at
            ) VALUES ($1, $2, 'success', '{"secret":"value"}'::jsonb, NOW())
            """,
            prompt_log_id,
            account_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_organizationmembership (
                account_id, organization_id, role, created_at, updated_at
            ) VALUES
                ($1, $2, 'org_viewer', NOW(), NOW()),
                ($1, $3, 'org_viewer', NOW(), NOW())
            """,
            account_id,
            *organization_ids,
        )

        preview = await OrganizationDeletionService(
            repository=OrganizationDeletionRepository(db),
            cache_invalidation_service=None,
            requests_enabled=True,
        ).preview(organization_ids[0])

        assert preview.record.counts.ambiguous_sensitive_records == 1
        assert preview.record.counts.conflicting_sensitive_records == 0
        assert preview.record.counts.unattributed_sensitive_records == 1
        assert preview.can_request is False
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_promptrenderlog WHERE prompt_render_log_id = $1",
            prompt_log_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_organizationmembership WHERE account_id = $1",
            account_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_teamtable WHERE team_id = $1",
            team_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id IN ($1, $2)",
            *organization_ids,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_platformaccount WHERE account_id = $1",
            account_id,
        )
        await db.disconnect()


@pytest.mark.asyncio
async def test_sensitive_history_uses_scope_precedence_not_requester_membership() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    organization_a = f"org-precedence-a-{suffix}"
    organization_b = f"org-precedence-b-{suffix}"
    team_a = f"team-precedence-a-{suffix}"
    api_key_a = f"key-precedence-a-{suffix}"
    account_b = str(uuid4())
    prompt_log_id = str(uuid4())
    approval_id = str(uuid4())
    mcp_server_id = str(uuid4())
    try:
        await db.execute_raw(
            """
            INSERT INTO deltallm_organizationtable (
                organization_id, organization_name, created_at, updated_at
            ) VALUES
                ($1, 'Precedence owner A', NOW(), NOW()),
                ($2, 'Precedence requester B', NOW(), NOW())
            """,
            organization_a,
            organization_b,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_teamtable (
                team_id, team_alias, organization_id, models, created_at, updated_at
            ) VALUES ($1, 'Precedence team A', $2, ARRAY[]::text[], NOW(), NOW())
            """,
            team_a,
            organization_a,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_platformaccount (
                account_id, email, created_at, updated_at
            ) VALUES ($1, $2, NOW(), NOW())
            """,
            account_b,
            f"precedence-{suffix}@example.com",
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_verificationtoken (
                token, key_name, team_id, models, created_at, updated_at
            ) VALUES ($1, 'Precedence key A', $2, ARRAY[]::text[], NOW(), NOW())
            """,
            api_key_a,
            team_a,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_organizationmembership (
                account_id, organization_id, role, created_at, updated_at
            ) VALUES ($1, $2, 'org_viewer', NOW(), NOW())
            """,
            account_b,
            organization_b,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_mcpserver (
                mcp_server_id, server_key, name, owner_scope_type,
                transport, base_url, forwarded_headers_allowlist, created_at, updated_at
            ) VALUES (
                $1, $2, 'Precedence MCP', 'global', 'streamable_http',
                'https://mcp.example.com', ARRAY[]::text[], NOW(), NOW()
            )
            """,
            mcp_server_id,
            f"precedence-mcp-{suffix}",
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_promptrenderlog (
                prompt_render_log_id, api_key, user_id, status, variables, created_at
            ) VALUES ($1, $2, $3, 'success', '{"secret":"value"}'::jsonb, NOW())
            """,
            prompt_log_id,
            api_key_a,
            account_b,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_mcpapprovalrequest (
                mcp_approval_request_id, mcp_server_id, tool_name,
                scope_type, scope_id, status, request_fingerprint,
                requested_by_user, arguments_json, created_at, updated_at
            ) VALUES (
                $1, $2, 'precedence_tool', 'team', $3, 'pending',
                $4, $5, '{}'::jsonb, NOW(), NOW()
            )
            """,
            approval_id,
            mcp_server_id,
            team_a,
            f"precedence-approval-{suffix}",
            account_b,
        )

        service = OrganizationDeletionService(
            repository=OrganizationDeletionRepository(db),
            cache_invalidation_service=None,
            requests_enabled=True,
        )
        preview_a = await service.preview(organization_a)
        preview_b = await service.preview(organization_b)

        assert preview_a.record.counts.prompt_render_logs == 1
        assert preview_a.record.counts.pending_mcp_approvals == 1
        assert preview_b.record.counts.prompt_render_logs == 0
        assert preview_b.record.counts.pending_mcp_approvals == 0
        assert preview_b.record.counts.ambiguous_sensitive_records == 0
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_mcpapprovalrequest WHERE mcp_approval_request_id = $1",
            approval_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_promptrenderlog WHERE prompt_render_log_id = $1",
            prompt_log_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_mcpserver WHERE mcp_server_id = $1",
            mcp_server_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_organizationmembership WHERE account_id = $1",
            account_b,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_verificationtoken WHERE token = $1",
            api_key_a,
        )
        await db.execute_raw("DELETE FROM deltallm_teamtable WHERE team_id = $1", team_a)
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id IN ($1, $2)",
            organization_a,
            organization_b,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_platformaccount WHERE account_id = $1",
            account_b,
        )
        await db.disconnect()


@pytest.mark.asyncio
async def test_database_scope_guards_reject_missing_and_tombstoned_referents() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    organization_id = f"org-scope-guard-{suffix}"
    other_organization_id = f"org-scope-guard-other-{suffix}"
    other_team_id = f"team-scope-guard-other-{suffix}"
    tombstoned_team_id = f"team-tombstoned-{suffix}"
    input_file_id = str(uuid4())
    batch_id = str(uuid4())
    mcp_server_id = str(uuid4())
    try:
        await db.execute_raw(
            """
            INSERT INTO deltallm_organizationtable (
                organization_id, organization_name, created_at, updated_at
            ) VALUES
                ($1, 'Scope guard fixture', NOW(), NOW()),
                ($2, 'Other scope guard fixture', NOW(), NOW())
            """,
            organization_id,
            other_organization_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_teamtable (
                team_id, team_alias, organization_id, models, created_at, updated_at
            ) VALUES ($1, 'Other scope guard team', $2, ARRAY[]::text[], NOW(), NOW())
            """,
            other_team_id,
            other_organization_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_batch_file (
                file_id, purpose, filename, bytes, status,
                storage_backend, storage_key, created_by_organization_id, created_at
            ) VALUES (
                $1, 'batch', 'scope-guard.jsonl', 2, 'processed',
                'local', $2, $3, NOW()
            )
            """,
            input_file_id,
            f"scope-guard/{suffix}/input.jsonl",
            other_organization_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_batch_job (
                batch_id, endpoint, status, input_file_id,
                created_by_organization_id, created_at
            ) VALUES ($1, '/v1/chat/completions', 'queued', $2, $3, NOW())
            """,
            batch_id,
            input_file_id,
            other_organization_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_mcpserver (
                mcp_server_id, server_key, name, owner_scope_type,
                transport, base_url, forwarded_headers_allowlist, created_at, updated_at
            ) VALUES (
                $1, $2, 'Scope guard MCP', 'global', 'streamable_http',
                'https://mcp.example.com', ARRAY[]::text[], NOW(), NOW()
            )
            """,
            mcp_server_id,
            f"scope-guard-mcp-{suffix}",
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_teamtombstone (
                team_id, organization_id, deletion_job_id, deleted_at
            ) VALUES ($1, $2, $3, NOW())
            """,
            tombstoned_team_id,
            organization_id,
            f"scope-guard-job-{suffix}",
        )

        for scope_type, scope_id, expected_message in (
            ("organization", f"missing-org-{suffix}", "organization scope does not exist"),
            ("team", f"missing-team-{suffix}", "team scope does not exist"),
            ("api_key", f"missing-key-{suffix}", "api key scope does not exist"),
            ("team", tombstoned_team_id, "team scope is tombstoned"),
        ):
            with pytest.raises(Exception, match=expected_message):
                await db.execute_raw(
                    """
                    INSERT INTO deltallm_callabletargetbinding (
                        callable_target_binding_id, callable_key,
                        scope_type, scope_id, enabled, created_at, updated_at
                    ) VALUES ($1, $2, $3, $4, TRUE, NOW(), NOW())
                    """,
                    str(uuid4()),
                    f"scope-guard-{scope_type}-{suffix}",
                    scope_type,
                    scope_id,
                )

        with pytest.raises(Exception, match="created owner organization claims conflict"):
            await db.execute_raw(
                """
                INSERT INTO deltallm_batch_create_session (
                    session_id, target_batch_id, status, endpoint, input_file_id,
                    staged_storage_backend, staged_storage_key, staged_bytes,
                    expected_item_count, created_by_organization_id,
                    created_by_team_id, created_at
                ) VALUES (
                    $1, $2, 'staged', '/v1/chat/completions', $3,
                    'local', $4, 2, 1, $5, $6, NOW()
                )
                """,
                str(uuid4()),
                str(uuid4()),
                input_file_id,
                f"scope-guard/{suffix}/staged.jsonl",
                organization_id,
                other_team_id,
            )
        with pytest.raises(Exception, match="approval organization claims conflict"):
            await db.execute_raw(
                """
                INSERT INTO deltallm_mcpapprovalrequest (
                    mcp_approval_request_id, mcp_server_id, tool_name,
                    scope_type, scope_id, status, request_fingerprint,
                    organization_id, arguments_json, created_at, updated_at
                ) VALUES (
                    $1, $2, 'scope_guard_tool', 'team', $3, 'pending',
                    $4, $5, '{}'::jsonb, NOW(), NOW()
                )
                """,
                str(uuid4()),
                mcp_server_id,
                other_team_id,
                f"scope-guard-approval-{suffix}",
                organization_id,
            )
        with pytest.raises(Exception, match="webhook owner organization claims conflict"):
            await db.execute_raw(
                """
                INSERT INTO deltallm_batch_webhook_outbox (
                    event_id, batch_id, event_type, created_by_organization_id,
                    target_config_ciphertext, payload_json, payload_sha256,
                    status, created_at, updated_at
                ) VALUES (
                    $1, $2, 'batch.completed', $3, 'ciphertext', '{}'::jsonb,
                    $4, 'queued', NOW(), NOW()
                )
                """,
                str(uuid4()),
                batch_id,
                organization_id,
                "0" * 64,
            )
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_batch_job WHERE batch_id = $1",
            batch_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_batch_file WHERE file_id = $1",
            input_file_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_mcpserver WHERE mcp_server_id = $1",
            mcp_server_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_teamtombstone WHERE team_id = $1",
            tombstoned_team_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_teamtable WHERE team_id = $1",
            other_team_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id IN ($1, $2)",
            organization_id,
            other_organization_id,
        )
        await db.disconnect()


@pytest.mark.asyncio
async def test_database_rejects_parent_organization_delete_with_referenced_team() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    organization_id = f"org-parent-guard-{suffix}"
    team_id = f"team-parent-guard-{suffix}"
    try:
        await db.execute_raw(
            """
            INSERT INTO deltallm_organizationtable (
                organization_id, organization_name, created_at, updated_at
            ) VALUES ($1, 'Parent guard', NOW(), NOW())
            """,
            organization_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_teamtable (
                team_id, team_alias, organization_id, models, created_at, updated_at
            ) VALUES ($1, 'Referenced team', $2, ARRAY[]::text[], NOW(), NOW())
            """,
            team_id,
            organization_id,
        )

        with pytest.raises(Exception, match="organization still has referenced teams"):
            await db.execute_raw(
                "DELETE FROM deltallm_organizationtable WHERE organization_id = $1",
                organization_id,
            )
    finally:
        await db.execute_raw("DELETE FROM deltallm_teamtable WHERE team_id = $1", team_id)
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id = $1",
            organization_id,
        )
        await db.disconnect()


@pytest.mark.asyncio
async def test_preview_blocks_assets_referenced_outside_the_organization() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    organization_id = f"org-owner-{suffix}"
    external_organization_id = f"org-consumer-{suffix}"
    external_team_id = f"team-consumer-{suffix}"
    mcp_server_id = str(uuid4())
    prompt_template_id = str(uuid4())
    route_group_id = str(uuid4())
    route_group_key = f"owned-route-{suffix}"
    external_prompt_template_id = str(uuid4())
    external_prompt_version_id = str(uuid4())
    consumer_route_group_id = str(uuid4())
    try:
        await db.execute_raw(
            """
            INSERT INTO deltallm_organizationtable (
                organization_id, organization_name, created_at, updated_at
            ) VALUES
                ($1, 'Asset owner', NOW(), NOW()),
                ($2, 'Asset consumer', NOW(), NOW())
            """,
            organization_id,
            external_organization_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_teamtable (
                team_id, team_alias, organization_id, models, created_at, updated_at
            ) VALUES ($1, 'Consumer team', $2, ARRAY[]::text[], NOW(), NOW())
            """,
            external_team_id,
            external_organization_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_mcpserver (
                mcp_server_id, server_key, name, owner_scope_type, owner_scope_id,
                transport, base_url, forwarded_headers_allowlist, created_at, updated_at
            ) VALUES (
                $1, $2, 'Owned MCP', 'organization', $3,
                'streamable_http', 'https://mcp.example.com', ARRAY[]::text[], NOW(), NOW()
            )
            """,
            mcp_server_id,
            f"owned-mcp-{suffix}",
            organization_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_mcpbinding (
                mcp_binding_id, mcp_server_id, scope_type, scope_id,
                tool_allowlist, created_at, updated_at
            ) VALUES ($1, $2, 'team', $3, ARRAY[]::text[], NOW(), NOW())
            """,
            str(uuid4()),
            mcp_server_id,
            external_team_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_prompttemplate (
                prompt_template_id, template_key, name, owner_scope,
                metadata, created_at, updated_at
            ) VALUES (
                $1, $2, 'Owned prompt', 'organization',
                jsonb_build_object(
                    '_asset_governance',
                    jsonb_build_object('owner_scope_id', $3::text)
                ),
                NOW(), NOW()
            )
            """,
            prompt_template_id,
            f"owned-prompt-{suffix}",
            organization_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_promptbinding (
                prompt_binding_id, scope_type, scope_id, prompt_template_id,
                label, created_at, updated_at
            ) VALUES ($1, 'organization', $2, $3, 'production', NOW(), NOW())
            """,
            str(uuid4()),
            external_organization_id,
            prompt_template_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_routegroup (
                route_group_id, group_key, name, metadata, created_at, updated_at
            ) VALUES (
                $1, $2, 'Prompt consumer route group',
                jsonb_build_object(
                    'default_prompt',
                    jsonb_build_object('template_key', $3::text, 'label', 'production')
                ),
                NOW(), NOW()
            )
            """,
            consumer_route_group_id,
            f"prompt-consumer-route-{suffix}",
            f"owned-prompt-{suffix}",
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_routegroup (
                route_group_id, group_key, name, metadata, created_at, updated_at
            ) VALUES (
                $1, $2, 'Owned route group',
                jsonb_build_object(
                    '_asset_governance',
                    jsonb_build_object(
                        'owner_scope_type', 'organization',
                        'owner_scope_id', $3::text
                    )
                ),
                NOW(), NOW()
            )
            """,
            route_group_id,
            route_group_key,
            organization_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_routegroupbinding (
                route_group_binding_id, route_group_id, scope_type, scope_id,
                enabled, created_at, updated_at
            ) VALUES ($1, $2, 'team', $3, TRUE, NOW(), NOW())
            """,
            str(uuid4()),
            route_group_id,
            external_team_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_callabletargetbinding (
                callable_target_binding_id, callable_key, scope_type, scope_id,
                enabled, created_at, updated_at
            ) VALUES ($1, $2, 'team', $3, TRUE, NOW(), NOW())
            """,
            str(uuid4()),
            route_group_key,
            external_team_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_prompttemplate (
                prompt_template_id, template_key, name, owner_scope,
                created_at, updated_at
            ) VALUES ($1, $2, 'External prompt', 'global', NOW(), NOW())
            """,
            external_prompt_template_id,
            f"external-prompt-{suffix}",
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_promptversion (
                prompt_version_id, prompt_template_id, version, status,
                template_body, route_preferences, created_at, updated_at
            ) VALUES (
                $1, $2, 1, 'draft', '{}'::jsonb,
                jsonb_build_object('route_group', $3::text), NOW(), NOW()
            )
            """,
            external_prompt_version_id,
            external_prompt_template_id,
            route_group_key,
        )

        service = OrganizationDeletionService(
            repository=OrganizationDeletionRepository(db),
            cache_invalidation_service=None,
            requests_enabled=True,
        )
        preview = await service.preview(organization_id)

        assert preview.record.counts.external_mcp_dependencies == 1
        assert preview.record.counts.external_prompt_dependencies == 2
        assert preview.record.counts.owned_route_groups == 1
        assert preview.record.counts.external_route_group_dependencies == 3
        assert preview.can_request is False

        await db.execute_raw(
            "DELETE FROM deltallm_mcpbinding WHERE mcp_server_id = $1",
            mcp_server_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_promptbinding WHERE prompt_template_id = $1",
            prompt_template_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_routegroup WHERE route_group_id = $1",
            consumer_route_group_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_routegroupbinding WHERE route_group_id = $1",
            route_group_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_callabletargetbinding WHERE callable_key = $1",
            route_group_key,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_promptversion WHERE prompt_version_id = $1",
            external_prompt_version_id,
        )
        unblocked = await service.preview(organization_id)
        assert unblocked.can_request is True
        await service.request_deletion(
            organization_id=organization_id,
            confirmation_name="Asset owner",
            plan_token=unblocked.plan_token,
            idempotency_key=f"asset-owner-delete-{suffix}",
            requested_by_account_id=None,
        )

        with pytest.raises(Exception, match="asset owner organization is not active"):
            await db.execute_raw(
                """
                INSERT INTO deltallm_mcpbinding (
                    mcp_binding_id, mcp_server_id, scope_type, scope_id,
                    tool_allowlist, created_at, updated_at
                ) VALUES ($1, $2, 'team', $3, ARRAY[]::text[], NOW(), NOW())
                """,
                str(uuid4()),
                mcp_server_id,
                external_team_id,
            )
        with pytest.raises(Exception, match="asset owner organization is not active"):
            await db.execute_raw(
                """
                INSERT INTO deltallm_promptbinding (
                    prompt_binding_id, scope_type, scope_id, prompt_template_id,
                    label, created_at, updated_at
                ) VALUES ($1, 'organization', $2, $3, 'production', NOW(), NOW())
                """,
                str(uuid4()),
                external_organization_id,
                prompt_template_id,
            )
        with pytest.raises(Exception, match="asset owner organization is not active"):
            await db.execute_raw(
                """
                INSERT INTO deltallm_routegroup (
                    route_group_id, group_key, name, metadata, created_at, updated_at
                ) VALUES (
                    $1, $2, 'Late prompt consumer route group',
                    jsonb_build_object(
                        'default_prompt',
                        jsonb_build_object(
                            'template_key', $3::text,
                            'label', 'production'
                        )
                    ),
                    NOW(), NOW()
                )
                """,
                str(uuid4()),
                f"late-prompt-consumer-route-{suffix}",
                f"owned-prompt-{suffix}",
            )
        with pytest.raises(Exception, match="asset owner organization is not active"):
            await db.execute_raw(
                """
                INSERT INTO deltallm_routegroupbinding (
                    route_group_binding_id, route_group_id, scope_type, scope_id,
                    enabled, created_at, updated_at
                ) VALUES ($1, $2, 'team', $3, TRUE, NOW(), NOW())
                """,
                str(uuid4()),
                route_group_id,
                external_team_id,
            )
        with pytest.raises(Exception, match="asset owner organization is not active"):
            await db.execute_raw(
                """
                INSERT INTO deltallm_callabletargetbinding (
                    callable_target_binding_id, callable_key, scope_type, scope_id,
                    enabled, created_at, updated_at
                ) VALUES ($1, $2, 'team', $3, TRUE, NOW(), NOW())
                """,
                str(uuid4()),
                route_group_key,
                external_team_id,
            )
        with pytest.raises(Exception, match="asset owner organization is not active"):
            await db.execute_raw(
                """
                INSERT INTO deltallm_promptversion (
                    prompt_version_id, prompt_template_id, version, status,
                    template_body, route_preferences, created_at, updated_at
                ) VALUES (
                    $1, $2, 2, 'draft', '{}'::jsonb,
                    jsonb_build_object('route_group', $3::text), NOW(), NOW()
                )
                """,
                str(uuid4()),
                external_prompt_template_id,
                route_group_key,
            )

        cleanup_repository = OrganizationDeletionCleanupRepository(db)
        for _ in range(10):
            cleanup_page = await cleanup_repository.delete_owned_assets_page(
                organization_id,
                page_size=1,
            )
            if cleanup_page.processed == 0:
                break
        assert not await db.query_raw(
            "SELECT 1 FROM deltallm_mcpserver WHERE mcp_server_id = $1",
            mcp_server_id,
        )
        assert not await db.query_raw(
            "SELECT 1 FROM deltallm_prompttemplate WHERE prompt_template_id = $1",
            prompt_template_id,
        )
        assert not await db.query_raw(
            "SELECT 1 FROM deltallm_routegroup WHERE route_group_id = $1",
            route_group_id,
        )
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_mcpbinding WHERE mcp_server_id = $1",
            mcp_server_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_promptbinding WHERE prompt_template_id = $1",
            prompt_template_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_routegroupbinding WHERE route_group_id = $1",
            route_group_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_routegroup WHERE route_group_id = $1",
            consumer_route_group_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_callabletargetbinding WHERE callable_key = $1",
            route_group_key,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_promptversion WHERE prompt_template_id = $1",
            external_prompt_template_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_prompttemplate WHERE prompt_template_id = $1",
            external_prompt_template_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_routegroup WHERE route_group_id = $1",
            route_group_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_mcpserver WHERE mcp_server_id = $1",
            mcp_server_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_prompttemplate WHERE prompt_template_id = $1",
            prompt_template_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_cacheinvalidationoutbox WHERE scope_type = 'organization' AND scope_id = $1",
            organization_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_auditevent WHERE organization_id = $1",
            organization_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_organizationdeletionjob WHERE organization_id = $1",
            organization_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_teamtable WHERE team_id = $1",
            external_team_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id IN ($1, $2)",
            organization_id,
            external_organization_id,
        )
        await db.disconnect()
