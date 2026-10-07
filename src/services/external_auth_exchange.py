from __future__ import annotations

from datetime import UTC, datetime, timedelta
import secrets

from src.audit.actions import AuditAction
from src.auth.external_config import ExternalAuthSettings
from src.auth.external_policy import EXTERNAL_SESSION_PREFIX
from src.auth.external_contracts import VerifiedExternalAssertion
from src.auth.external_errors import ExternalAuthError
from src.auth.sso_identity import (
    SSOAccountMatch,
    SSOAccountLinkRequiredError,
    SSOAccountResolutionPolicy,
    SSOIdentityAssertion,
    SSOSubjectSource,
)
from src.db.external_auth_assertions import ExternalAssertionRepository
from src.db.external_auth_provisioning import ExternalAccountState, ExternalProvisioningRepository
from src.db.external_auth_records import ExternalSubjectRecord
from src.db.external_auth_sessions import ExternalSessionRepository
from src.db.external_auth_subjects import ExternalSubjectRepository
from src.db.external_auth_transactions import ExternalAuthTransactions
from src.db.platform_accounts import PlatformAccountDatabase
from src.models.external_auth import ExternalExchangeResponse
from src.services.external_auth_audit import ExternalAuditEvent, ExternalAuthAudit
from src.services.external_auth_binding_policy import (
    LockedExternalBinding,
    lock_external_binding,
    require_subject_binding,
)
from src.services.platform_identity_service import PlatformIdentityService
from src.services.sso_account_service import SSOAccountService


