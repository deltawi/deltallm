from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.db.external_auth_records import (
    ExternalBindingRecord,
    ExternalParentRecord,
    ExternalSubjectRecord,
)
from src.db.platform_accounts import PlatformAccountDatabase
from src.db.platform_sessions import PlatformSessionRecord


@dataclass(frozen=True, slots=True)
class ExternalSessionRecord:
    session: PlatformSessionRecord
    integration_id: str
    binding_id: str
    subject_id: str
    organization_id: str
    team_id: str
    runtime_user_id: str
    parent_id: str
    generation: int
    organization_role: str
    team_role: str


@dataclass(frozen=True, slots=True)
class ExternalIssuedSession:
    generation: int
    mfa_verified: bool


class ExternalSessionRepository:
    def __init__(self, db: PlatformAccountDatabase) -> None:
        self.db = db

    async def lock_parent(
        self,
        *,
        integration_id: str,
        parent_hash: str,
        subject_id: str,
        auth_time: datetime,
        expires_at: datetime,
        revoked: bool = False,
    ) -> ExternalParentRecord:
        rows = await self.db.query_raw(
            """
            INSERT INTO deltallm_externalauthparentsession (
                parent_id, integration_id, external_session_id_hash, subject_id,
                auth_time, expires_at, revoked_at, updated_at)
            VALUES (gen_random_uuid(), $1, $2, $3, $4::timestamptz, $5::timestamptz,
                CASE WHEN $6 THEN NOW() ELSE NULL END, NOW())
            ON CONFLICT (integration_id, external_session_id_hash)
            DO UPDATE SET updated_at = deltallm_externalauthparentsession.updated_at RETURNING *
            """,
            integration_id,
            parent_hash,
            subject_id,
            auth_time,
            expires_at,
            revoked,
        )
        return ExternalParentRecord.model_validate(rows[0])

    async def issue(
        self,
        *,
        parent: ExternalParentRecord,
        subject: ExternalSubjectRecord,
        binding: ExternalBindingRecord,
        integration_epoch: int,
        token_hash: str,
        expires_at: datetime,
        mfa_verified: bool,
        overlap_seconds: int,
    ) -> ExternalIssuedSession:
        rows = await self.db.query_raw(
            """
            WITH mfa_proof AS MATERIALIZED (
                SELECT previous.external_mfa_secret_digest FROM deltallm_platformsession previous
                JOIN deltallm_platformaccount account ON account.account_id = previous.account_id
                WHERE previous.external_parent_id = $1 AND previous.account_id = $2
                  AND previous.revoked_at IS NULL AND previous.expires_at > NOW() AND previous.mfa_verified
                  AND previous.external_integration_epoch = $6 AND previous.external_binding_epoch = $7
                  AND previous.external_subject_epoch = $8 AND account.mfa_enabled
                  AND previous.external_mfa_secret_digest = encode(digest(account.mfa_secret, 'sha256'), 'hex')
                ORDER BY previous.external_generation DESC LIMIT 1
            ), advanced AS (
                UPDATE deltallm_externalauthparentsession SET generation = generation + 1, updated_at = NOW()
                WHERE parent_id = $1 AND revoked_at IS NULL AND expires_at > NOW() RETURNING generation
            ), retired AS (
                UPDATE deltallm_platformsession child SET
                    expires_at = CASE WHEN child.external_generation = advanced.generation - 1
                        THEN LEAST(child.expires_at, NOW() + make_interval(secs => $10)) ELSE child.expires_at END,
                    revoked_at = CASE WHEN child.external_generation < advanced.generation - 1
                        THEN NOW() ELSE child.revoked_at END,
                    updated_at = NOW()
                FROM advanced WHERE child.external_parent_id = $1 AND child.revoked_at IS NULL
                RETURNING child.session_id
            ) INSERT INTO deltallm_platformsession (
                session_id, account_id, session_token_hash, mfa_verified, expires_at, updated_at,
                external_parent_id, external_generation, external_integration_epoch, external_binding_epoch, external_subject_epoch, external_mfa_secret_digest)
            SELECT gen_random_uuid(), $2, $3, ($4 OR EXISTS (SELECT 1 FROM mfa_proof)), $5::timestamptz, NOW(),
                $1, advanced.generation, $6, $7, $8, (SELECT external_mfa_secret_digest FROM mfa_proof)
            FROM advanced CROSS JOIN (SELECT count(*) FROM retired) AS retirement
            WHERE $9::bool RETURNING external_generation, mfa_verified
            """,
            parent.parent_id,
            subject.account_id,
            token_hash,
            mfa_verified,
            expires_at,
            integration_epoch,
            binding.epoch,
            subject.epoch,
            True,
            overlap_seconds,
        )
        if not rows:
            raise ValueError("External parent is revoked or expired")
        return ExternalIssuedSession(
            int(rows[0]["external_generation"]), rows[0]["mfa_verified"] is True
        )

    async def revoke_parent(self, parent_id: str) -> None:
        await self.db.execute_raw(
            """
            WITH revoked AS (
                UPDATE deltallm_externalauthparentsession SET revoked_at = COALESCE(revoked_at, NOW()), updated_at = NOW()
                WHERE parent_id = $1 RETURNING parent_id
            ) UPDATE deltallm_platformsession SET revoked_at = COALESCE(revoked_at, NOW()), updated_at = NOW()
            WHERE external_parent_id IN (SELECT parent_id FROM revoked)
            """,
            parent_id,
        )

    async def revoke_scope(
        self, *, integration_id: str, binding_id: str | None = None, subject_id: str | None = None
    ) -> None:
        await self.db.execute_raw(
            """
            WITH revoked AS (
                UPDATE deltallm_externalauthparentsession p SET revoked_at = COALESCE(p.revoked_at, NOW()), updated_at = NOW()
                FROM deltallm_externalauthsubject s WHERE p.subject_id = s.subject_id
                  AND p.integration_id = $1 AND ($2::text IS NULL OR s.binding_id = $2)
                  AND ($3::text IS NULL OR s.subject_id = $3) RETURNING p.parent_id
            ) UPDATE deltallm_platformsession SET revoked_at = COALESCE(revoked_at, NOW()), updated_at = NOW()
            WHERE external_parent_id IN (SELECT parent_id FROM revoked)
            """,
            integration_id,
            binding_id,
            subject_id,
        )

    async def get_active(self, token_hash: str) -> ExternalSessionRecord | None:
        rows = await self.db.query_raw(
            """
            SELECT child.account_id,
                CASE WHEN NOT a.mfa_enabled THEN child.mfa_verified ELSE
                    child.mfa_verified AND child.external_mfa_secret_digest = encode(digest(a.mfa_secret, 'sha256'), 'hex')
                END AS mfa_verified, child.expires_at, child.external_generation,
                a.email, a.role, a.is_active, a.mfa_enabled, a.force_password_change,
                p.parent_id, s.subject_id, s.runtime_user_id, b.binding_id, b.integration_id,
                b.organization_id, b.team_id, om.role AS organization_role, tm.role AS team_role
            FROM deltallm_platformsession child
            JOIN deltallm_platformaccount a ON a.account_id = child.account_id
            JOIN deltallm_externalauthparentsession p ON p.parent_id = child.external_parent_id
            JOIN deltallm_externalauthsubject s ON s.subject_id = p.subject_id AND s.account_id = a.account_id
            JOIN deltallm_externalauthbinding b ON b.binding_id = s.binding_id
            JOIN deltallm_externalauthintegration i ON i.integration_id = b.integration_id
            JOIN deltallm_organizationtable o ON o.organization_id = b.organization_id
            JOIN deltallm_teamtable t ON t.team_id = b.team_id AND t.organization_id = o.organization_id
            JOIN deltallm_usertable u ON u.user_id = s.runtime_user_id AND u.team_id = b.team_id
            JOIN deltallm_organizationmembership om ON om.account_id = a.account_id AND om.organization_id = b.organization_id
            JOIN deltallm_teammembership tm ON tm.account_id = a.account_id AND tm.team_id = b.team_id
            WHERE child.session_token_hash = $1 AND child.revoked_at IS NULL AND child.expires_at > NOW()
              AND a.is_active AND a.role = 'org_user' AND i.enabled AND b.state = 'active' AND s.state = 'active'
              AND o.lifecycle_state = 'active' AND p.revoked_at IS NULL AND p.expires_at > NOW()
              AND child.external_generation IN (p.generation, p.generation - 1)
              AND child.external_integration_epoch = i.epoch AND child.external_binding_epoch = b.epoch
              AND child.external_subject_epoch = s.epoch
              AND NOT EXISTS (SELECT 1 FROM deltallm_organizationmembership other WHERE other.account_id = a.account_id AND other.organization_id <> b.organization_id)
              AND NOT EXISTS (SELECT 1 FROM deltallm_teammembership other WHERE other.account_id = a.account_id AND other.team_id <> b.team_id)
            LIMIT 1
            """,
            token_hash,
        )
        if not rows:
            return None
        row = rows[0]
        return ExternalSessionRecord(
            session=PlatformSessionRecord.from_row(row),
            integration_id=str(row["integration_id"]),
            binding_id=str(row["binding_id"]),
            subject_id=str(row["subject_id"]),
            organization_id=str(row["organization_id"]),
            team_id=str(row["team_id"]),
            runtime_user_id=str(row["runtime_user_id"]),
            parent_id=str(row["parent_id"]),
            generation=int(row["external_generation"]),
            organization_role=str(row["organization_role"]),
            team_role=str(row["team_role"]),
        )

    async def mark_mfa_verified(self, token_hash: str, expected_secret_digest: str) -> bool:
        rows = await self.db.query_raw(
            """UPDATE deltallm_platformsession child
            SET mfa_verified = true, external_mfa_secret_digest = $2, updated_at = NOW()
            FROM deltallm_platformaccount account
            WHERE child.session_token_hash = $1 AND child.account_id = account.account_id
              AND child.external_parent_id IS NOT NULL AND child.revoked_at IS NULL AND child.expires_at > NOW()
              AND account.mfa_enabled AND account.is_active
              AND encode(digest(account.mfa_secret, 'sha256'), 'hex') = $2
            RETURNING child.session_id""",
            token_hash,
            expected_secret_digest,
        )
        return bool(rows)
