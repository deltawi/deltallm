from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.db.organization_deletion_records import (
    OrganizationDeletionCounts,
    OrganizationDeletionJobRecord,
    OrganizationDeletionPlanRecord,
)
from src.services.organization_deletion import (
    OrganizationDeletionConflictError,
    OrganizationDeletionRequestsDisabledError,
    OrganizationDeletionService,
    OrganizationDeletionUnavailableError,
    OrganizationDeletionValidationError,
)


class _FakeTransaction:
    def __init__(self, repository: _FakeRepository) -> None:
        self.repository = repository
        self.snapshot: tuple[dict, dict, int, list] | None = None

    async def __aenter__(self) -> _FakeRepository:
        self.snapshot = (
            deepcopy(self.repository.organizations),
            deepcopy(self.repository.jobs),
            self.repository.generation,
            deepcopy(self.repository.outbox),
        )
        return self.repository

    async def __aexit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
        del exc, tb
        if exc_type is not None and self.snapshot is not None:
            (
                self.repository.organizations,
                self.repository.jobs,
                self.repository.generation,
                self.repository.outbox,
            ) = self.snapshot
        return False


class _FakeRepository:
    def __init__(self) -> None:
        self.prisma = self
        self.organizations = {
            "org-1": {
                "organization_id": "org-1",
                "organization_name": "Acme",
                "lifecycle_state": "active",
                "lifecycle_version": 0,
                "deletion_requested_at": None,
                "deletion_not_before_at": None,
                "deletion_job_id": None,
            }
        }
        self.jobs: dict[str, OrganizationDeletionJobRecord] = {}
        self.generation = 0
        self.outbox: list[dict[str, str]] = []
        self.plan_reads = 0
        self.change_impact_on_read: int | None = None
        self.external_mcp_dependencies = 0
        self.ambiguous_sensitive_records = 0
        self.unresolved_batch_ownership_records = 0
        self.retained_audit_events = 0
        self.active_batches = 1

    def tx(self) -> _FakeTransaction:
        return _FakeTransaction(self)

    def with_db(self, tx):  # noqa: ANN001, ANN201
        return tx

    def supports_transactions(self) -> bool:
        return True

    async def get_plan(self, organization_id: str):  # noqa: ANN201
        self.plan_reads += 1
        row = self.organizations.get(organization_id)
        if row is None:
            return None
        return OrganizationDeletionPlanRecord(
            organization_id=organization_id,
            organization_name=str(row["organization_name"]),
            lifecycle_state=str(row["lifecycle_state"]),
            lifecycle_version=int(row["lifecycle_version"]),
            deletion_requested_at=row["deletion_requested_at"],
            deletion_not_before_at=row["deletion_not_before_at"],
            deletion_job_id=row["deletion_job_id"],
            counts=OrganizationDeletionCounts(
                teams=(4 if self.change_impact_on_read == self.plan_reads else 3),
                api_keys=7,
                active_batches=self.active_batches,
                external_mcp_dependencies=self.external_mcp_dependencies,
                ambiguous_sensitive_records=self.ambiguous_sensitive_records,
                unresolved_batch_ownership_records=self.unresolved_batch_ownership_records,
                retained_audit_events=self.retained_audit_events,
            ),
        )

    async def get_organization_for_update(self, organization_id: str):  # noqa: ANN201
        row = self.organizations.get(organization_id)
        return dict(row) if row else None

    async def get_job_by_idempotency_key(self, *, organization_id: str, idempotency_key: str):  # noqa: ANN201
        return next(
            (
                job
                for job in self.jobs.values()
                if job.organization_id == organization_id and job.idempotency_key == idempotency_key
            ),
            None,
        )

    async def create_job(self, **fields):  # noqa: ANN003, ANN201
        job = OrganizationDeletionJobRecord(
            deletion_job_id=str(uuid4()),
            organization_id=str(fields["organization_id"]),
            status="pending",
            phase="cancel_pending",
            requested_by_account_id=fields["requested_by_account_id"],
            idempotency_key=str(fields["idempotency_key"]),
            request_hash=str(fields["request_hash"]),
            plan_token=str(fields["plan_token"]),
            plan_snapshot=dict(fields["plan_snapshot"]),
            options=dict(fields["options"]),
            not_before_at=fields["not_before_at"],
            max_attempts=int(fields["max_attempts"]),
            next_attempt_at=datetime.now(tz=UTC),
        )
        self.jobs[job.deletion_job_id] = job
        return job

    async def mark_organization_deletion_pending(
        self,
        *,
        organization_id: str,
        deletion_job_id: str,
        not_before_at: datetime,
    ) -> bool:
        row = self.organizations[organization_id]
        if row["lifecycle_state"] != "active":
            return False
        row.update(
            lifecycle_state="deletion_pending",
            lifecycle_version=int(row["lifecycle_version"]) + 1,
            deletion_requested_at=datetime.now(tz=UTC),
            deletion_not_before_at=not_before_at,
            deletion_job_id=deletion_job_id,
        )
        return True

    async def increment_lifecycle_generation(self) -> int:
        self.generation += 1
        return self.generation

    async def get_job(
        self,
        *,
        organization_id: str,
        deletion_job_id: str,
        for_update: bool = False,
    ):  # noqa: ANN201
        del for_update
        job = self.jobs.get(deletion_job_id)
        return job if job and job.organization_id == organization_id else None

    async def restore_organization(self, *, organization_id: str, deletion_job_id: str) -> bool:
        row = self.organizations[organization_id]
        if row["deletion_job_id"] != deletion_job_id:
            return False
        row.update(
            lifecycle_state="active",
            lifecycle_version=int(row["lifecycle_version"]) + 1,
            deletion_requested_at=None,
            deletion_not_before_at=None,
            deletion_job_id=None,
        )
        return True

    async def mark_job_restored(self, *, deletion_job_id: str) -> bool:
        job = self.jobs[deletion_job_id]
        self.jobs[deletion_job_id] = OrganizationDeletionJobRecord(
            **{
                **job.__dict__,
                "status": "restored",
                "phase": "restored",
                "restored_at": datetime.now(tz=UTC),
            }
        )
        return True