class ExternalAuthExchange:
    def __init__(
        self,
        transactions: ExternalAuthTransactions,
        audit: ExternalAuthAudit,
        identities: PlatformIdentityService,
        settings: ExternalAuthSettings,
    ) -> None:
        self.transactions = transactions
        self.audit = audit
        self.identities = identities
        self.settings = settings

    async def consume(self, assertion: VerifiedExternalAssertion, correlation_id: str) -> None:
        async with self.transactions.transaction() as db:
            result = await ExternalAssertionRepository(db).consume(assertion)
            await self.audit.write(
                db,
                ExternalAuditEvent(
                    AuditAction.EXTERNAL_AUTH_ATTEMPT,
                    assertion.integration_id,
                    correlation_id,
                    result,
                    binding_id=assertion.claims.binding_id,
                    purpose=assertion.claims.purpose,
                ),
            )
        if result == "replayed":
            raise ExternalAuthError("assertion_replayed", status_code=409)
        if result != "claimed":
            raise ExternalAuthError()

    async def exchange(
        self, assertion: VerifiedExternalAssertion, correlation_id: str
    ) -> ExternalExchangeResponse:
        async with self.transactions.transaction() as db:
            locked = await lock_external_binding(db, assertion)
            subjects = ExternalSubjectRepository(db)
            subject = await subjects.lock_identity(assertion)
            created = subject is None or subject.state == "pending"
            if subject is None:
                subject = await subjects.create_shell(assertion)
            require_subject_binding(subject, locked.binding)
            if subject.state == "suspended":
                raise ExternalAuthError()
            if created:
                subject = await self._provision(db, assertion, subject, locked)
                state = ExternalAccountState(
                    subject.account_id or "",
                    subject.identity_id or "",
                    subject.runtime_user_id or "",
                    False,
                    False,
                )
            else:
                state = await ExternalProvisioningRepository(db).eligible_account(
                    subject, locked.binding
                )
                if state is None:
                    raise ExternalAuthError()
            response = await self._issue(db, assertion, subject, locked, state)
            events = [
                ExternalAuditEvent(
                    AuditAction.EXTERNAL_AUTH_SUCCESS,
                    assertion.integration_id,
                    correlation_id,
                    "success",
                    locked.binding.organization_id,
                    locked.binding.binding_id,
                    state.account_id,
                    assertion.claims.purpose,
                )
            ]
            if created:
                events.append(
                    ExternalAuditEvent(
                        AuditAction.EXTERNAL_AUTH_PROVISION,
                        assertion.integration_id,
                        correlation_id,
                        "success",
                        locked.binding.organization_id,
                        locked.binding.binding_id,
                        state.account_id,
                        assertion.claims.purpose,
                    )
                )
            await self.audit.write_many(db, events)
            return response

    async def _provision(
        self,
        db: PlatformAccountDatabase,
        assertion: VerifiedExternalAssertion,
        subject: ExternalSubjectRecord,
        locked: LockedExternalBinding,
    ) -> ExternalSubjectRecord:
        identity = SSOIdentityAssertion(
            provider=assertion.provider,
            subject=assertion.claims.sub,
            email=assertion.claims.email,
            email_verified=True,
            subject_source=SSOSubjectSource.PROVIDER,
        )
        try:
            resolved = await SSOAccountService(db, self.identities.with_db(db)).resolve(
                identity, policy=SSOAccountResolutionPolicy.EXTERNAL_CUSTOMER
            )
        except SSOAccountLinkRequiredError as exc:
            raise ExternalAuthError("account_link_required", status_code=409) from exc
        if resolved.match is not SSOAccountMatch.CREATED:
            raise ExternalAuthError("account_link_required", status_code=409)
        provisioned = await ExternalProvisioningRepository(db).finish_new_account(
            subject=subject,
            binding=locked.binding,
            account_id=resolved.account.account_id,
            email=resolved.account.email,
            provider=assertion.provider,
        )
        if provisioned is None:
            raise ExternalAuthError("account_link_required", status_code=409)
        return provisioned

    async def _issue(
        self,
        db: PlatformAccountDatabase,
        assertion: VerifiedExternalAssertion,
        subject: ExternalSubjectRecord,
        locked: LockedExternalBinding,
        state: ExternalAccountState,
    ) -> ExternalExchangeResponse:
        now = datetime.now(UTC)
        auth_time = datetime.fromtimestamp(assertion.claims.auth_time, UTC)
        parent_expiry = min(
            datetime.fromtimestamp(assertion.claims.external_session_expires_at, UTC),
            auth_time + timedelta(seconds=self.settings.parent_lifetime_seconds),
        )
        sessions = ExternalSessionRepository(db)
        parent = await sessions.lock_parent(
            integration_id=assertion.integration_id,
            parent_hash=assertion.parent_hash,
            subject_id=subject.subject_id,
            auth_time=auth_time,
            expires_at=parent_expiry,
        )
        if parent.subject_id != subject.subject_id or parent.auth_time != auth_time:
            raise ExternalAuthError("identity_binding_conflict", status_code=409)
        if parent.revoked_at is not None:
            raise ExternalAuthError("external_reauthentication_required")
        if parent.expires_at <= now or parent_expiry <= now or parent_expiry > parent.expires_at:
            raise ExternalAuthError("external_reauthentication_required")
        expires_at = min(
            parent_expiry,
            parent.expires_at,
            now + timedelta(seconds=self.settings.child_lifetime_seconds),
        )
        token = EXTERNAL_SESSION_PREFIX + secrets.token_urlsafe(32)
        issued = await sessions.issue(
            parent=parent,
            subject=subject,
            binding=locked.binding,
            integration_epoch=locked.integration.epoch,
            token_hash=self.identities.sessions.hash_token(token),
            expires_at=expires_at,
            mfa_verified=not state.mfa_enabled,
            overlap_seconds=self.settings.previous_overlap_seconds,
        )
        return ExternalExchangeResponse(
            session_token=token,
            session_generation=issued.generation,
            account_id=state.account_id,
            organization_id=locked.binding.organization_id,
            team_id=locked.binding.team_id,
            inference_user_id=state.runtime_user_id,
            binding_id=locked.binding.binding_id,
            expires_at=expires_at,
            refresh_after_seconds=max(1, min(240, int((expires_at - now).total_seconds()) - 30)),
            next_step="mfa_verify"
            if state.mfa_enabled and not issued.mfa_verified
            else "password_change"
            if state.force_password_change
            else "ready",
            mfa_required=state.mfa_enabled and not issued.mfa_verified,
        )
