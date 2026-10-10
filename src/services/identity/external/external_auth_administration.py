from __future__ import annotations

from src.audit.actions import AuditAction
from src.auth.external_config import ExternalAuthSettings
from src.auth.external_errors import ExternalAuthError
from src.db.identity.external.external_auth_bindings import ExternalBindingRepository
from src.db.identity.external.external_auth_integrations import ExternalIntegrationRepository
from src.db.identity.external.external_auth_provisioning import ExternalProvisioningRepository
from src.db.identity.external.external_auth_records import (
    ExternalBindingRecord,
    ExternalIntegrationRecord,
    ExternalSubjectRecord,
)
from src.db.identity.external.external_auth_subjects import ExternalSubjectRepository
from src.db.identity.external.external_auth_transactions import ExternalAuthTransactions
from src.models.external_auth import ExternalVersionRequest
from src.services.identity.external.external_auth_audit import ExternalAuditEvent, ExternalAuthAudit


class ExternalAuthAdministration:
    def __init__(
        self,
        transactions: ExternalAuthTransactions,
        audit: ExternalAuthAudit,
        settings: ExternalAuthSettings,
    ) -> None:
        self.transactions = transactions
        self.audit = audit
        self.configured_ids = frozenset(item.integration_id for item in settings.integrations)

    def require_configured(self, integration_id: str) -> None:
        if integration_id not in self.configured_ids:
            raise ExternalAuthError()

    async def set_integration(
        self,
        integration_id: str,
        *,
        enabled: bool,
        request: ExternalVersionRequest,
        correlation_id: str,
        approved_by: str,
    ) -> ExternalIntegrationRecord:
        self.require_configured(integration_id)
        async with self.transactions.transaction() as db:
            repository = ExternalIntegrationRepository(db)
            await repository.lock(integration_id, write=True)
            updated = await repository.set_enabled(
                integration_id, enabled=enabled, version=request.expected_version
            )
            if updated is None:
                raise ExternalAuthError("identity_binding_conflict", status_code=409)
            await self.audit.write(
                db,
                ExternalAuditEvent(
                    AuditAction.EXTERNAL_AUTH_INTEGRATION_UPDATE,
                    integration_id,
                    correlation_id,
                    "success",
                    reason=request.reason,
                    approved_by=approved_by,
                ),
            )
            return updated

    async def register_binding(
        self,
        integration_id: str,
        *,
        customer_id: str,
        organization_id: str,
        team_id: str,
        correlation_id: str,
        approved_by: str,
    ) -> ExternalBindingRecord:
        self.require_configured(integration_id)
        async with self.transactions.transaction() as db:
            if await ExternalIntegrationRepository(db).lock(integration_id) is None:
                raise ExternalAuthError()
            repository = ExternalBindingRepository(db)
            if not await repository.lock_tenant_ids(organization_id, team_id):
                raise ExternalAuthError()
            binding = await repository.register(
                integration_id=integration_id,
                customer_id=customer_id,
                organization_id=organization_id,
                team_id=team_id,
            )
            if binding.organization_id != organization_id or binding.team_id != team_id:
                raise ExternalAuthError("identity_binding_conflict", status_code=409)
            await self.audit.write(
                db,
                ExternalAuditEvent(
                    AuditAction.EXTERNAL_AUTH_BINDING_REGISTER,
                    integration_id,
                    correlation_id,
                    "success",
                    organization_id,
                    binding.binding_id,
                    approved_by=approved_by,
                ),
            )
            return binding

    async def set_binding(
        self,
        binding_id: str,
        *,
        active: bool,
        request: ExternalVersionRequest,
        correlation_id: str,
        approved_by: str,
    ) -> ExternalBindingRecord:
        async with self.transactions.transaction() as db:
            repository = ExternalBindingRepository(db)
            reference = await repository.get(binding_id)
            if reference is None:
                raise ExternalAuthError()
            self.require_configured(reference.integration_id)
            await ExternalIntegrationRepository(db).lock(reference.integration_id)
            binding = await repository.lock(binding_id, reference.integration_id, write=True)
            if binding is None or (active and not await repository.lock_active_tenant(binding)):
                raise ExternalAuthError()
            result = await repository.set_state(
                binding, state="active" if active else "suspended", version=request.expected_version
            )
            if result is None:
                raise ExternalAuthError("identity_binding_conflict", status_code=409)
            action = (
                AuditAction.EXTERNAL_AUTH_BINDING_RESUME
                if active
                else AuditAction.EXTERNAL_AUTH_BINDING_SUSPEND
            )
            await self.audit.write(
                db,
                ExternalAuditEvent(
                    action,
                    binding.integration_id,
                    correlation_id,
                    "success",
                    binding.organization_id,
                    binding_id,
                    reason=request.reason,
                    approved_by=approved_by,
                ),
            )
            return result

    async def resume_subject(
        self,
        subject_id: str,
        *,
        request: ExternalVersionRequest,
        correlation_id: str,
        approved_by: str,
    ) -> ExternalSubjectRecord:
        async with self.transactions.transaction() as db:
            subjects = ExternalSubjectRepository(db)
            reference = await subjects.get(subject_id)
            if reference is None:
                raise ExternalAuthError()
            self.require_configured(reference.integration_id)
            await ExternalIntegrationRepository(db).lock(reference.integration_id)
            bindings = ExternalBindingRepository(db)
            binding = await bindings.lock(reference.binding_id, reference.integration_id)
            if (
                binding is None
                or binding.state != "active"
                or not await bindings.lock_active_tenant(binding)
            ):
                raise ExternalAuthError()
            subject = await subjects.get(subject_id, lock=True)
            if subject is None or subject.state != "suspended":
                raise ExternalAuthError("identity_binding_conflict", status_code=409)
            if (
                subject.account_id is not None
                and await ExternalProvisioningRepository(db).eligible_account(subject, binding)
                is None
            ):
                raise ExternalAuthError()
            result = await subjects.set_state(
                subject_id,
                state="active" if subject.account_id else "pending",
                version=request.expected_version,
            )
            if result is None:
                raise ExternalAuthError("identity_binding_conflict", status_code=409)
            await self.audit.write(
                db,
                ExternalAuditEvent(
                    AuditAction.EXTERNAL_AUTH_SUBJECT_RESUME,
                    subject.integration_id,
                    correlation_id,
                    "success",
                    binding.organization_id,
                    binding.binding_id,
                    subject.account_id,
                    reason=request.reason,
                    approved_by=approved_by,
                ),
            )
            return result

    async def list_bindings(
        self,
        integration_id: str,
        *,
        after: str | None,
        state: str | None,
        customer: str | None,
        limit: int,
    ) -> list[ExternalBindingRecord]:
        self.require_configured(integration_id)
        async with self.transactions.transaction("validation") as db:
            return await ExternalBindingRepository(db).list_page(
                integration_id=integration_id,
                after=after,
                state=state,
                customer=customer,
                limit=limit,
            )
