from __future__ import annotations

from dataclasses import dataclass

from src.db.external_auth_records import ExternalBindingRecord, ExternalSubjectRecord
from src.db.platform_accounts import PlatformAccountDatabase


@dataclass(frozen=True, slots=True)
class ExternalAccountState:
    account_id: str
    identity_id: str
    runtime_user_id: str
    mfa_enabled: bool
    force_password_change: bool


class ExternalProvisioningRepository:
    def __init__(self, db: PlatformAccountDatabase) -> None:
        self.db = db

    async def finish_new_account(
        self,
        *,
        subject: ExternalSubjectRecord,
        binding: ExternalBindingRecord,
        account_id: str,
        email: str,
        provider: str,
    ) -> ExternalSubjectRecord | None:
        rows = await self.db.query_raw(
            """
            WITH runtime_created AS (
                INSERT INTO deltallm_usertable (user_id, user_email, user_role, team_id, models, updated_at)
                SELECT $1, $2, 'internal_user', $3, ARRAY[]::text[], NOW() WHERE $4::text IS NULL
                ON CONFLICT DO NOTHING RETURNING user_id
            ), runtime_identity AS MATERIALIZED (
                SELECT user_id FROM runtime_created UNION ALL
                SELECT user_id FROM deltallm_usertable WHERE user_id = $4 AND team_id = $3
            ), org_membership AS (
                INSERT INTO deltallm_organizationmembership (membership_id, account_id, organization_id, role, updated_at)
                SELECT gen_random_uuid(), $1, $5, 'org_member', NOW() FROM runtime_identity
                ON CONFLICT DO NOTHING RETURNING membership_id
            ), team_membership AS (
                INSERT INTO deltallm_teammembership (membership_id, account_id, team_id, role, updated_at)
                SELECT gen_random_uuid(), $1, $3, 'team_developer', NOW() FROM runtime_identity
                ON CONFLICT DO NOTHING RETURNING membership_id
            ) UPDATE deltallm_externalauthsubject s SET account_id = $1, identity_id = i.identity_id,
                runtime_user_id = r.user_id, state = 'active', version = version + 1, updated_at = NOW()
            FROM runtime_identity r, deltallm_platformidentity i,
                (SELECT count(*) FROM org_membership) om, (SELECT count(*) FROM team_membership) tm
            WHERE s.subject_id = $6 AND s.state = 'pending' AND i.account_id = $1
                AND i.provider = $7 AND i.subject = s.subject RETURNING s.*
            """,
            account_id,
            email,
            binding.team_id,
            subject.runtime_user_id,
            binding.organization_id,
            subject.subject_id,
            provider,
        )
        return ExternalSubjectRecord.model_validate(rows[0]) if rows else None

    async def eligible_account(
        self, subject: ExternalSubjectRecord, binding: ExternalBindingRecord
    ) -> ExternalAccountState | None:
        return await self.eligible_target(
            account_id=subject.account_id or "",
            runtime_user_id=subject.runtime_user_id or "",
            binding=binding,
            identity_id=subject.identity_id,
        )

    async def eligible_target(
        self,
        *,
        account_id: str,
        runtime_user_id: str,
        binding: ExternalBindingRecord,
        identity_id: str | None = None,
        require_email: str | None = None,
        check_assets: bool = False,
    ) -> ExternalAccountState | None:
        rows = await self.db.query_raw(
            """
            SELECT a.account_id, COALESCE($5::text, '') AS identity_id, u.user_id, a.mfa_enabled, a.force_password_change
            FROM deltallm_platformaccount a JOIN deltallm_usertable u ON u.user_id = $2 AND u.team_id = $3
            JOIN deltallm_organizationmembership om ON om.account_id = a.account_id AND om.organization_id = $4
            JOIN deltallm_teammembership tm ON tm.account_id = a.account_id AND tm.team_id = $3
            WHERE a.account_id = $1 AND a.is_active AND a.role = 'org_user'
              AND ($6::text IS NULL OR lower(a.email) = lower($6))
              AND ($5::text IS NULL OR EXISTS (SELECT 1 FROM deltallm_platformidentity i WHERE i.identity_id = $5 AND i.account_id = a.account_id))
              AND NOT EXISTS (SELECT 1 FROM deltallm_organizationmembership m WHERE m.account_id = a.account_id AND m.organization_id <> $4)
              AND NOT EXISTS (SELECT 1 FROM deltallm_teammembership m WHERE m.account_id = a.account_id AND m.team_id <> $3)
              AND (NOT $7::bool OR NOT EXISTS (
                SELECT 1 FROM deltallm_managedasset asset JOIN deltallm_assetgrant g ON g.managed_asset_id = asset.asset_id
                WHERE asset.owner_account_id = a.account_id AND (g.subject_type NOT IN ('team', 'organization')
                    OR (g.subject_type = 'team' AND g.team_id <> $3)
                    OR (g.subject_type = 'organization' AND g.organization_id <> $4))
              ))
              AND (NOT $7::bool OR NOT EXISTS (
                SELECT 1 FROM deltallm_verificationtoken key
                WHERE key.owner_account_id = a.account_id AND (
                    key.team_id IS DISTINCT FROM $3 OR key.user_id IS DISTINCT FROM $2
                    OR key.owner_service_account_id IS NOT NULL)
              ))
            FOR UPDATE OF a
            """,
            account_id,
            runtime_user_id,
            binding.team_id,
            binding.organization_id,
            identity_id,
            require_email,
            check_assets,
        )
        if not rows:
            return None
        row = rows[0]
        return ExternalAccountState(
            str(row["account_id"]),
            str(row["identity_id"]),
            str(row["user_id"]),
            row.get("mfa_enabled") is True,
            row.get("force_password_change") is True,
        )

    async def identity_id(self, *, provider: str, subject: str, account_id: str) -> str | None:
        rows = await self.db.query_raw(
            "SELECT identity_id FROM deltallm_platformidentity WHERE provider = $1 AND subject = $2 AND account_id = $3",
            provider,
            subject,
            account_id,
        )
        return str(rows[0]["identity_id"]) if rows else None

    async def lock_runtime_user(self, runtime_user_id: str, team_id: str) -> bool:
        rows = await self.db.query_raw(
            "SELECT user_id FROM deltallm_usertable WHERE user_id = $1 AND team_id = $2 FOR SHARE",
            runtime_user_id,
            team_id,
        )
        return bool(rows)