class _FakeOutboxRepository:
    fail = False

    def __init__(self, repository: _FakeRepository) -> None:
        self.repository = repository

    async def enqueue(self, **fields):  # noqa: ANN003, ANN201
        if self.fail:
            raise RuntimeError("outbox unavailable")
        self.repository.outbox.append(
            {
                "scope_type": str(fields["scope_type"]),
                "scope_id": str(fields["scope_id"]),
                "reason": str(fields["reason"]),
            }
        )
        return SimpleNamespace(invalidation_id=str(uuid4()))


class _FakeCacheInvalidationService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def invalidate_organization_cache_now(self, organization_id: str, *, reason: str):  # noqa: ANN201
        self.calls.append((organization_id, reason))
        return SimpleNamespace(invalidated=True)


class _FakeWorkerRepository:
    def __init__(self, repository: _FakeRepository) -> None:
        self.repository = repository

    async def retry_failed(
        self,
        *,
        organization_id: str,
        deletion_job_id: str,
        retried_by_account_id: str | None,
    ) -> bool:
        del retried_by_account_id
        job = self.repository.jobs[deletion_job_id]
        if job.organization_id != organization_id or job.status != "failed":
            return False
        self.repository.jobs[deletion_job_id] = OrganizationDeletionJobRecord(
            **{
                **job.__dict__,
                "status": "pending",
                "last_error_code": None,
                "last_error_detail": None,
            }
        )
        self.repository.organizations[organization_id]["lifecycle_state"] = "deletion_pending"
        return True


