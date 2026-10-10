from __future__ import annotations

from collections.abc import Callable
import hashlib

from src.db.identity.platform_sessions import PlatformSessionRepository

from src.audit.actions import AuditAction
from src.auth.roles import ORG_ROLE_PERMISSIONS, TEAM_ROLE_PERMISSIONS
from src.auth.external_policy import CUSTOMER_PERMISSION_CEILING
from src.db.identity.external.external_auth_sessions import ExternalSessionRepository
from src.db.identity.external.external_auth_transactions import ExternalAuthTransactions
from src.models.external_auth import ExternalWorkspaceContext
from src.models.platform_auth import PlatformAuthContext
from src.services.identity.external.external_auth_audit import ExternalAuditEvent, ExternalAuthAudit


class ExternalSessionService:
    def __init__(self, transactions: ExternalAuthTransactions, audit: ExternalAuthAudit) -> None:
        self.transactions = transactions
        self.audit = audit

    async def get_context(self, token_hash: str) -> PlatformAuthContext | None:
        async with self.transactions.transaction("validation") as db:
            record = await ExternalSessionRepository(db).get_active(token_hash)
        if record is None:
            return None
        permissions = (
            ORG_ROLE_PERMISSIONS.get(record.organization_role, set())
            | TEAM_ROLE_PERMISSIONS.get(record.team_role, set())
        ) & CUSTOMER_PERMISSION_CEILING
        return PlatformAuthContext(
            account_id=record.session.account_id,
            email=record.session.email,
            role="org_user",
            mfa_enabled=record.session.mfa_enabled,
            mfa_verified=record.session.mfa_verified,
            force_password_change=record.session.force_password_change,
            permissions=sorted(permissions),
            organization_memberships=[
                {"organization_id": record.organization_id, "role": record.organization_role}
            ],
            team_memberships=[{"team_id": record.team_id, "role": record.team_role}],
            session_expires_at=record.session.expires_at,
            external_workspace=ExternalWorkspaceContext(
                integration_id=record.integration_id,
                binding_id=record.binding_id,
                subject_id=record.subject_id,
                organization_id=record.organization_id,
                team_id=record.team_id,
                inference_user_id=record.runtime_user_id,
                parent_id=record.parent_id,
                generation=record.generation,
            ),
        )

    async def logout(self, token_hash: str, *, correlation_id: str) -> None:
        async with self.transactions.transaction() as db:
            record = await ExternalSessionRepository(db).get_active(token_hash)
            if record is None:
                return
            await ExternalSessionRepository(db).revoke_parent(record.parent_id)
            await self.audit.write(
                db,
                ExternalAuditEvent(
                    AuditAction.EXTERNAL_AUTH_PARENT_REVOKE,
                    record.integration_id,
                    correlation_id,
                    "success",
                    record.organization_id,
                    record.binding_id,
                    record.session.account_id,
                    reason="gateway_logout",
                ),
            )

    async def verify_mfa(
        self, *, token_hash: str, code: str, verify_code: Callable[[str, str], bool]
    ) -> bool:
        async with self.transactions.transaction() as db:
            live = await ExternalSessionRepository(db).get_active(token_hash)
            if live is None or not live.session.mfa_enabled:
                return False
            repository = PlatformSessionRepository(db)
            mfa = await repository.get_mfa(token_hash, external=True)
            if mfa is None or mfa.secret is None or not verify_code(mfa.secret, code):
                return False
            return await ExternalSessionRepository(db).mark_mfa_verified(
                token_hash, hashlib.sha256(mfa.secret.encode()).hexdigest()
            )
