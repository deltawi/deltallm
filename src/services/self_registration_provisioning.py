from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from src.auth.roles import OrganizationRole, PlatformRole
from src.config import SelfRegistrationSettings
from src.services.platform_identity_service import PlatformIdentityService


_SELF_REGISTRATION_SOURCE = "self_registration"
_ACCOUNT_METADATA_KEY = "self_registration"


@dataclass(frozen=True)
class SelfRegistrationProvisioningResult:
    account_id: str
    email: str
    organization_id: str
    team_id: str
    user_id: str
    team_role: str
    account_is_active: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "email": self.email,
            "organization_id": self.organization_id,
            "team_id": self.team_id,
            "user_id": self.user_id,
            "team_role": self.team_role,
            "account_is_active": self.account_is_active,
        }


@dataclass(frozen=True)
class SelfRegistrationSSOLoginResult:
    provisioning: SelfRegistrationProvisioningResult
    login: Any


class SelfRegistrationProvisioningService:
    def __init__(
        self,
        *,
        db_client: Any,
        platform_identity_service: PlatformIdentityService,
    ) -> None:
        self.db = db_client
        self.platform_identity_service = platform_identity_service

    async def provision_from_defaults(
        self,
        *,
        email: str,
        settings: SelfRegistrationSettings,
        is_active: bool = True,
    ) -> SelfRegistrationProvisioningResult:
        if not settings.enabled:
            raise ValueError("self-registration is disabled")
        if self.db is None:
            raise RuntimeError("database is required for self-registration provisioning")

        normalized_email = self.platform_identity_service.normalize_email(email)
        if not normalized_email:
            raise ValueError("email is required")

        if hasattr(self.db, "tx"):
            async with self.db.tx() as tx:
                identity_service = self.platform_identity_service.with_db(tx)
                return await self._provision_with_dependencies(
                    db_client=tx,
                    identity_service=identity_service,
                    email=normalized_email,
                    settings=settings,
                    is_active=is_active,
                )

        return await self._provision_with_dependencies(
            db_client=self.db,
            identity_service=self.platform_identity_service,
            email=normalized_email,
            settings=settings,
            is_active=is_active,
        )

    async def provision_sso_from_defaults(
        self,
        *,
        email: str,
        settings: SelfRegistrationSettings,
        provider: str,
        subject: str,
        is_active: bool = True,
    ) -> SelfRegistrationSSOLoginResult:
        if not settings.enabled:
            raise ValueError("self-registration is disabled")
        if self.db is None:
            raise RuntimeError("database is required for self-registration provisioning")

        normalized_email = self.platform_identity_service.normalize_email(email)
        normalized_provider = str(provider or "sso").strip() or "sso"
        normalized_subject = str(subject or normalized_email).strip()
        if not normalized_email:
            raise ValueError("email is required")
        if not normalized_subject:
            raise ValueError("subject is required")

        if not hasattr(self.db, "tx"):
            raise RuntimeError("database transactions are required for SSO self-registration provisioning")

        async with self.db.tx() as tx:
            identity_service = self.platform_identity_service.with_db(tx)
            return await self._provision_sso_with_dependencies(
                db_client=tx,
                identity_service=identity_service,
                email=normalized_email,
                settings=settings,
                provider=normalized_provider,
                subject=normalized_subject,
                is_active=is_active,
            )

    async def _provision_sso_with_dependencies(
        self,
        *,
        db_client: Any,
        identity_service: PlatformIdentityService,
        email: str,
        settings: SelfRegistrationSettings,
        provider: str,
        subject: str,
        is_active: bool,
    ) -> SelfRegistrationSSOLoginResult:
        provisioning = await self._provision_with_dependencies(
            db_client=db_client,
            identity_service=identity_service,
            email=email,
            settings=settings,
            is_active=is_active,
        )
        await identity_service.link_sso_identity(
            account_id=provisioning.account_id,
            email=email,
            provider=provider,
            subject=subject,
        )
        login = await identity_service.create_login_result_for_account(provisioning.account_id)
        if login is None:
            raise RuntimeError("failed to establish self-registration session")
        await identity_service.mark_last_login(provisioning.account_id)
        return SelfRegistrationSSOLoginResult(provisioning=provisioning, login=login)

    async def _provision_with_dependencies(
        self,
        *,
        db_client: Any,
        identity_service: PlatformIdentityService,
        email: str,
        settings: SelfRegistrationSettings,
        is_active: bool,
    ) -> SelfRegistrationProvisioningResult:
        organization_id = str(settings.default_org.id or "").strip()
        team_id = str(settings.default_team.id or "").strip()
        if not organization_id or not team_id:
            raise ValueError("default organization and team are required")

        await self._ensure_team_can_belong_to_org(
            db_client=db_client,
            team_id=team_id,
            organization_id=organization_id,
        )
        await self._insert_default_org(db_client, settings=settings)
        await self._insert_default_team(db_client, settings=settings)

        account = await identity_service.ensure_account(
            email=email,
            role=PlatformRole.ORG_USER,
            is_active=is_active,
        )
        account_id = str(account.get("account_id") or "").strip()
        if not account_id:
            raise RuntimeError("failed to provision account")

        user_id = await self._ensure_runtime_user(
            db_client,
            account_id=account_id,
            email=email,
            team_id=team_id,
            settings=settings,
        )
        await self._insert_org_membership(
            db_client,
            account_id=account_id,
            organization_id=organization_id,
        )
        await self._insert_team_membership(
            db_client,
            account_id=account_id,
            team_id=team_id,
            role=settings.default_team.role,
        )
        await self._mark_account_self_registered(
            db_client,
            account_id=account_id,
            organization_id=organization_id,
            team_id=team_id,
        )

        return SelfRegistrationProvisioningResult(
            account_id=account_id,
            email=str(account.get("email") or email),
            organization_id=organization_id,
            team_id=team_id,
            user_id=user_id,
            team_role=settings.default_team.role,
            account_is_active=bool(account.get("is_active", is_active)),
        )

    async def _ensure_team_can_belong_to_org(
        self,
        *,
        db_client: Any,
        team_id: str,
        organization_id: str,
    ) -> None:
        rows = await db_client.query_raw(
            """
            SELECT team_id, organization_id
            FROM deltallm_teamtable
            WHERE team_id = $1
            LIMIT 1
            """,
            team_id,
        )
        if not rows:
            return
        existing_org_id = str(rows[0].get("organization_id") or "").strip()
        if existing_org_id != organization_id:
            raise ValueError("default team already belongs to a different organization")

    async def _insert_default_org(self, db_client: Any, *, settings: SelfRegistrationSettings) -> None:
        default_org = settings.default_org
        await db_client.execute_raw(
            """
            INSERT INTO deltallm_organizationtable (
                id,
                organization_id,
                organization_name,
                max_budget,
                soft_budget,
                spend,
                rpm_limit,
                tpm_limit,
                rph_limit,
                rpd_limit,
                tpd_limit,
                metadata,
                created_at,
                updated_at
            )
            VALUES (gen_random_uuid(), $1, $2, $3, $4, 0, $5, $6, $7, $8, $9, $10::jsonb, NOW(), NOW())
            ON CONFLICT (organization_id) DO NOTHING
            """,
            default_org.id,
            default_org.name,
            default_org.max_budget,
            default_org.soft_budget,
            default_org.rpm_limit,
            default_org.tpm_limit,
            default_org.rph_limit,
            default_org.rpd_limit,
            default_org.tpd_limit,
            self._metadata_json("organization"),
        )

    async def _insert_default_team(self, db_client: Any, *, settings: SelfRegistrationSettings) -> None:
        default_team = settings.default_team
        await db_client.execute_raw(
            """
            INSERT INTO deltallm_teamtable (
                team_id,
                team_alias,
                organization_id,
                max_budget,
                soft_budget,
                spend,
                rpm_limit,
                tpm_limit,
                rph_limit,
                rpd_limit,
                tpd_limit,
                blocked,
                metadata,
                self_service_keys_enabled,
                self_service_max_keys_per_user,
                self_service_budget_ceiling,
                self_service_require_expiry,
                self_service_max_expiry_days,
                created_at,
                updated_at
            )
            VALUES ($1, $2, $3, $4, $5, 0, $6, $7, $8, $9, $10, false, $11::jsonb, $12, $13, $14, $15, $16, NOW(), NOW())
            ON CONFLICT (team_id) DO NOTHING
            """,
            default_team.id,
            default_team.alias,
            settings.default_org.id,
            default_team.max_budget,
            default_team.soft_budget,
            default_team.rpm_limit,
            default_team.tpm_limit,
            default_team.rph_limit,
            default_team.rpd_limit,
            default_team.tpd_limit,
            self._metadata_json("team"),
            default_team.self_service_keys_enabled,
            default_team.self_service_max_keys_per_user,
            default_team.self_service_budget_ceiling,
            default_team.self_service_require_expiry,
            default_team.self_service_max_expiry_days,
        )

    async def _insert_org_membership(
        self,
        db_client: Any,
        *,
        account_id: str,
        organization_id: str,
    ) -> None:
        await db_client.execute_raw(
            """
            INSERT INTO deltallm_organizationmembership (
                membership_id, account_id, organization_id, role, created_at, updated_at
            )
            VALUES (gen_random_uuid(), $1, $2, $3, NOW(), NOW())
            ON CONFLICT (account_id, organization_id) DO NOTHING
            """,
            account_id,
            organization_id,
            OrganizationRole.MEMBER,
        )

    async def _insert_team_membership(
        self,
        db_client: Any,
        *,
        account_id: str,
        team_id: str,
        role: str,
    ) -> None:
        await db_client.execute_raw(
            """
            INSERT INTO deltallm_teammembership (
                membership_id, account_id, team_id, role, created_at, updated_at
            )
            VALUES (gen_random_uuid(), $1, $2, $3, NOW(), NOW())
            ON CONFLICT (account_id, team_id) DO NOTHING
            """,
            account_id,
            team_id,
            role,
        )

    async def _mark_account_self_registered(
        self,
        db_client: Any,
        *,
        account_id: str,
        organization_id: str,
        team_id: str,
    ) -> None:
        await db_client.execute_raw(
            """
            UPDATE deltallm_platformaccount
            SET metadata = NULLIF(
                    CASE
                        WHEN jsonb_typeof(metadata) = 'object'
                        THEN metadata
                        ELSE '{}'::jsonb
                    END
                    || jsonb_build_object(
                        $2,
                        CASE
                            WHEN jsonb_typeof(metadata -> $2) = 'object'
                            THEN metadata -> $2
                            ELSE '{}'::jsonb
                        END || $3::jsonb
                    ),
                    '{}'::jsonb
                ),
                updated_at = NOW()
            WHERE account_id = $1
            """,
            account_id,
            _ACCOUNT_METADATA_KEY,
            json.dumps(
                {
                    "source": _SELF_REGISTRATION_SOURCE,
                    "registered": True,
                    "default_organization_id": organization_id,
                    "default_team_id": team_id,
                }
            ),
        )

    async def _ensure_runtime_user(
        self,
        db_client: Any,
        *,
        account_id: str,
        email: str,
        team_id: str,
        settings: SelfRegistrationSettings,
    ) -> str:
        default_user = settings.default_user
        await db_client.execute_raw(
            """
            INSERT INTO deltallm_usertable (
                user_id,
                user_email,
                user_role,
                max_budget,
                soft_budget,
                spend,
                models,
                rpm_limit,
                tpm_limit,
                rph_limit,
                rpd_limit,
                tpd_limit,
                team_id,
                metadata,
                created_at,
                updated_at
            )
            VALUES ($1, $2, $3, $4, $5, 0, ARRAY[]::text[], $6, $7, $8, $9, $10, $11, $12::jsonb, NOW(), NOW())
            ON CONFLICT DO NOTHING
            """,
            account_id,
            email,
            default_user.user_role,
            default_user.max_budget,
            default_user.soft_budget,
            default_user.rpm_limit,
            default_user.tpm_limit,
            default_user.rph_limit,
            default_user.rpd_limit,
            default_user.tpd_limit,
            team_id,
            self._metadata_json("user"),
        )
        runtime_user = await self._get_runtime_user_by_account_or_email(
            db_client,
            account_id=account_id,
            email=email,
        )
        if runtime_user is None:
            raise RuntimeError("failed to provision runtime user")
        user_id = str(runtime_user.get("user_id") or "").strip()
        if not user_id:
            raise RuntimeError("failed to provision runtime user")
        runtime_team_id = str(runtime_user.get("team_id") or "").strip()
        if runtime_team_id != team_id:
            raise RuntimeError("self-registration runtime user belongs to a different team")
        return user_id

    async def _get_runtime_user_by_account_or_email(
        self,
        db_client: Any,
        *,
        account_id: str,
        email: str,
    ) -> dict[str, Any] | None:
        rows = await db_client.query_raw(
            """
            SELECT user_id, user_email, team_id
            FROM deltallm_usertable
            WHERE user_id = $1 OR lower(user_email) = lower($2)
            ORDER BY CASE WHEN user_id = $1 THEN 0 ELSE 1 END
            LIMIT 1
            """,
            account_id,
            email,
        )
        return dict(rows[0]) if rows else None

    def _metadata_json(self, entity_type: str) -> str:
        return json.dumps(
            {
                "source": _SELF_REGISTRATION_SOURCE,
                "self_registration_default": entity_type,
            }
        )
