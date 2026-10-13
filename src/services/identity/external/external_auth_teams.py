from __future__ import annotations

from src.audit.actions import AuditAction
from src.auth.external_config import ExternalAuthSettings
from src.auth.external_errors import ExternalAuthError
from src.db.identity.external.external_auth_bindings import ExternalBindingRepository
from src.db.identity.external.external_auth_integrations import ExternalIntegrationRepository
from src.db.identity.external.external_auth_records import (
    ExternalBindingRecord,
    ExternalBindingTeamRecord,
    ExternalBindingTeamView,
    ExternalSubjectTeamRecord,
    ExternalSubjectTeamsView,
)
from src.db.identity.external.external_auth_subjects import ExternalSubjectRepository
from src.db.identity.external.external_auth_teams import ExternalTeamRepository
from src.db.identity.external.external_auth_transactions import ExternalAuthTransactions
from src.db.identity.platform_accounts import PlatformAccountDatabase
from src.db.runtime.cache_invalidation_outbox import CacheInvalidationOutboxRepository
from src.models.external_auth import ExternalTeamGrantRequest, ExternalVersionRequest
from src.services.identity.external.external_auth_audit import ExternalAuditEvent, ExternalAuthAudit


def _conflict() -> ExternalAuthError:
    return ExternalAuthError("identity_binding_conflict", status_code=409)


