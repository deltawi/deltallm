from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
from typing import Any

from src.auth.roles import (
    OrganizationRole,
    PlatformRole,
    TeamRole,
    validate_organization_role,
    validate_team_role,
)
from src.db.email import EmailOutboxRepository
from src.db.email_feedback import EmailFeedbackRepository
from src.db.email_tokens import EmailTokenRepository
from src.db.invitations import InvitationRepository, PlatformInvitationRecord
from src.services.email_token_service import EmailTokenService
from src.services.email_outbox_service import EmailOutboxService
from src.services.platform_identity_service import LoginResult, PlatformIdentityService


@dataclass(frozen=True)
class InvitationContext:
    invitation_id: str
    email: str
    status: str
    invite_scope_type: str
    expires_at: datetime
    inviter_email: str | None
    metadata: dict[str, Any] | None
    password_required: bool


@dataclass(frozen=True)
class InvitationAcceptResult:
    account_id: str
    email: str
    role: str
    mfa_enabled: bool
    force_password_change: bool
    session_established: bool
    next_step: str
    session_token: str | None = None
    mfa_required: bool = False
    mfa_prompt: bool = False


class InvitationService:
    def __init__(
        self,
        *,
        db_client: Any,
        repository: InvitationRepository,
        token_service: EmailTokenService,
        outbox_service: EmailOutboxService,
        platform_identity_service: PlatformIdentityService,
        config_getter,
    ) -> None:  # noqa: ANN001
        self.db = db_client
        self.repository = repository
        self.token_service = token_service
        self.outbox_service = outbox_service
        self.platform_identity_service = platform_identity_service
        self._config_getter = config_getter

    def _transactional_dependencies(
        self,
        db_client: Any,
    ) -> tuple[InvitationRepository, EmailTokenService, EmailOutboxService, PlatformIdentityService]:
        if self.outbox_service is None:
            raise RuntimeError("email outbox service unavailable")
        if db_client is self.db:
            return self.repository, self.token_service, self.outbox_service, self.platform_identity_service
        return (
            InvitationRepository(db_client),
            self.token_service.with_repository(EmailTokenRepository(db_client)),
            self.outbox_service.with_repository(
                EmailOutboxRepository(db_client),
                feedback_repository=EmailFeedbackRepository(db_client),
            ),
            self.platform_identity_service.with_db(db_client),
        )

    async def list_invitations(self, *, status: str | None = None, search: str | None = None) -> list[dict[str, Any]]:
        invitations = await self.repository.list_all(status=status, search=search)
        if not invitations:
            return []

        actor_ids = sorted({item.invited_by_account_id for item in invitations if item.invited_by_account_id})
        actor_map = await self._load_account_emails(actor_ids)
        return [self._serialize_invitation(item, actor_map=actor_map) for item in invitations]

    async def get_invitation(self, invitation_id: str) -> dict[str, Any] | None:
        invitation = await self.repository.get_by_id(invitation_id)
        if invitation is None:
            return None
        actor_map = await self._load_account_emails(
            [invitation.invited_by_account_id] if invitation.invited_by_account_id else []
        )
        return self._serialize_invitation(invitation, actor_map=actor_map)

    async def create_invitation(
        self,
        *,
        email: str,
        invited_by_account_id: str | None,
        organization_id: str | None = None,
        organization_role: str = OrganizationRole.MEMBER,
        team_id: str | None = None,
        team_role: str = TeamRole.VIEWER,
    ) -> dict[str, Any]:
        if self.db is not None and hasattr(self.db, "tx"):
            async with self.db.tx() as tx:
                repository, token_service, outbox_service, identity_service = self._transactional_dependencies(tx)
                return await self._create_invitation_with_dependencies(
                    db_client=tx,
                    repository=repository,
                    token_service=token_service,
                    outbox_service=outbox_service,
                    identity_service=identity_service,
                    email=email,
                    invited_by_account_id=invited_by_account_id,
                    organization_id=organization_id,
                    organization_role=organization_role,
                    team_id=team_id,
                    team_role=team_role,
                )
        if self.outbox_service is None:
            raise RuntimeError("email outbox service unavailable")
        return await self._create_invitation_with_dependencies(
            db_client=self.db,
            repository=self.repository,
            token_service=self.token_service,
            outbox_service=self.outbox_service,
            identity_service=self.platform_identity_service,
            email=email,
            invited_by_account_id=invited_by_account_id,
            organization_id=organization_id,
            organization_role=organization_role,
            team_id=team_id,
            team_role=team_role,
        )

    async def _create_invitation_with_dependencies(
        self,
        *,
        db_client: Any,
        repository: InvitationRepository,
        token_service: EmailTokenService,
        outbox_service: EmailOutboxService,
        identity_service: PlatformIdentityService,
        email: str,
        invited_by_account_id: str | None,
        organization_id: str | None,
        organization_role: str,
        team_id: str | None,
        team_role: str,
    ) -> dict[str, Any]:
        normalized_email = self.platform_identity_service.normalize_email(email)
        if not normalized_email:
            raise ValueError("email is required")
        if not organization_id and not team_id:
            raise ValueError("organization_id or team_id is required")
        if organization_id:
            organization_role = validate_organization_role(organization_role)
        if team_id:
            team_role = validate_team_role(team_role)

        team_context = await self._resolve_team_context(team_id, db_client=db_client) if team_id else None
        organization_context = await self._resolve_organization_context(organization_id, db_client=db_client) if organization_id else None
        if team_context and organization_context:
            team_organization_id = str(team_context.get("organization_id") or "").strip()
            if team_organization_id != str(organization_context.get("organization_id") or "").strip():
                raise ValueError("team_id does not belong to organization_id")

        account = await identity_service.ensure_account(
            email=normalized_email,
            role=PlatformRole.ORG_USER,
            is_active=False,
        )
        account_id = str(account.get("account_id") or "")
        metadata = self._merge_invitation_metadata(
            None,
            organization_context=organization_context,
            organization_role=organization_role if organization_id else None,
            team_context=team_context,
            team_role=team_role if team_id else None,
        )
        scope_fingerprint = self._scope_fingerprint(metadata)
        pending = await repository.list_pending_by_account_id(account_id)
        existing = next(
            (item for item in pending if self._scope_fingerprint(item.metadata) == scope_fingerprint),
            None,
        )
        expires_at = self._invitation_expiry()
        scope_type = self._scope_type(metadata)

        if existing is not None:
            invitation = await repository.update_pending(
                invitation_id=existing.invitation_id,
                invite_scope_type=scope_type,
                invited_by_account_id=invited_by_account_id,
                expires_at=expires_at,
                metadata=metadata,
            )
            if invitation is None:
                raise RuntimeError("failed to update invitation")
        else:
            invitation = await repository.create(
                PlatformInvitationRecord(
                    invitation_id="",
                    account_id=account_id,
                    email=normalized_email,
                    status="pending",
                    invite_scope_type=scope_type,
                    invited_by_account_id=invited_by_account_id,
                    expires_at=expires_at,
                    metadata=metadata,
                )
            )

        token_issue = await token_service.issue_invitation_token(
            account_id=account_id,
            email=normalized_email,
            invitation_id=invitation.invitation_id,
            created_by_account_id=invited_by_account_id,
        )
        try:
            inviter_email = await self._load_account_email(invited_by_account_id, db_client=db_client)
            queued = await outbox_service.enqueue_template_email(
                template_key="invite_user",
                to_addresses=(normalized_email,),
                payload_json={
                    "accept_url": token_service.build_action_url(path="/accept-invite", raw_token=token_issue.raw_token),
                    "instance_name": str(getattr(self._general_settings(), "instance_name", "DeltaLLM") or "DeltaLLM"),
                    "inviter_email": inviter_email or "an administrator",
                    "scope_summary": self._scope_summary(metadata),
                },
                kind="transactional",
                created_by_account_id=invited_by_account_id,
            )
            if queued.status != "queued":
                raise ValueError("invitation email cannot be delivered to the requested recipient")
            updated = await repository.mark_sent(
                invitation_id=invitation.invitation_id,
                message_email_id=queued.email_id,
                expires_at=token_issue.record.expires_at,
                metadata=metadata,
            )
        except Exception:
            await token_service.consume_token(token_id=token_issue.record.token_id)
            raise
        await token_service.invalidate_active_tokens(
            purpose="invite_accept",
            account_id=account_id,
            invitation_id=invitation.invitation_id,
            exclude_token_id=token_issue.record.token_id,
        )
        invitation = updated or invitation
        return {
            "invitation_id": invitation.invitation_id,
            "account_id": invitation.account_id,
            "email": invitation.email,
            "status": invitation.status,
            "invite_scope_type": invitation.invite_scope_type,
            "expires_at": invitation.expires_at.isoformat(),
            "metadata": invitation.metadata or {},
            "message_email_id": invitation.message_email_id,
        }

    async def resend_invitation(self, *, invitation_id: str, invited_by_account_id: str | None) -> dict[str, Any]:
        invitation = await self._require_manageable_invitation(invitation_id)
        return await self.create_invitation(
            email=invitation.email,
            invited_by_account_id=invited_by_account_id,
            organization_id=self._first_organization_id(invitation.metadata),
            organization_role=self._first_organization_role(invitation.metadata),
            team_id=self._first_team_id(invitation.metadata),
            team_role=self._first_team_role(invitation.metadata),
        )

    async def cancel_invitation(self, *, invitation_id: str) -> bool:
        invitation = await self._require_manageable_invitation(invitation_id)
        await self.token_service.invalidate_active_tokens(
            purpose="invite_accept",
            account_id=invitation.account_id,
            invitation_id=invitation.invitation_id,
        )
        return await self.repository.mark_cancelled(invitation.invitation_id)

    async def describe_invitation_token(self, raw_token: str) -> InvitationContext | None:
        token = await self.token_service.validate_token(purpose="invite_accept", raw_token=raw_token)
        if token is None or token.invitation_id is None:
            return None
        invitation = await self.repository.get_by_id(token.invitation_id)
        if invitation is None:
            return None
        invitation = await self._expire_if_needed(invitation)
        if invitation.status not in {"pending", "sent"}:
            return None
        auth_state = await self.platform_identity_service.get_account_auth_state(invitation.account_id)
        if auth_state is None:
            return None
        return InvitationContext(
            invitation_id=invitation.invitation_id,
            email=invitation.email,
            status=invitation.status,
            invite_scope_type=invitation.invite_scope_type,
            expires_at=invitation.expires_at,
            inviter_email=await self._load_account_email(invitation.invited_by_account_id),
            metadata=invitation.metadata or {},
            password_required=not auth_state.has_local_password and not auth_state.has_sso_identity,
        )

    async def accept_invitation(self, *, raw_token: str, password: str | None) -> InvitationAcceptResult | None:
        if self.db is not None and hasattr(self.db, "tx"):
            async with self.db.tx() as tx:
                repository, token_service, _outbox_service, identity_service = self._transactional_dependencies(tx)
                return await self._accept_invitation_with_dependencies(
                    raw_token=raw_token,
                    password=password,
                    repository=repository,
                    token_service=token_service,
                    identity_service=identity_service,
                )
        return await self._accept_invitation_with_dependencies(
            raw_token=raw_token,
            password=password,
            repository=self.repository,
            token_service=self.token_service,
            identity_service=self.platform_identity_service,
        )

    async def _accept_invitation_with_dependencies(
        self,
        *,
        raw_token: str,
        password: str | None,
        repository: InvitationRepository,
        token_service: EmailTokenService,
        identity_service: PlatformIdentityService,
    ) -> InvitationAcceptResult | None:
        token = await token_service.validate_token(purpose="invite_accept", raw_token=raw_token)
        if token is None or token.invitation_id is None:
            return None
        invitation = await repository.get_by_id(token.invitation_id)
        if invitation is None:
            return None
        invitation = await self._expire_if_needed(invitation, repository=repository, token_service=token_service)
        if invitation.status not in {"pending", "sent"}:
            return None

        auth_state = await identity_service.get_account_auth_state(invitation.account_id)
        if auth_state is None:
            return None

        password_required = not auth_state.has_local_password and not auth_state.has_sso_identity
        if password_required:
            if not password:
                raise ValueError("password is required")
            identity_service.validate_password_policy(password)
        elif password:
            raise ValueError("password cannot be changed when accepting this invitation")

        claimed = await token_service.claim_token(purpose="invite_accept", raw_token=raw_token)
        if claimed is None or claimed.invitation_id != invitation.invitation_id:
            return None

        if password_required:
            await identity_service.set_password(account_id=invitation.account_id, new_password=password)

        await self._apply_invitation_memberships(
            account_id=invitation.account_id,
            metadata=invitation.metadata,
            identity_service=identity_service,
        )
        await identity_service.set_account_active(invitation.account_id, is_active=True)
        await token_service.invalidate_active_tokens(
            purpose="invite_accept",
            account_id=invitation.account_id,
            invitation_id=invitation.invitation_id,
        )
        accepted = await repository.mark_accepted(invitation.invitation_id)
        if not accepted:
            raise RuntimeError("failed to mark invitation accepted")
        account = await identity_service.get_account_by_id(invitation.account_id)
        if account is None:
            raise RuntimeError("failed to load account after invitation acceptance")

        mfa_enabled = bool(account.get("mfa_enabled", False))
        if mfa_enabled:
            return InvitationAcceptResult(
                account_id=invitation.account_id,
                email=str(account.get("email") or invitation.email),
                role=str(account.get("role") or "org_user"),
                mfa_enabled=True,
                force_password_change=bool(account.get("force_password_change", False)),
                session_established=False,
                next_step="login",
                mfa_required=True,
                mfa_prompt=False,
            )

        login = await identity_service.create_login_result_for_account(invitation.account_id)
        if login is None:
            raise RuntimeError("failed to create login session after invitation acceptance")
        await identity_service.mark_last_login(invitation.account_id)
        return self._to_accept_result(login)

    async def _require_manageable_invitation(self, invitation_id: str) -> PlatformInvitationRecord:
        invitation = await self.repository.get_by_id(invitation_id)
        if invitation is None:
            raise ValueError("invitation not found")
        invitation = await self._expire_if_needed(invitation)
        if invitation.status == "accepted":
            raise ValueError("accepted invitations cannot be modified")
        if invitation.status == "cancelled":
            raise ValueError("cancelled invitations cannot be modified")
        return invitation

    async def _expire_if_needed(
        self,
        invitation: PlatformInvitationRecord,
        *,
        repository: InvitationRepository | None = None,
        token_service: EmailTokenService | None = None,
    ) -> PlatformInvitationRecord:
        repository = repository or self.repository
        token_service = token_service or self.token_service
        if invitation.status in {"pending", "sent"} and invitation.expires_at <= datetime.now(tz=UTC):
            await repository.mark_expired(invitation.invitation_id)
            await token_service.invalidate_active_tokens(
                purpose="invite_accept",
                account_id=invitation.account_id,
                invitation_id=invitation.invitation_id,
            )
            refreshed = await repository.get_by_id(invitation.invitation_id)
            if refreshed is not None:
                return refreshed
        return invitation

    async def _resolve_organization_context(self, organization_id: str | None, *, db_client: Any | None = None) -> dict[str, Any] | None:
        if not organization_id:
            return None
        query_client = db_client or self.db
        rows = await query_client.query_raw(
            """
            SELECT organization_id, organization_name
            FROM deltallm_organizationtable
            WHERE organization_id = $1
            LIMIT 1
            """,
            organization_id,
        )
        if not rows:
            raise ValueError("organization not found")
        return dict(rows[0])

    async def _resolve_team_context(self, team_id: str | None, *, db_client: Any | None = None) -> dict[str, Any] | None:
        if not team_id:
            return None
        query_client = db_client or self.db
        rows = await query_client.query_raw(
            """
            SELECT team_id, team_alias, organization_id
            FROM deltallm_teamtable
            WHERE team_id = $1
            LIMIT 1
            """,
            team_id,
        )
        if not rows:
            raise ValueError("team not found")
        return dict(rows[0])

    def _merge_invitation_metadata(
        self,
        existing: dict[str, Any] | None,
        *,
        organization_context: dict[str, Any] | None,
        organization_role: str | None,
        team_context: dict[str, Any] | None,
        team_role: str | None,
    ) -> dict[str, Any]:
        metadata = dict(existing or {})
        organization_invites = [dict(item) for item in list(metadata.get("organization_invites") or []) if isinstance(item, dict)]
        team_invites = [dict(item) for item in list(metadata.get("team_invites") or []) if isinstance(item, dict)]

        if organization_context and organization_role:
            entry = {
                "organization_id": str(organization_context.get("organization_id") or ""),
                "organization_name": str(organization_context.get("organization_name") or "") or None,
                "role": organization_role,
            }
            organization_invites = [item for item in organization_invites if item.get("organization_id") != entry["organization_id"]]
            organization_invites.append(entry)

        if team_context and team_role:
            entry = {
                "team_id": str(team_context.get("team_id") or ""),
                "team_alias": str(team_context.get("team_alias") or "") or None,
                "organization_id": str(team_context.get("organization_id") or ""),
                "role": team_role,
            }
            team_invites = [item for item in team_invites if item.get("team_id") != entry["team_id"]]
            team_invites.append(entry)

        metadata["organization_invites"] = sorted(organization_invites, key=lambda item: str(item.get("organization_id") or ""))
        metadata["team_invites"] = sorted(team_invites, key=lambda item: str(item.get("team_id") or ""))
        return metadata

    async def _apply_invitation_memberships(
        self,
        *,
        account_id: str,
        metadata: dict[str, Any] | None,
        identity_service: PlatformIdentityService | None = None,
    ) -> None:
        identity_service = identity_service or self.platform_identity_service
        explicit_org_roles: dict[str, str] = {}
        for item in list((metadata or {}).get("organization_invites") or []):
            if not isinstance(item, dict):
                continue
            organization_id = str(item.get("organization_id") or "").strip()
            role = validate_organization_role(str(item.get("role") or OrganizationRole.MEMBER).strip() or OrganizationRole.MEMBER)
            if not organization_id:
                continue
            explicit_org_roles[organization_id] = role
            await identity_service.upsert_organization_membership(
                account_id=account_id,
                organization_id=organization_id,
                role=role,
            )

        for item in list((metadata or {}).get("team_invites") or []):
            if not isinstance(item, dict):
                continue
            team_id = str(item.get("team_id") or "").strip()
            team_role = validate_team_role(str(item.get("role") or TeamRole.VIEWER).strip() or TeamRole.VIEWER)
            organization_id = str(item.get("organization_id") or "").strip()
            if organization_id and organization_id not in explicit_org_roles:
                await identity_service.upsert_organization_membership(
                    account_id=account_id,
                    organization_id=organization_id,
                    role=OrganizationRole.MEMBER,
                )
            if team_id:
                await identity_service.upsert_team_membership(
                    account_id=account_id,
                    team_id=team_id,
                    role=team_role,
                )

    def _scope_type(self, metadata: dict[str, Any] | None) -> str:
        organization_invites = list((metadata or {}).get("organization_invites") or [])
        team_invites = list((metadata or {}).get("team_invites") or [])
        if organization_invites and team_invites:
            return "mixed"
        if team_invites:
            return "team"
        return "organization"

    def _scope_summary(self, metadata: dict[str, Any] | None) -> str:
        parts: list[str] = []
        for item in list((metadata or {}).get("organization_invites") or []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("organization_name") or item.get("organization_id") or "").strip()
            role = str(item.get("role") or "").replace("_", " ")
            if name:
                parts.append(f"Organization {name} ({role})")
        for item in list((metadata or {}).get("team_invites") or []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("team_alias") or item.get("team_id") or "").strip()
            role = str(item.get("role") or "").replace("_", " ")
            if name:
                parts.append(f"Team {name} ({role})")
        return ", ".join(parts) or "your assigned access"

    def _first_organization_id(self, metadata: dict[str, Any] | None) -> str | None:
        entries = list((metadata or {}).get("organization_invites") or [])
        if not entries:
            return None
        first = entries[0]
        if not isinstance(first, dict):
            return None
        return str(first.get("organization_id") or "") or None

    def _first_organization_role(self, metadata: dict[str, Any] | None) -> str:
        entries = list((metadata or {}).get("organization_invites") or [])
        if not entries or not isinstance(entries[0], dict):
            return OrganizationRole.MEMBER
        return str(entries[0].get("role") or OrganizationRole.MEMBER)

    def _first_team_id(self, metadata: dict[str, Any] | None) -> str | None:
        entries = list((metadata or {}).get("team_invites") or [])
        if not entries:
            return None
        first = entries[0]
        if not isinstance(first, dict):
            return None
        return str(first.get("team_id") or "") or None

    def _first_team_role(self, metadata: dict[str, Any] | None) -> str:
        entries = list((metadata or {}).get("team_invites") or [])
        if not entries or not isinstance(entries[0], dict):
            return TeamRole.VIEWER
        return str(entries[0].get("role") or TeamRole.VIEWER)

    def _invitation_expiry(self) -> datetime:
        hours = int(getattr(self._general_settings(), "invitation_token_ttl_hours", 72) or 72)
        return datetime.now(tz=UTC).replace(microsecond=0) + timedelta(hours=hours)

    async def _load_account_email(self, account_id: str | None, *, db_client: Any | None = None) -> str | None:
        if not account_id:
            return None
        query_client = db_client or self.db
        rows = await query_client.query_raw(
            "SELECT email FROM deltallm_platformaccount WHERE account_id = $1 LIMIT 1",
            account_id,
        )
        if not rows:
            return None
        return str(rows[0].get("email") or "") or None

    async def _load_account_emails(self, account_ids: list[str]) -> dict[str, str]:
        if not account_ids:
            return {}
        placeholders = ", ".join(f"${index + 1}" for index in range(len(account_ids)))
        rows = await self.db.query_raw(
            f"SELECT account_id, email FROM deltallm_platformaccount WHERE account_id IN ({placeholders})",
            *account_ids,
        )
        return {str(row.get("account_id") or ""): str(row.get("email") or "") for row in rows}

    def _serialize_invitation(
        self,
        invitation: PlatformInvitationRecord,
        *,
        actor_map: dict[str, str],
    ) -> dict[str, Any]:
        return {
            "invitation_id": invitation.invitation_id,
            "account_id": invitation.account_id,
            "email": invitation.email,
            "status": invitation.status,
            "invite_scope_type": invitation.invite_scope_type,
            "expires_at": invitation.expires_at.isoformat(),
            "accepted_at": invitation.accepted_at.isoformat() if invitation.accepted_at else None,
            "cancelled_at": invitation.cancelled_at.isoformat() if invitation.cancelled_at else None,
            "created_at": invitation.created_at.isoformat() if invitation.created_at else None,
            "updated_at": invitation.updated_at.isoformat() if invitation.updated_at else None,
            "invited_by_account_id": invitation.invited_by_account_id,
            "inviter_email": actor_map.get(invitation.invited_by_account_id or ""),
            "message_email_id": invitation.message_email_id,
            "metadata": invitation.metadata or {},
        }

    def _scope_fingerprint(self, metadata: dict[str, Any] | None) -> str:
        normalized = {
            "organization_invites": sorted(
                [
                    {
                        "organization_id": str(item.get("organization_id") or ""),
                        "role": str(item.get("role") or OrganizationRole.MEMBER),
                    }
                    for item in list((metadata or {}).get("organization_invites") or [])
                    if isinstance(item, dict) and str(item.get("organization_id") or "").strip()
                ],
                key=lambda item: (item["organization_id"], item["role"]),
            ),
            "team_invites": sorted(
                [
                    {
                        "team_id": str(item.get("team_id") or ""),
                        "organization_id": str(item.get("organization_id") or ""),
                        "role": str(item.get("role") or TeamRole.VIEWER),
                    }
                    for item in list((metadata or {}).get("team_invites") or [])
                    if isinstance(item, dict) and str(item.get("team_id") or "").strip()
                ],
                key=lambda item: (item["team_id"], item["organization_id"], item["role"]),
            ),
        }
        encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _general_settings(self) -> Any:
        cfg = self._config_getter()
        return getattr(cfg, "general_settings", None)

    def _to_accept_result(self, login: LoginResult) -> InvitationAcceptResult:
        return InvitationAcceptResult(
            account_id=login.context.account_id,
            email=login.context.email,
            role=login.context.role,
            mfa_enabled=login.context.mfa_enabled,
            force_password_change=login.context.force_password_change,
            session_established=True,
            next_step="session_established",
            session_token=login.session_token,
            mfa_required=login.mfa_required,
            mfa_prompt=login.mfa_prompt,
        )
