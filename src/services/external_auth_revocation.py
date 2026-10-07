from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.audit.actions import AuditAction
from src.auth.external_config import ExternalAuthSettings
from src.auth.external_contracts import ExternalPurpose, VerifiedExternalAssertion
from src.auth.external_errors import ExternalAuthError
from src.db.external_auth_sessions import ExternalSessionRepository
from src.db.external_auth_subjects import ExternalSubjectRepository
from src.db.external_auth_transactions import ExternalAuthTransactions
from src.services.external_auth_audit import ExternalAuditEvent, ExternalAuthAudit
from src.services.external_auth_binding_policy import lock_external_binding, require_subject_binding


class ExternalAuthRevocation:
    def __init__(
        self,
        transactions: ExternalAuthTransactions,
        audit: ExternalAuthAudit,
        settings: ExternalAuthSettings,
    ) -> None:
        self.transactions = transactions
        self.audit = audit
        self.settings = settings

    async def revoke(self, assertion: VerifiedExternalAssertion, correlation_id: str) -> None:
        if assertion.claims.purpose not in (ExternalPurpose.REVOKE, ExternalPurpose.SUSPEND):
            raise ExternalAuthError()
        async with self.transactions.transaction() as db:
            locked = await lock_external_binding(db, assertion, revocation=True)
            subjects = ExternalSubjectRepository(db)
            subject = await subjects.lock_identity(assertion)
            if subject is None:
                subject = await subjects.create_shell(assertion)
            require_subject_binding(subject, locked.binding)
            sessions = ExternalSessionRepository(db)
            if assertion.claims.purpose == ExternalPurpose.SUSPEND:
                await subjects.set_state(subject.subject_id, state="suspended")
                await sessions.revoke_scope(
                    integration_id=assertion.integration_id, subject_id=subject.subject_id
                )
                action = AuditAction.EXTERNAL_AUTH_SUBJECT_SUSPEND
            else:
                auth_time = datetime.fromtimestamp(assertion.claims.auth_time, UTC)
                expiry = min(
                    datetime.fromtimestamp(assertion.claims.external_session_expires_at, UTC),
                    auth_time + timedelta(seconds=self.settings.parent_lifetime_seconds),
                )
                parent = await sessions.lock_parent(
                    integration_id=assertion.integration_id,
                    parent_hash=assertion.parent_hash,
                    subject_id=subject.subject_id,
                    auth_time=auth_time,
                    expires_at=expiry,
                    revoked=True,
                )
                if parent.subject_id != subject.subject_id or parent.auth_time != auth_time:
                    raise ExternalAuthError("identity_binding_conflict", status_code=409)
                await sessions.revoke_parent(parent.parent_id)
                action = AuditAction.EXTERNAL_AUTH_PARENT_REVOKE
            await self.audit.write(
                db,
                ExternalAuditEvent(
                    action,
                    assertion.integration_id,
                    correlation_id,
                    "success",
                    locked.binding.organization_id,
                    locked.binding.binding_id,
                    subject.account_id,
                    assertion.claims.purpose,
                ),
            )
