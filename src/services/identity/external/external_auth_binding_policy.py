from __future__ import annotations

from dataclasses import dataclass

from src.auth.external_contracts import VerifiedExternalAssertion
from src.auth.external_errors import ExternalAuthError
from src.db.identity.external.external_auth_bindings import ExternalBindingRepository
from src.db.identity.external.external_auth_records import (
    ExternalBindingRecord,
    ExternalIntegrationRecord,
    ExternalSubjectRecord,
)
from src.db.identity.platform_accounts import PlatformAccountDatabase


@dataclass(frozen=True, slots=True)
class LockedExternalBinding:
    integration: ExternalIntegrationRecord
    binding: ExternalBindingRecord


async def lock_external_binding(
    db: PlatformAccountDatabase, assertion: VerifiedExternalAssertion, *, revocation: bool = False
) -> LockedExternalBinding:
    bindings = ExternalBindingRepository(db)
    registered = await bindings.lock_workspace(
        assertion.claims.binding_id, assertion.integration_id
    )
    if registered is None:
        raise ExternalAuthError()
    integration, binding = registered.integration, registered.binding
    if (
        not revocation and not integration.enabled
    ) or binding.external_customer_id != assertion.claims.external_customer_id:
        raise ExternalAuthError()
    if not revocation and (
        binding.state != "active" or not await bindings.lock_active_tenant(binding)
    ):
        raise ExternalAuthError()
    return LockedExternalBinding(integration, binding)


def require_subject_binding(subject: ExternalSubjectRecord, binding: ExternalBindingRecord) -> None:
    if subject.integration_id != binding.integration_id or subject.binding_id != binding.binding_id:
        raise ExternalAuthError("identity_binding_conflict", status_code=409)