class ExternalTeamAdministration:
    """Operator management of a binding's additional teams and subject grants.

    The binding's own team is the immutable default team. Every subject of the
    binding holds it implicitly, so it cannot be attached, detached, granted or
    revoked here; subject suspension controls it.
    """

    def __init__(
        self,
        transactions: ExternalAuthTransactions,
        audit: ExternalAuthAudit,
        settings: ExternalAuthSettings,
    ) -> None:
        self.transactions = transactions
        self.audit = audit
        self.identity_issuers = {
            item.integration_id: item.identity_issuer for item in settings.integrations
        }

    async def _binding(self, db: PlatformAccountDatabase, binding_id: str) -> ExternalBindingRecord:
        bindings = ExternalBindingRepository(db)
        reference = await bindings.get(binding_id)
        if reference is None or reference.integration_id not in self.identity_issuers:
            raise ExternalAuthError()
        await ExternalIntegrationRepository(db).lock(reference.integration_id)
        binding = await bindings.lock(binding_id, reference.integration_id)
        if binding is None:
            raise ExternalAuthError()
        return binding

    async def attach(
        self,
        binding_id: str,
        team_id: str,
        *,
        request: ExternalVersionRequest,
        correlation_id: str,
        approved_by: str,
    ) -> ExternalBindingTeamRecord:
        async with self.transactions.transaction() as db:
            binding = await self._binding(db, binding_id)
            if team_id == binding.team_id:
                raise _conflict()
            if not await ExternalBindingRepository(db).lock_tenant_ids(
                binding.organization_id, team_id
            ):
                raise ExternalAuthError()
            teams = ExternalTeamRepository(db)
            current = await teams.lock_attachment(binding_id, team_id)
            if current is not None and current.state == "active":
                return current
            if current is None and request.expected_version != 0:
                raise _conflict()
            attached = await teams.attach(binding_id, team_id, version=request.expected_version)
            if attached is None:
                raise _conflict()
            await self._audit(
                db,
                AuditAction.EXTERNAL_AUTH_TEAM_ATTACH,
                binding,
                team_id,
                request.reason,
                correlation_id,
                approved_by,
            )
            return attached

    async def detach(
        self,
        binding_id: str,
        team_id: str,
        *,
        request: ExternalVersionRequest,
        correlation_id: str,
        approved_by: str,
    ) -> ExternalBindingTeamRecord:
        async with self.transactions.transaction() as db:
            binding = await self._binding(db, binding_id)
            if team_id == binding.team_id:
                raise _conflict()
            teams = ExternalTeamRepository(db)
            current = await teams.lock_attachment(binding_id, team_id)
            if current is None:
                raise ExternalAuthError()
            if current.state == "detached":
                return current
            detached = await teams.detach(current, version=request.expected_version)
            if detached is None:
                raise _conflict()
            if await teams.withdraw(binding_id=binding_id, team_id=team_id):
                await CacheInvalidationOutboxRepository(db).enqueue(
                    scope_type="team", scope_id=team_id, reason="external_team_detached"
                )
            await self._audit(
                db,
                AuditAction.EXTERNAL_AUTH_TEAM_DETACH,
                binding,
                team_id,
                request.reason,
                correlation_id,
                approved_by,
            )
            return detached

    async def grant(
        self,
        binding_id: str,
        team_id: str,
        *,
        request: ExternalTeamGrantRequest,
        correlation_id: str,
        approved_by: str,
    ) -> ExternalSubjectTeamRecord:
        async with self.transactions.transaction() as db:
            binding = await self._binding(db, binding_id)
            if team_id == binding.team_id:
                raise _conflict()
            if request.identity_issuer != self.identity_issuers[binding.integration_id]:
                raise ExternalAuthError()
            subjects = ExternalSubjectRepository(db)
            subject = await subjects.lock_by_identity(request.identity_issuer, request.subject)
            if subject is None:
                # Invite ahead of first sign-in. The account and runtime users are
                # created on the first exchange.
                subject = await subjects.create_pending(
                    integration_id=binding.integration_id,
                    binding_id=binding_id,
                    identity_issuer=request.identity_issuer,
                    subject=request.subject,
                )
            if subject.binding_id != binding_id or subject.integration_id != binding.integration_id:
                raise _conflict()
            if subject.state == "suspended":
                # A grant would restore team access that suspension must keep closed.
                raise _conflict()
            teams = ExternalTeamRepository(db)
            attachment = await teams.lock_attachment(binding_id, team_id)
            if attachment is None or attachment.state != "active":
                raise ExternalAuthError()
            current = await teams.lock_grant(subject.subject_id, team_id)
            if current is not None and current.state == "active":
                return current
            if current is None and request.expected_version != 0:
                raise _conflict()
            granted = await teams.grant(
                subject_id=subject.subject_id,
                binding_id=binding_id,
                team_id=team_id,
                version=request.expected_version,
            )
            if granted is None:
                raise _conflict()
            if current is not None and granted.runtime_user_id is not None:
                await teams.restore_access(granted)
            await self._audit(
                db,
                AuditAction.EXTERNAL_AUTH_TEAM_GRANT,
                binding,
                team_id,
                request.reason,
                correlation_id,
                approved_by,
                subject.account_id,
            )
            return granted

    async def revoke(
        self,
        binding_id: str,
        team_id: str,
        subject_id: str,
        *,
        request: ExternalVersionRequest,
        correlation_id: str,
        approved_by: str,
    ) -> ExternalSubjectTeamRecord:
        async with self.transactions.transaction() as db:
            binding = await self._binding(db, binding_id)
            if team_id == binding.team_id:
                raise _conflict()
            teams = ExternalTeamRepository(db)
            current = await teams.lock_grant(subject_id, team_id)
            if current is None or current.binding_id != binding_id:
                raise ExternalAuthError()
            if current.state == "removed":
                return current
            removed = await teams.withdraw(
                binding_id=binding_id,
                team_id=team_id,
                subject_id=subject_id,
                version=request.expected_version,
            )
            if not removed:
                raise _conflict()
            if current.runtime_user_id is not None:
                await CacheInvalidationOutboxRepository(db).enqueue(
                    scope_type="user",
                    scope_id=current.runtime_user_id,
                    reason="external_team_grant_removed",
                )
            subject = await ExternalSubjectRepository(db).get(subject_id)
            await self._audit(
                db,
                AuditAction.EXTERNAL_AUTH_TEAM_REVOKE,
                binding,
                team_id,
                request.reason,
                correlation_id,
                approved_by,
                subject.account_id if subject else None,
            )
            return removed[0]

    async def list_teams(
        self, binding_id: str, *, after: str | None, limit: int
    ) -> tuple[list[ExternalBindingTeamView], str | None]:
        """Return one page of teams and the next cursor.

        The first page starts with the implicit default team, which takes one of
        its `limit` slots. A page that holds only the default team continues with
        the empty cursor, which sorts before every attached team.
        """

        async with self.transactions.transaction("validation") as db:
            binding = await ExternalBindingRepository(db).get(binding_id)
            if binding is None or binding.integration_id not in self.identity_issuers:
                raise ExternalAuthError()
            views = []
            if after is None:
                views.append(
                    ExternalBindingTeamView(
                        binding_id=binding_id,
                        team_id=binding.team_id,
                        is_default=True,
                        state="active",
                        version=None,
                    )
                )
            room = limit - len(views)
            rows = await ExternalTeamRepository(db).list_attachments(
                binding_id, after=after, limit=room + 1
            )
            for row in rows[:room]:
                views.append(
                    ExternalBindingTeamView(
                        binding_id=row.binding_id,
                        team_id=row.team_id,
                        is_default=False,
                        state=row.state,
                        version=row.version,
                    )
                )
            if len(rows) <= room:
                return views, None
            return views, "" if views[-1].is_default else views[-1].team_id

    async def list_subjects(
        self, binding_id: str, *, team_id: str | None, after: str | None, limit: int
    ) -> list[ExternalSubjectTeamsView]:
        async with self.transactions.transaction("validation") as db:
            binding = await ExternalBindingRepository(db).get(binding_id)
            if binding is None or binding.integration_id not in self.identity_issuers:
                raise ExternalAuthError()
            rows = await ExternalTeamRepository(db).list_subjects(
                binding_id, team_id=team_id, after=after, limit=limit
            )
            return [ExternalSubjectTeamsView.model_validate(row) for row in rows]

    async def _audit(
        self,
        db: PlatformAccountDatabase,
        action: AuditAction,
        binding: ExternalBindingRecord,
        team_id: str,
        reason: str,
        correlation_id: str,
        approved_by: str,
        account_id: str | None = None,
    ) -> None:
        await self.audit.write(
            db,
            ExternalAuditEvent(
                action,
                binding.integration_id,
                correlation_id,
                "success",
                binding.organization_id,
                binding.binding_id,
                account_id,
                reason=reason,
                approved_by=approved_by,
                team_id=team_id,
            ),
        )