@pytest.fixture
def deletion_service(monkeypatch):  # noqa: ANN001, ANN201
    repository = _FakeRepository()
    cache = _FakeCacheInvalidationService()
    monkeypatch.setattr(
        "src.services.organization_deletion.CacheInvalidationOutboxRepository",
        _FakeOutboxRepository,
    )
    monkeypatch.setattr(
        "src.services.organization_deletion_request.CacheInvalidationOutboxRepository",
        _FakeOutboxRepository,
    )

    async def _record_audit(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        del args, kwargs

    monkeypatch.setattr(
        "src.services.organization_deletion.record_lifecycle_mutation_audit",
        _record_audit,
    )
    monkeypatch.setattr(
        "src.services.organization_deletion_request.record_lifecycle_mutation_audit",
        _record_audit,
    )
    return (
        OrganizationDeletionService(
            repository=repository,  # type: ignore[arg-type]
            cache_invalidation_service=cache,
            worker_repository=_FakeWorkerRepository(repository),  # type: ignore[arg-type]
            recovery_window_hours=24,
            requests_enabled=True,
        ),
        repository,
        cache,
    )


@pytest.mark.asyncio
async def test_preview_returns_stable_typed_impact(deletion_service) -> None:  # noqa: ANN001
    service, _repository, _cache = deletion_service

    first = await service.preview("org-1")
    second = await service.preview("org-1")

    assert first.plan_token == second.plan_token
    assert first.record.counts.teams == 3
    assert first.record.counts.api_keys == 7
    assert first.record.counts.active_batches == 1
    assert first.recovery_window_hours == 24
    assert first.requests_enabled is True
    assert first.can_request is True


@pytest.mark.asyncio
async def test_requests_are_disabled_by_default() -> None:
    repository = _FakeRepository()
    service = OrganizationDeletionService(
        repository=repository,  # type: ignore[arg-type]
        cache_invalidation_service=None,
    )

    preview = await service.preview("org-1")

    assert preview.requests_enabled is False
    assert preview.can_request is False
    with pytest.raises(OrganizationDeletionRequestsDisabledError):
        await service.request_deletion(
            organization_id="org-1",
            confirmation_name="Acme",
            plan_token=preview.plan_token,
            idempotency_key="request-disabled",
            requested_by_account_id="account-1",
        )
    assert repository.jobs == {}
    assert repository.organizations["org-1"]["lifecycle_state"] == "active"


@pytest.mark.asyncio
async def test_request_is_atomic_idempotent_and_invalidates_auth(deletion_service) -> None:  # noqa: ANN001
    service, repository, cache = deletion_service
    preview = await service.preview("org-1")

    result = await service.request_deletion(
        organization_id="org-1",
        confirmation_name="Acme",
        plan_token=preview.plan_token,
        idempotency_key="request-1",
        requested_by_account_id="account-1",
    )
    repeated = await service.request_deletion(
        organization_id="org-1",
        confirmation_name="Acme",
        plan_token=preview.plan_token,
        idempotency_key="request-1",
        requested_by_account_id="account-1",
    )

    assert repeated.job.deletion_job_id == result.job.deletion_job_id
    assert repository.organizations["org-1"]["lifecycle_state"] == "deletion_pending"
    assert repository.generation == 1
    assert repository.outbox == [
        {
            "scope_type": "organization",
            "scope_id": "org-1",
            "reason": "organization_deletion_requested",
        }
    ]
    assert cache.calls == [("org-1", "organization_deletion_requested")]


@pytest.mark.asyncio
async def test_request_rejects_stale_plan_and_wrong_confirmation(deletion_service) -> None:  # noqa: ANN001
    service, repository, _cache = deletion_service
    preview = await service.preview("org-1")

    with pytest.raises(OrganizationDeletionConflictError) as stale:
        await service.request_deletion(
            organization_id="org-1",
            confirmation_name="Acme",
            plan_token="0" * 64,
            idempotency_key="request-1",
            requested_by_account_id="account-1",
        )
    assert stale.value.code == "organization_deletion_plan_stale"

    with pytest.raises(OrganizationDeletionValidationError):
        await service.request_deletion(
            organization_id="org-1",
            confirmation_name="acme",
            plan_token=preview.plan_token,
            idempotency_key="request-2",
            requested_by_account_id="account-1",
        )
    assert repository.jobs == {}
    assert repository.organizations["org-1"]["lifecycle_state"] == "active"


@pytest.mark.asyncio
async def test_request_revalidates_impact_inside_lifecycle_transaction(
    deletion_service,
) -> None:  # noqa: ANN001
    service, repository, _cache = deletion_service
    preview = await service.preview("org-1")
    repository.change_impact_on_read = 2

    with pytest.raises(OrganizationDeletionConflictError) as exc_info:
        await service.request_deletion(
            organization_id="org-1",
            confirmation_name="Acme",
            plan_token=preview.plan_token,
            idempotency_key="request-1",
            requested_by_account_id="account-1",
        )

    assert exc_info.value.code == "organization_deletion_plan_stale"
    assert repository.jobs == {}
    assert repository.organizations["org-1"]["lifecycle_state"] == "active"


@pytest.mark.asyncio
async def test_request_ignores_volatile_telemetry_changes_inside_lifecycle_transaction(
    deletion_service,
) -> None:  # noqa: ANN001
    service, repository, _cache = deletion_service
    preview = await service.preview("org-1")
    repository.retained_audit_events = 1
    repository.active_batches = 0

    result = await service.request_deletion(
        organization_id="org-1",
        confirmation_name="Acme",
        plan_token=preview.plan_token,
        idempotency_key="request-volatile-telemetry",
        requested_by_account_id="account-1",
    )

    assert result.job.plan_token == preview.plan_token
    assert repository.organizations["org-1"]["lifecycle_state"] == "deletion_pending"


@pytest.mark.asyncio
async def test_external_asset_dependencies_block_preview_and_request(
    deletion_service,
) -> None:  # noqa: ANN001
    service, repository, _cache = deletion_service
    repository.external_mcp_dependencies = 2
    preview = await service.preview("org-1")

    assert preview.can_request is False
    with pytest.raises(OrganizationDeletionConflictError) as exc_info:
        await service.request_deletion(
            organization_id="org-1",
            confirmation_name="Acme",
            plan_token=preview.plan_token,
            idempotency_key="request-with-dependencies",
            requested_by_account_id="account-1",
        )

    assert exc_info.value.code == "organization_deletion_asset_dependencies"
    assert repository.jobs == {}
    assert repository.organizations["org-1"]["lifecycle_state"] == "active"


@pytest.mark.asyncio
async def test_ambiguous_sensitive_records_block_request(deletion_service) -> None:  # noqa: ANN001
    service, repository, _cache = deletion_service
    repository.ambiguous_sensitive_records = 1
    preview = await service.preview("org-1")

    assert preview.can_request is False
    with pytest.raises(OrganizationDeletionConflictError) as exc_info:
        await service.request_deletion(
            organization_id="org-1",
            confirmation_name="Acme",
            plan_token=preview.plan_token,
            idempotency_key="request-with-ambiguous-history",
            requested_by_account_id="account-1",
        )

    assert exc_info.value.code == "organization_deletion_ambiguous_sensitive_records"
    assert repository.jobs == {}


@pytest.mark.asyncio
async def test_unresolved_batch_ownership_blocks_request(deletion_service) -> None:  # noqa: ANN001
    service, repository, _cache = deletion_service
    repository.unresolved_batch_ownership_records = 1
    preview = await service.preview("org-1")

    assert preview.can_request is False
    with pytest.raises(OrganizationDeletionConflictError) as exc_info:
        await service.request_deletion(
            organization_id="org-1",
            confirmation_name="Acme",
            plan_token=preview.plan_token,
            idempotency_key="request-with-unresolved-batch-ownership",
            requested_by_account_id="account-1",
        )

    assert exc_info.value.code == "organization_deletion_unresolved_batch_ownership"
    assert repository.jobs == {}


@pytest.mark.asyncio
async def test_outbox_failure_rolls_back_lifecycle_and_job(deletion_service) -> None:  # noqa: ANN001
    service, repository, _cache = deletion_service
    preview = await service.preview("org-1")
    _FakeOutboxRepository.fail = True
    try:
        with pytest.raises(OrganizationDeletionUnavailableError):
            await service.request_deletion(
                organization_id="org-1",
                confirmation_name="Acme",
                plan_token=preview.plan_token,
                idempotency_key="request-1",
                requested_by_account_id="account-1",
            )
    finally:
        _FakeOutboxRepository.fail = False

    assert repository.jobs == {}
    assert repository.generation == 0
    assert repository.organizations["org-1"]["lifecycle_state"] == "active"


@pytest.mark.asyncio
async def test_restore_reactivates_before_irreversible_phase(deletion_service) -> None:  # noqa: ANN001
    service, repository, cache = deletion_service
    preview = await service.preview("org-1")
    requested = await service.request_deletion(
        organization_id="org-1",
        confirmation_name="Acme",
        plan_token=preview.plan_token,
        idempotency_key="request-1",
        requested_by_account_id="account-1",
    )

    restored = await service.restore(
        organization_id="org-1",
        deletion_job_id=requested.job.deletion_job_id,
    )

    assert restored.job.status == "restored"
    assert repository.organizations["org-1"]["lifecycle_state"] == "active"
    assert repository.generation == 2
    assert cache.calls[-1] == ("org-1", "organization_deletion_restored")


@pytest.mark.asyncio
async def test_restore_rejects_expired_window(deletion_service) -> None:  # noqa: ANN001
    service, repository, _cache = deletion_service
    preview = await service.preview("org-1")
    requested = await service.request_deletion(
        organization_id="org-1",
        confirmation_name="Acme",
        plan_token=preview.plan_token,
        idempotency_key="request-1",
        requested_by_account_id="account-1",
    )
    job = repository.jobs[requested.job.deletion_job_id]
    repository.jobs[job.deletion_job_id] = OrganizationDeletionJobRecord(
        **{**job.__dict__, "not_before_at": datetime.now(tz=UTC) - timedelta(seconds=1)}
    )

    with pytest.raises(OrganizationDeletionConflictError) as exc_info:
        await service.restore(
            organization_id="org-1",
            deletion_job_id=job.deletion_job_id,
        )
    assert exc_info.value.code == "organization_deletion_restore_closed"


@pytest.mark.asyncio
async def test_retry_reschedules_only_failed_job(deletion_service) -> None:  # noqa: ANN001
    service, repository, cache = deletion_service
    preview = await service.preview("org-1")
    requested = await service.request_deletion(
        organization_id="org-1",
        confirmation_name="Acme",
        plan_token=preview.plan_token,
        idempotency_key="request-1",
        requested_by_account_id="account-1",
    )
    repository.jobs[requested.job.deletion_job_id] = OrganizationDeletionJobRecord(
        **{
            **requested.job.__dict__,
            "status": "failed",
            "last_error_code": "cleanup_failed",
            "last_error_detail": "temporary database error",
        }
    )
    repository.organizations["org-1"]["lifecycle_state"] = "deletion_failed"

    retried = await service.retry_failed(
        organization_id="org-1",
        deletion_job_id=requested.job.deletion_job_id,
        retried_by_account_id="account-2",
    )

    assert retried.job.status == "pending"
    assert retried.job.last_error_code is None
    assert repository.organizations["org-1"]["lifecycle_state"] == "deletion_pending"
    assert cache.calls[-1] == ("org-1", "organization_deletion_retried")
