from __future__ import annotations

import json
from uuid import uuid4

import pytest

from src.batch.repositories.webhook_outbox_repository import BatchWebhookOutboxRepository
from src.db.organization_deletion_final_inventory import (
    ORGANIZATION_DELETION_FINAL_INVENTORY_SQL,
)
from src.db.organization_deletion_invitation_cleanup import (
    OrganizationDeletionInvitationCleanup,
)
from src.db.organization_deletion_tenant_cleanup import OrganizationDeletionTenantCleanup
from tests.db.tier_migration_helpers import connect_prisma


@pytest.mark.asyncio
async def test_batch_ownership_is_snapshotted_and_inactive_webhooks_are_suppressed() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    organization_id = f"org-invariant-{suffix}"
    team_id = f"team-invariant-{suffix}"
    user_id = f"user-invariant-{suffix}"
    api_key = f"key-invariant-{suffix}"
    file_id = str(uuid4())
    batch_id = str(uuid4())
    session_id = str(uuid4())
    retrying_event_id = str(uuid4())
    suppressed_event_id = str(uuid4())
    try:
        await db.execute_raw(
            """
            INSERT INTO deltallm_organizationtable (
                organization_id, organization_name, created_at, updated_at
            ) VALUES ($1, 'Invariant tenant', NOW(), NOW())
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
            INSERT INTO deltallm_usertable (
                user_id, team_id, models, created_at, updated_at
            ) VALUES ($1, $2, ARRAY[]::text[], NOW(), NOW())
            """,
            user_id,
            team_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_verificationtoken (
                token, team_id, models, created_at, updated_at
            ) VALUES ($1, $2, ARRAY[]::text[], NOW(), NOW())
            """,
            api_key,
            team_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_batch_file (
                file_id, purpose, filename, bytes, storage_backend,
                storage_key, created_at
            ) VALUES ($1, 'batch', 'input.jsonl', 2, 'local', $2, NOW())
            """,
            file_id,
            f"invariant/{suffix}/input.jsonl",
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_batch_job (
                batch_id, endpoint, status, input_file_id,
                created_by_api_key, created_at
            ) VALUES ($1, '/v1/chat/completions', 'queued', $2, $3, NOW())
            """,
            batch_id,
            file_id,
            api_key,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_batch_create_session (
                session_id, target_batch_id, status, endpoint, input_file_id,
                staged_storage_backend, staged_storage_key, staged_bytes,
                expected_item_count, created_by_user_id, created_at
            ) VALUES (
                $1, $2, 'staged', '/v1/chat/completions', $3,
                'local', $4, 2, 1, $5, NOW()
            )
            """,
            session_id,
            str(uuid4()),
            file_id,
            f"invariant/{suffix}/staged.jsonl",
            user_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_batch_webhook_outbox (
                event_id, batch_id, event_type, target_config_ciphertext,
                payload_json, payload_sha256, status, next_attempt_at,
                created_at, updated_at
            ) VALUES (
                $1, $2, 'batch.failed', 'ciphertext', '{}'::jsonb,
                $3, 'retrying', NOW(), NOW(), NOW()
            )
            """,
            retrying_event_id,
            batch_id,
            "0" * 64,
        )

        ownership = await db.query_raw(
            """
            SELECT j.created_by_organization_id AS job_organization_id,
                   s.created_by_organization_id AS session_organization_id,
                   w.created_by_organization_id AS webhook_organization_id
            FROM deltallm_batch_job j
            JOIN deltallm_batch_create_session s ON s.session_id = $2
            JOIN deltallm_batch_webhook_outbox w ON w.event_id = $3
            WHERE j.batch_id = $1
            """,
            batch_id,
            session_id,
            retrying_event_id,
        )
        assert dict(ownership[0]) == {
            "job_organization_id": organization_id,
            "session_organization_id": organization_id,
            "webhook_organization_id": organization_id,
        }

        await db.execute_raw(
            """
            UPDATE deltallm_organizationtable
            SET lifecycle_state = 'deletion_pending',
                lifecycle_version = lifecycle_version + 1,
                deletion_requested_at = NOW(),
                deletion_not_before_at = NOW(),
                deletion_job_id = $2,
                updated_at = NOW()
            WHERE organization_id = $1
            """,
            organization_id,
            f"job-{suffix}",
        )

        inventory = await db.query_raw(
            ORGANIZATION_DELETION_FINAL_INVENTORY_SQL,
            organization_id,
        )
        assert inventory[0]["webhook_deliveries"] is True

        async with db.tx() as tx:
            await tx.execute_raw(
                """
                UPDATE deltallm_batch_job
                SET status = 'cancelled', status_last_updated_at = NOW()
                WHERE batch_id = $1
                """,
                batch_id,
            )
            await tx.execute_raw(
                """
                INSERT INTO deltallm_batch_webhook_outbox (
                    event_id, batch_id, event_type, target_config_ciphertext,
                    payload_json, payload_sha256, status, next_attempt_at,
                    created_at, updated_at
                ) VALUES (
                    $1, $2, 'batch.cancelled', 'ciphertext', '{}'::jsonb,
                    $3, 'queued', NOW(), NOW(), NOW()
                )
                """,
                suppressed_event_id,
                batch_id,
                "1" * 64,
            )
        await OrganizationDeletionTenantCleanup(db).cancel_webhook_deliveries(
            organization_id,
            limit=10,
        )

        webhook_rows = await db.query_raw(
            """
            SELECT event_id, status, last_error, created_by_organization_id
            FROM deltallm_batch_webhook_outbox
            WHERE event_id IN ($1, $2)
            ORDER BY event_id
            """,
            retrying_event_id,
            suppressed_event_id,
        )
        assert len(webhook_rows) == 2
        assert all(row["status"] == "failed" for row in webhook_rows)
        assert all(row["last_error"] == "organization_deletion_requested" for row in webhook_rows)
        assert all(row["created_by_organization_id"] == organization_id for row in webhook_rows)
        claimed = await BatchWebhookOutboxRepository(db).claim_due(
            worker_id="invariant-worker",
            lease_seconds=30,
            limit=10,
        )
        assert {row.event_id for row in claimed}.isdisjoint(
            {retrying_event_id, suppressed_event_id}
        )
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_batch_webhook_outbox WHERE event_id IN ($1, $2)",
            retrying_event_id,
            suppressed_event_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_batch_create_session WHERE session_id = $1",
            session_id,
        )
        await db.execute_raw("DELETE FROM deltallm_batch_job WHERE batch_id = $1", batch_id)
        await db.execute_raw("DELETE FROM deltallm_batch_file WHERE file_id = $1", file_id)
        await db.execute_raw("DELETE FROM deltallm_verificationtoken WHERE token = $1", api_key)
        await db.execute_raw("DELETE FROM deltallm_usertable WHERE user_id = $1", user_id)
        await db.execute_raw("DELETE FROM deltallm_teamtable WHERE team_id = $1", team_id)
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id = $1",
            organization_id,
        )
        await db.disconnect()


@pytest.mark.asyncio
async def test_shared_invitation_scopes_can_be_removed_for_two_inactive_organizations() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    organization_a = f"org-invite-a-{suffix}"
    organization_b = f"org-invite-b-{suffix}"
    account_id = str(uuid4())
    invitation_id = str(uuid4())
    metadata = {
        "organization_invites": [
            {"organization_id": organization_a, "role": "org_member"},
            {"organization_id": organization_b, "role": "org_member"},
        ],
        "team_invites": [],
        "preserved": "metadata",
    }
    try:
        await db.execute_raw(
            """
            INSERT INTO deltallm_organizationtable (
                organization_id, organization_name, created_at, updated_at
            ) VALUES
                ($1, 'Invitation tenant A', NOW(), NOW()),
                ($2, 'Invitation tenant B', NOW(), NOW())
            """,
            organization_a,
            organization_b,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_platformaccount (
                account_id, email, created_at, updated_at
            ) VALUES ($1, $2, NOW(), NOW())
            """,
            account_id,
            f"invite-{suffix}@example.com",
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_platforminvitation (
                invitation_id, account_id, email, status, invite_scope_type,
                expires_at, metadata, created_at, updated_at
            ) VALUES (
                $1, $2, $3, 'pending', 'organization',
                NOW() + INTERVAL '1 day', $4::jsonb, NOW(), NOW()
            )
            """,
            invitation_id,
            account_id,
            f"invite-{suffix}@example.com",
            json.dumps(metadata),
        )
        await db.execute_raw(
            """
            UPDATE deltallm_organizationtable
            SET lifecycle_state = 'deletion_pending',
                lifecycle_version = lifecycle_version + 1,
                deletion_requested_at = NOW(), deletion_not_before_at = NOW(),
                deletion_job_id = 'invitation-' || organization_id,
                updated_at = NOW()
            WHERE organization_id IN ($1, $2)
            """,
            organization_a,
            organization_b,
        )

        async with db.tx() as tx:
            assert (
                await OrganizationDeletionInvitationCleanup(tx).clean_page(
                    organization_a,
                    page_size=10,
                )
                == 1
            )
        remaining = await db.query_raw(
            "SELECT metadata, status FROM deltallm_platforminvitation WHERE invitation_id = $1",
            invitation_id,
        )
        remaining_metadata = remaining[0]["metadata"]
        assert remaining[0]["status"] == "pending"
        assert remaining_metadata["preserved"] == "metadata"
        assert remaining_metadata["organization_invites"] == [
            {"organization_id": organization_b, "role": "org_member"}
        ]

        changed_metadata = dict(remaining_metadata)
        changed_metadata["preserved"] = "changed"
        with pytest.raises(Exception, match="organization is not active"):
            await db.execute_raw(
                """
                UPDATE deltallm_platforminvitation
                SET metadata = $2::jsonb, updated_at = NOW()
                WHERE invitation_id = $1
                """,
                invitation_id,
                json.dumps(changed_metadata),
            )

        async with db.tx() as tx:
            assert (
                await OrganizationDeletionInvitationCleanup(tx).clean_page(
                    organization_b,
                    page_size=10,
                )
                == 1
            )
        cancelled = await db.query_raw(
            "SELECT metadata, status FROM deltallm_platforminvitation WHERE invitation_id = $1",
            invitation_id,
        )
        assert cancelled[0]["status"] == "cancelled"
        assert cancelled[0]["metadata"]["preserved"] == "metadata"
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_platforminvitation WHERE invitation_id = $1",
            invitation_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_platformaccount WHERE account_id = $1",
            account_id,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id IN ($1, $2)",
            organization_a,
            organization_b,
        )
        await db.disconnect()
