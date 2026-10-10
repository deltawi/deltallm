from __future__ import annotations

from dataclasses import dataclass

from src.audit.actions import AuditAction
from src.auth.external_contracts import ExternalPurpose, VerifiedExternalAssertion
from src.auth.external_errors import ExternalAuthError
from src.auth.sso_identity import SSOAccountResolutionPolicy, SSOIdentityAssertion, SSOSubjectSource
from src.db.identity.external.external_auth_provisioning import ExternalProvisioningRepository
from src.db.identity.external.external_auth_records import ExternalSubjectRecord
from src.db.identity.external.external_auth_subjects import ExternalSubjectRepository
from src.db.identity.external.external_auth_transactions import ExternalAuthTransactions
from src.db.identity.platform_accounts import PlatformAccountDatabase
from src.services.external_auth_audit import ExternalAuditEvent, ExternalAuthAudit
from src.services.external_auth_binding_policy import (
    LockedExternalBinding,
    lock_external_binding,
    require_subject_binding,
)
from src.services.platform_identity_service import PlatformIdentityService
from src.services.sso_account_service import SSOAccountService


@dataclass(frozen=True, slots=True)
class ExternalLinkApproval:
    binding_id: str
    runtime_user_id: str
    expected_version: int
    approval_reference: str
    reason: str
    approved_by: str
    account_id: str | None = None


class ExternalAuthLinking:
    def __init__(
        self,
        transactions: ExternalAuthTransactions,
        audit: ExternalAuthAudit,
        identities: PlatformIdentityService,
    ) -> None:
        self.transactions = transactions
        self.audit = audit
        self.identities = identities

    async def link(
        self,
        assertion: VerifiedExternalAssertion,
        approval: ExternalLinkApproval,
        correlation_id: str,
    ) -> ExternalSubjectRecord:
        if assertion.claims.purpose != ExternalPurpose.LINK or approval.account_id is None:
            raise ExternalAuthError()
        async with self.transactions.transaction() as db:
            locked, subject = await self._pending(db, assertion, approval)
            provisioning = ExternalProvisioningRepository(db)
            state = await provisioning.eligible_target(
                account_id=approval.account_id,
                runtime_user_id=approval.runtime_user_id,
                binding=locked.binding,
                require_email=assertion.claims.email,
                check_assets=True,
            )
            if state is None:
                raise ExternalAuthError()
            identity = SSOIdentityAssertion(
                assertion.provider,
                assertion.claims.sub,
                assertion.claims.email,
                True,
                SSOSubjectSource.PROVIDER,
            )
            try:
                await SSOAccountService(db, self.identities.with_db(db)).resolve(
                    identity,
                    expected_account_id=approval.account_id,
                    policy=SSOAccountResolutionPolicy.EXTERNAL_CUSTOMER,
                )
            except ValueError as exc:
                raise ExternalAuthError("identity_binding_conflict", status_code=409) from exc
            identity_id = await provisioning.identity_id(
                provider=assertion.provider,
                subject=assertion.claims.sub,
                account_id=approval.account_id,
            )
            if identity_id is None:
                raise ExternalAuthError("identity_binding_conflict", status_code=409)
            linked = await ExternalSubjectRepository(db).activate(
                subject,
                account_id=approval.account_id,
                identity_id=identity_id,
                runtime_user_id=approval.runtime_user_id,
            )
            await self._audit(
                db, assertion, approval, locked, correlation_id, AuditAction.EXTERNAL_AUTH_LINK
            )
            return linked

    async def bind_runtime(
        self,
        assertion: VerifiedExternalAssertion,
        approval: ExternalLinkApproval,
        correlation_id: str,
    ) -> ExternalSubjectRecord:
        if assertion.claims.purpose != ExternalPurpose.RUNTIME_BIND:
            raise ExternalAuthError()
        async with self.transactions.transaction() as db:
            locked, subject = await self._pending(db, assertion, approval)
            if not await ExternalProvisioningRepository(db).lock_runtime_user(
                approval.runtime_user_id, locked.binding.team_id
            ):
                raise ExternalAuthError()
            bound = await ExternalSubjectRepository(db).bind_runtime(
                subject.subject_id, approval.runtime_user_id
            )
            if bound is None:
                raise ExternalAuthError("identity_binding_conflict", status_code=409)
            await self._audit(
                db,
                assertion,
                approval,
                locked,
                correlation_id,
                AuditAction.EXTERNAL_AUTH_RUNTIME_BIND,
            )
            return bound

    async def _pending(
        self,
        db: PlatformAccountDatabase,
        assertion: VerifiedExternalAssertion,
        approval: ExternalLinkApproval,
    ) -> tuple[LockedExternalBinding, ExternalSubjectRecord]:
        if approval.binding_id != assertion.claims.binding_id:
            raise ExternalAuthError("identity_binding_conflict", status_code=409)
        locked = await lock_external_binding(db, assertion)
        repository = ExternalSubjectRepository(db)
        subject = await repository.lock_identity(assertion)
        if subject is None:
            subject = await repository.create_shell(assertion)
        require_subject_binding(subject, locked.binding)
        if (
            subject.state != "pending"
            or subject.version != approval.expected_version
            or subject.runtime_user_id not in (None, approval.runtime_user_id)
        ):
            raise ExternalAuthError("identity_binding_conflict", status_code=409)
        return locked, subject

    async def _audit(
        self,
        db: PlatformAccountDatabase,
        assertion: VerifiedExternalAssertion,
        approval: ExternalLinkApproval,
        locked: LockedExternalBinding,
        correlation_id: str,
        action: AuditAction,
    ) -> None:
        await self.audit.write(
            db,
            ExternalAuditEvent(
                action,
                assertion.integration_id,
                correlation_id,
                "success",
                locked.binding.organization_id,
                locked.binding.binding_id,
                approval.account_id,
                assertion.claims.purpose,
                approval.reason,
                approval.approval_reference,
                approval.approved_by,
            ),
        )
