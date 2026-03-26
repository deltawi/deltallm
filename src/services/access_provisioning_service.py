from __future__ import annotations

from typing import Any

from src.auth.roles import (
    OrganizationRole,
    PlatformRole,
    TeamRole,
    validate_organization_role,
    validate_platform_role,
    validate_team_role,
)
from src.services.invitation_service import InvitationService
from src.services.platform_identity_service import PlatformIdentityService


class AccessProvisioningService:
    def __init__(
        self,
        *,
        db_client: Any,
        platform_identity_service: PlatformIdentityService,
        invitation_service: InvitationService | None,
    ) -> None:
        self.db = db_client
        self.platform_identity_service = platform_identity_service
        self.invitation_service = invitation_service

    async def provision_person(
        self,
        *,
        email: str,
        mode: str,
        platform_role: str | None = None,
        password: str | None = None,
        is_active: bool = True,
        organization_id: str | None = None,
        organization_role: str | None = None,
        team_id: str | None = None,
        team_role: str | None = None,
        invited_by_account_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_email = self.platform_identity_service.normalize_email(email)
        if not normalized_email:
            raise ValueError("email is required")

        normalized_mode = str(mode or "").strip()
        if normalized_mode not in {"invite_email", "create_account"}:
            raise ValueError("invalid provisioning mode")

        normalized_organization_id = str(organization_id or "").strip() or None
        normalized_team_id = str(team_id or "").strip() or None
        scope_type = self._scope_type(organization_id=normalized_organization_id, team_id=normalized_team_id)

        if normalized_mode == "invite_email":
            return await self._invite_person(
                email=normalized_email,
                organization_id=normalized_organization_id,
                organization_role=organization_role,
                team_id=normalized_team_id,
                team_role=team_role,
                invited_by_account_id=invited_by_account_id,
                scope_type=scope_type,
                platform_role=platform_role,
                password=password,
            )

        return await self._create_account(
            email=normalized_email,
            platform_role=platform_role,
            password=password,
            is_active=is_active,
            organization_id=normalized_organization_id,
            organization_role=organization_role,
            team_id=normalized_team_id,
            team_role=team_role,
            scope_type=scope_type,
        )

    async def _invite_person(
        self,
        *,
        email: str,
        organization_id: str | None,
        organization_role: str | None,
        team_id: str | None,
        team_role: str | None,
        invited_by_account_id: str | None,
        scope_type: str,
        platform_role: str | None,
        password: str | None,
    ) -> dict[str, Any]:
        if password:
            raise ValueError("password is not allowed when sending an invitation")
        if platform_role and validate_platform_role(platform_role) != PlatformRole.ORG_USER:
            raise ValueError("platform_admin cannot be invited by email")
        if scope_type == "none":
            raise ValueError("organization or team access is required when sending an invitation")

        organization_role_value = (
            validate_organization_role(organization_role or OrganizationRole.MEMBER)
            if organization_id
            else OrganizationRole.MEMBER
        )
        team_role_value = validate_team_role(team_role or TeamRole.VIEWER) if team_id else TeamRole.VIEWER
        if self.invitation_service is None:
            raise RuntimeError("invitation service unavailable")

        invitation = await self.invitation_service.create_invitation(
            email=email,
            invited_by_account_id=invited_by_account_id,
            organization_id=organization_id,
            organization_role=organization_role_value,
            team_id=team_id,
            team_role=team_role_value,
        )
        return {
            "mode": "invite_email",
            **invitation,
        }

    async def _create_account(
        self,
        *,
        email: str,
        platform_role: str | None,
        password: str | None,
        is_active: bool,
        organization_id: str | None,
        organization_role: str | None,
        team_id: str | None,
        team_role: str | None,
        scope_type: str,
    ) -> dict[str, Any]:
        if not password:
            raise ValueError("password is required when creating an account manually")

        role = validate_platform_role(platform_role or PlatformRole.ORG_USER)
        if role == PlatformRole.ADMIN and scope_type != "none":
            raise ValueError("platform_admin accounts cannot be provisioned with scoped memberships")

        if self.db is not None and hasattr(self.db, "tx"):
            async with self.db.tx() as tx:
                identity_service = self.platform_identity_service.with_db(tx)
                return await self._create_account_with_dependencies(
                    identity_service=identity_service,
                    email=email,
                    password=password,
                    role=role,
                    is_active=is_active,
                    organization_id=organization_id,
                    organization_role=organization_role,
                    team_id=team_id,
                    team_role=team_role,
                    scope_type=scope_type,
                )

        return await self._create_account_with_dependencies(
            identity_service=self.platform_identity_service,
            email=email,
            password=password,
            role=role,
            is_active=is_active,
            organization_id=organization_id,
            organization_role=organization_role,
            team_id=team_id,
            team_role=team_role,
            scope_type=scope_type,
        )

    async def _create_account_with_dependencies(
        self,
        *,
        identity_service: PlatformIdentityService,
        email: str,
        password: str,
        role: str,
        is_active: bool,
        organization_id: str | None,
        organization_role: str | None,
        team_id: str | None,
        team_role: str | None,
        scope_type: str,
    ) -> dict[str, Any]:
        identity_service.validate_password_policy(password)
        existing = await identity_service.get_account_by_email(email)
        if existing is not None:
            raise ValueError("account already exists")

        team_organization_id = await self._resolve_team_organization_id(team_id, db_client=identity_service.db) if team_id else None
        if team_id and not team_organization_id:
            raise ValueError("team not found")
        if organization_id:
            exists = await self._organization_exists(organization_id, db_client=identity_service.db)
            if not exists:
                raise ValueError("organization not found")
        if organization_id and team_organization_id and team_organization_id != organization_id:
            raise ValueError("team_id does not belong to organization_id")

        account = await identity_service.create_account(
            email=email,
            role=role,
            is_active=is_active,
            password=password,
        )
        account_id = str(account.get("account_id") or "")
        if not account_id:
            raise RuntimeError("failed to create account")

        if scope_type == "organization" and organization_id:
            await identity_service.upsert_organization_membership(
                account_id=account_id,
                organization_id=organization_id,
                role=validate_organization_role(organization_role or OrganizationRole.MEMBER),
            )
        elif scope_type == "team" and team_id:
            team_org_id = team_organization_id or organization_id
            if team_org_id:
                await identity_service.upsert_organization_membership(
                    account_id=account_id,
                    organization_id=team_org_id,
                    role=OrganizationRole.MEMBER,
                )
            await identity_service.upsert_team_membership(
                account_id=account_id,
                team_id=team_id,
                role=validate_team_role(team_role or TeamRole.VIEWER),
            )

        created = await identity_service.get_account_by_id(account_id)
        if created is None:
            raise RuntimeError("failed to load created account")

        return {
            "mode": "create_account",
            "account_id": str(created.get("account_id") or ""),
            "email": str(created.get("email") or email),
            "role": str(created.get("role") or role),
            "is_active": bool(created.get("is_active", is_active)),
            "organization_id": organization_id,
            "team_id": team_id,
            "scope_type": scope_type,
        }

    async def _organization_exists(self, organization_id: str, *, db_client: Any | None = None) -> bool:
        query_client = db_client or self.db
        if query_client is None or not organization_id:
            return False
        rows = await query_client.query_raw(
            """
            SELECT organization_id
            FROM deltallm_organizationtable
            WHERE organization_id = $1
            LIMIT 1
            """,
            organization_id,
        )
        return bool(rows)

    async def _resolve_team_organization_id(self, team_id: str, *, db_client: Any | None = None) -> str | None:
        query_client = db_client or self.db
        if query_client is None or not team_id:
            return None
        rows = await query_client.query_raw(
            """
            SELECT organization_id
            FROM deltallm_teamtable
            WHERE team_id = $1
            LIMIT 1
            """,
            team_id,
        )
        if not rows:
            return None
        return str(rows[0].get("organization_id") or "").strip() or None

    def _scope_type(self, *, organization_id: str | None, team_id: str | None) -> str:
        if team_id:
            return "team"
        if organization_id:
            return "organization"
        return "none"
