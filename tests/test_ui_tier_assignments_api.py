from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
import json
from types import SimpleNamespace
from typing import Any

import pytest

from src.audit.actions import AuditAction
from src.auth.roles import OrganizationRole
from src.db.tiers import OrganizationTierAssignmentRecord
from src.models.platform_auth import PlatformAuthContext
from src.services.cache_invalidation import CacheInvalidationService


class _RecordingAuditService:
    def __init__(self) -> None:
        self.sync_calls: list[tuple[object, list[object]]] = []

    async def record_event_sync(self, event, *, payloads=None):  # noqa: ANN001, ANN201
        self.sync_calls.append((event, list(payloads or [])))

    def record_event(self, event, *, payloads=None, critical=False):  # noqa: ANN001, ANN201
        del event, payloads, critical


class _RecordingKeyService:
    def __init__(self, *, fail: bool = False, delay_seconds: float = 0) -> None:
        self.fail = fail
        self.delay_seconds = delay_seconds
        self.org_invalidation_attempts: list[str] = []
        self.org_invalidations: list[str] = []

    async def invalidate_keys_for_org(self, organization_id: str) -> int:
        self.org_invalidation_attempts.append(organization_id)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.fail:
            raise RuntimeError("cache unavailable")
        self.org_invalidations.append(organization_id)
        return 1


class _RecordingCacheInvalidationOutboxRepository:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.enqueues: list[dict[str, Any]] = []

    async def enqueue(self, **kwargs):  # noqa: ANN003, ANN201
        if self.fail:
            raise RuntimeError("outbox unavailable")
        self.enqueues.append(dict(kwargs))
        return SimpleNamespace(invalidation_id=f"invalidation-{len(self.enqueues)}")


class _RecordingGovernanceInvalidationService:
    def __init__(self, *, fail_local: bool = False, notify_result: bool = True) -> None:
        self.fail_local = fail_local
        self.notify_result = notify_result
        self.local_targets: list[tuple[str, ...]] = []
        self.notified_targets: list[tuple[str, ...]] = []

    async def invalidate_local(self, *targets: str) -> None:
        self.local_targets.append(tuple(targets))
        if self.fail_local:
            raise RuntimeError("tier policy reload unavailable")

    async def notify(self, *targets: str) -> bool:
        self.notified_targets.append(tuple(targets))
        return self.notify_result


class _FakeTierAssignmentRepository:
    def __init__(self) -> None:
        self.organizations = {"org-1"}
        self.organization_lifecycle_states = {"org-1": "active"}
        self.assignments: dict[str, OrganizationTierAssignmentRecord] = {}
        self.upsert_error: str | None = None
        self.upsert_exception: Exception | None = None
        self.require_active_version_for_enabled = False
        self.upsert_calls: list[dict[str, Any]] = []
        self.locked_assignment_reads: list[tuple[str, str]] = []
        self.delete_for_org_calls: list[tuple[str, str]] = []
        self.cache_enqueue_fail = False
        self.cache_enqueues: list[dict[str, Any]] = []
        self.tx_started = 0
        self.tx_committed = 0
        self.tx_rolled_back = 0
        self.prisma = SimpleNamespace(tx=lambda: _FakeTierAssignmentTxContext(self))

    def supports_transactions(self) -> bool:
        return True

    def with_db(self, tx):  # noqa: ANN001, ANN201
        return tx

    def seed_assignment(
        self,
        *,
        assignment_id: str = "assignment-1",
        organization_id: str = "org-1",
        tier_id: str = "tier-1",
        tier_version_id: str | None = "version-1",
        assignment_type: str = "primary",
        enabled: bool = True,
        weight: int = 1,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OrganizationTierAssignmentRecord:
        now = datetime.now(tz=UTC)
        record = OrganizationTierAssignmentRecord(
            assignment_id=assignment_id,
            organization_id=organization_id,
            tier_id=tier_id,
            tier_version_id=tier_version_id,
            assignment_type=assignment_type,
            enabled=enabled,
            weight=weight,
            starts_at=starts_at,
            ends_at=ends_at,
            metadata=metadata,
            tier_key=f"{tier_id}-key",
            tier_name=f"{tier_id} name",
            tier_version_number=1 if tier_version_id else None,
            tier_version_status="active" if tier_version_id else None,
            created_at=now,
            updated_at=now,
        )
        self.assignments[assignment_id] = record
        return record

    async def organization_exists_for_tier_assignment(self, organization_id: str) -> bool:
        return organization_id in self.organizations

    async def list_org_assignments(
        self,
        organization_id: str,
        *,
        enabled: bool | None = None,
    ) -> list[OrganizationTierAssignmentRecord]:
        records = [
            record
            for record in self.assignments.values()
            if record.organization_id == organization_id
        ]
        if enabled is not None:
            records = [record for record in records if record.enabled is enabled]
        return sorted(records, key=lambda item: item.assignment_id)

    async def get_org_assignment(
        self,
        assignment_id: str,
    ) -> OrganizationTierAssignmentRecord | None:
        return self.assignments.get(assignment_id)

    async def get_org_assignment_for_update(
        self,
        *,
        assignment_id: str,
        organization_id: str,
    ) -> OrganizationTierAssignmentRecord | None:
        self.locked_assignment_reads.append((assignment_id, organization_id))
        record = self.assignments.get(assignment_id)
        if record is None or record.organization_id != organization_id:
            return None
        return record

    async def upsert_org_assignment(
        self,
        *,
        organization_id: str,
        tier_id: str,
        tier_version_id: str | None = None,
        assignment_id: str | None = None,
        assignment_type: str = "primary",
        enabled: bool = True,
        weight: int = 1,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OrganizationTierAssignmentRecord | None:
        call = {
            "organization_id": organization_id,
            "tier_id": tier_id,
            "tier_version_id": tier_version_id,
            "assignment_id": assignment_id,
            "assignment_type": assignment_type,
            "enabled": enabled,
            "weight": weight,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "metadata": metadata,
        }
        self.upsert_calls.append(call)
        if self.upsert_exception is not None:
            raise self.upsert_exception
        if self.upsert_error is not None:
            raise ValueError(self.upsert_error)
        if self.require_active_version_for_enabled and enabled:
            raise ValueError("tier_version_id must reference an active tier version")

        now = datetime.now(tz=UTC)
        if assignment_id is not None:
            existing = self.assignments.get(assignment_id)
            if existing is None or existing.organization_id != organization_id:
                return None
            updated = replace(
                existing,
                organization_id=organization_id,
                tier_id=tier_id,
                tier_version_id=tier_version_id,
                assignment_type=assignment_type,
                enabled=enabled,
                weight=weight,
                starts_at=starts_at,
                ends_at=ends_at,
                metadata=metadata,
                tier_version_number=1 if tier_version_id else None,
                tier_version_status="active" if tier_version_id else None,
                updated_at=now,
            )
            self.assignments[assignment_id] = updated
            return updated

        new_assignment_id = f"assignment-{len(self.assignments) + 1}"
        return self.seed_assignment(
            assignment_id=new_assignment_id,
            organization_id=organization_id,
            tier_id=tier_id,
            tier_version_id=tier_version_id,
            assignment_type=assignment_type,
            enabled=enabled,
            weight=weight,
            starts_at=starts_at,
            ends_at=ends_at,
            metadata=metadata,
        )

    async def upsert_org_assignment_in_current_transaction(
        self,
        **kwargs,  # noqa: ANN003
    ) -> OrganizationTierAssignmentRecord | None:
        return await self.upsert_org_assignment(**kwargs)

    async def delete_org_assignment(self, assignment_id: str) -> bool:
        return self.assignments.pop(assignment_id, None) is not None

    async def delete_org_assignment_for_org(
        self,
        *,
        assignment_id: str,
        organization_id: str,
    ) -> bool:
        self.delete_for_org_calls.append((assignment_id, organization_id))
        record = self.assignments.get(assignment_id)
        if record is None or record.organization_id != organization_id:
            return False
        del self.assignments[assignment_id]
        return True

    async def query_raw(self, sql: str, *params: object) -> list[dict[str, object]]:
        if "FROM deltallm_organizationtable" in sql:
            organization_id = str(params[0])
            lifecycle_state = self.organization_lifecycle_states.get(organization_id)
            return (
                [
                    {
                        "organization_id": organization_id,
                        "lifecycle_state": lifecycle_state,
                    }
                ]
                if lifecycle_state is not None
                else []
            )
        if "INSERT INTO deltallm_cacheinvalidationoutbox" not in sql:
            return []
        if self.cache_enqueue_fail:
            raise RuntimeError("outbox unavailable")
        metadata = json.loads(str(params[4])) if params[4] is not None else None
        self.cache_enqueues.append(
            {
                "scope_type": params[1],
                "scope_id": params[2],
                "reason": params[3],
                "metadata": metadata,
                "max_attempts": params[5],
            }
        )
        now = datetime.now(tz=UTC)
        return [
            {
                "invalidation_id": params[0],
                "scope_type": params[1],
                "scope_id": params[2],
                "reason": params[3],
                "metadata": metadata,
                "status": "pending",
                "attempt_count": 0,
                "max_attempts": params[5],
                "next_attempt_at": params[6] or now,
                "last_error": None,
                "locked_by": None,
                "lease_expires_at": None,
                "created_at": now,
                "updated_at": now,
                "processed_at": None,
            }
        ]


class _FakeTierAssignmentTxContext:
    def __init__(self, root: _FakeTierAssignmentRepository) -> None:
        self.root = root
        self.tx: _FakeTierAssignmentRepository | None = None

    async def __aenter__(self) -> _FakeTierAssignmentRepository:
        self.root.tx_started += 1
        tx = _FakeTierAssignmentRepository()
        tx.organizations = set(self.root.organizations)
        tx.organization_lifecycle_states = dict(self.root.organization_lifecycle_states)
        tx.assignments = dict(self.root.assignments)
        tx.upsert_error = self.root.upsert_error
        tx.upsert_exception = self.root.upsert_exception
        tx.require_active_version_for_enabled = self.root.require_active_version_for_enabled
        tx.locked_assignment_reads = self.root.locked_assignment_reads
        tx.delete_for_org_calls = self.root.delete_for_org_calls
        tx.cache_enqueue_fail = self.root.cache_enqueue_fail
        self.tx = tx
        return tx

    async def __aexit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
        del exc, tb
        if exc_type is None:
            self.root.tx_committed += 1
            if self.tx is not None:
                self.root.assignments = dict(self.tx.assignments)
                self.root.upsert_calls.extend(self.tx.upsert_calls)
                self.root.cache_enqueues.extend(self.tx.cache_enqueues)
        else:
            self.root.tx_rolled_back += 1
        return False


def _headers(test_app) -> dict[str, str]:  # noqa: ANN001
    setattr(test_app.state.settings, "master_key", "mk-test")
    return {"Authorization": "Bearer mk-test"}


def _install_assignment_services(
    test_app,  # noqa: ANN001
    repository: _FakeTierAssignmentRepository,
    *,
    key_service: _RecordingKeyService | None = None,
    outbox_repository: _RecordingCacheInvalidationOutboxRepository | None = None,
    immediate_timeout_seconds: float = 0.5,
):  # noqa: ANN202
    audit = _RecordingAuditService()
    key_service = key_service or _RecordingKeyService()
    outbox_repository = outbox_repository or _RecordingCacheInvalidationOutboxRepository()
    repository.cache_enqueue_fail = outbox_repository.fail
    repository.cache_enqueues = outbox_repository.enqueues
    test_app.state.tier_repository = repository
    test_app.state.audit_service = audit
    test_app.state.key_service = key_service
    test_app.state.cache_invalidation_outbox_repository = outbox_repository
    test_app.state.cache_invalidation_service = CacheInvalidationService(
        key_service=key_service,
        repository=outbox_repository,
        immediate_timeout_seconds=immediate_timeout_seconds,
    )
    test_app.state.governance_invalidation_service = _RecordingGovernanceInvalidationService()
    return audit, key_service


def _set_auth_context(monkeypatch: pytest.MonkeyPatch, context: PlatformAuthContext | None) -> None:
    monkeypatch.setattr(
        "src.middleware.platform_auth.get_platform_auth_context",
        lambda request: context,
    )
    monkeypatch.setattr("src.middleware.admin.get_platform_auth_context", lambda request: context)


def _make_context(*, org_role: str | None = None) -> PlatformAuthContext:
    org_memberships = [{"organization_id": "org-1", "role": org_role}] if org_role else []
    return PlatformAuthContext(
        account_id="acct-1",
        email="user@example.com",
        role="platform_user",
        organization_memberships=org_memberships,
    )


def _audit_response_payloads(audit: _RecordingAuditService) -> list[dict[str, Any]]:
    return [
        payload.content_json
        for _, payloads in audit.sync_calls
        for payload in payloads
        if payload.kind == "response" and payload.content_json is not None
    ]


def _audit_events(audit: _RecordingAuditService) -> list[Any]:
    return [event for event, _ in audit.sync_calls]


def _assert_cache_invalidation_subset(
    payload: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    assert {key: payload.get(key) for key in expected} == expected


@pytest.mark.asyncio
async def test_org_tier_assignment_create_list_update_delete_audit_and_cache(
    client,
    test_app,
) -> None:
    repository = _FakeTierAssignmentRepository()
    audit, key_service = _install_assignment_services(test_app, repository)
    headers = _headers(test_app)

    create = await client.post(
        "/ui/api/organizations/org-1/tier-assignments",
        headers=headers,
        json={
            "tier_id": "tier-1",
            "tier_version_id": "version-1",
            "assignment_type": "primary",
            "weight": 3,
            "metadata": {"reason": "contract"},
        },
    )

    assert create.status_code == 200
    assignment = create.json()
    assignment_id = assignment["assignment_id"]
    assert assignment["organization_id"] == "org-1"
    assert assignment["tier_id"] == "tier-1"
    assert assignment["tier_version_id"] == "version-1"
    assert assignment["weight"] == 3

    listing = await client.get(
        "/ui/api/organizations/org-1/tier-assignments?enabled=true",
        headers=headers,
    )
    assert listing.status_code == 200
    assert [item["assignment_id"] for item in listing.json()["data"]] == [assignment_id]

    update = await client.patch(
        f"/ui/api/organizations/org-1/tier-assignments/{assignment_id}",
        headers=headers,
        json={
            "assignment_type": "addon",
            "enabled": False,
            "tier_version_id": None,
            "metadata": None,
        },
    )
    assert update.status_code == 200
    updated = update.json()
    assert updated["assignment_type"] == "addon"
    assert updated["enabled"] is False
    assert updated["tier_version_id"] is None
    assert updated["metadata"] is None

    delete = await client.delete(
        f"/ui/api/organizations/org-1/tier-assignments/{assignment_id}",
        headers=headers,
    )
    assert delete.status_code == 200
    assert delete.json() == {
        "deleted": True,
        "organization_id": "org-1",
        "assignment_id": assignment_id,
    }

    assert key_service.org_invalidation_attempts == ["org-1", "org-1", "org-1"]
    assert key_service.org_invalidations == ["org-1", "org-1", "org-1"]
    governance_invalidation = test_app.state.governance_invalidation_service
    assert governance_invalidation.local_targets == [
        ("tier_policy",),
        ("tier_policy",),
        ("tier_policy",),
    ]
    assert governance_invalidation.notified_targets == [
        ("tier_policy",),
        ("tier_policy",),
        ("tier_policy",),
    ]
    assert repository.locked_assignment_reads == [
        (assignment_id, "org-1"),
        (assignment_id, "org-1"),
    ]
    assert repository.delete_for_org_calls == [(assignment_id, "org-1")]
    assert test_app.state.cache_invalidation_outbox_repository.enqueues == [
        {
            "scope_type": "organization",
            "scope_id": "org-1",
            "reason": "organization_tier_assignment_create",
            "metadata": {"assignment_id": assignment_id},
            "max_attempts": 10,
        },
        {
            "scope_type": "organization",
            "scope_id": "org-1",
            "reason": "organization_tier_assignment_update",
            "metadata": {"assignment_id": assignment_id},
            "max_attempts": 10,
        },
        {
            "scope_type": "organization",
            "scope_id": "org-1",
            "reason": "organization_tier_assignment_delete",
            "metadata": {"assignment_id": assignment_id},
            "max_attempts": 10,
        },
    ]
    expected_cache_invalidation = {
        "attempted": True,
        "invalidated": True,
        "queued": True,
        "reason": "scheduled_for_worker",
        "immediate_attempted": True,
        "immediate_invalidated": True,
        "immediate_count": 1,
    }
    response_payloads = _audit_response_payloads(audit)
    _assert_cache_invalidation_subset(
        response_payloads[0]["cache_invalidation"],
        expected_cache_invalidation,
    )
    events = _audit_events(audit)
    assert [event.organization_id for event in events] == ["org-1", "org-1", "org-1"]
    assert all(
        {key: event.metadata["cache_invalidation"].get(key) for key in expected_cache_invalidation}
        == expected_cache_invalidation
        for event in events
    )
    assert all(
        event.metadata["tier_policy_invalidation"]
        == {
            "attempted": True,
            "reloaded": True,
            "notified": True,
            "reason": "reloaded_and_notified",
        }
        for event in events
    )
    assert "assignment_type" in events[1].metadata["changed_fields"]
    actions = [event.action for event in events]
    assert AuditAction.ADMIN_ORGANIZATION_TIER_ASSIGNMENT_CREATE.value in actions
    assert AuditAction.ADMIN_ORGANIZATION_TIER_ASSIGNMENT_UPDATE.value in actions
    assert AuditAction.ADMIN_ORGANIZATION_TIER_ASSIGNMENT_DELETE.value in actions


@pytest.mark.asyncio
async def test_org_tier_assignment_create_audits_non_fatal_tier_policy_reload_failure(
    client,
    test_app,
) -> None:
    repository = _FakeTierAssignmentRepository()
    audit, key_service = _install_assignment_services(test_app, repository)
    governance_invalidation = _RecordingGovernanceInvalidationService(fail_local=True)
    test_app.state.governance_invalidation_service = governance_invalidation

    response = await client.post(
        "/ui/api/organizations/org-1/tier-assignments",
        headers=_headers(test_app),
        json={"tier_id": "tier-1", "tier_version_id": "version-1"},
    )

    assert response.status_code == 200
    assert key_service.org_invalidations == ["org-1"]
    assert governance_invalidation.local_targets == [("tier_policy",)]
    assert governance_invalidation.notified_targets == [("tier_policy",)]
    event, _ = audit.sync_calls[-1]
    invalidation = event.metadata["tier_policy_invalidation"]
    assert invalidation["attempted"] is True
    assert invalidation["reloaded"] is False
    assert invalidation["notified"] is True
    assert invalidation["reason"] == "local_reload_failed_remote_notified"
    assert "tier policy reload unavailable" in invalidation["error"]
    response_payload = _audit_response_payloads(audit)[-1]
    assert response_payload["tier_policy_invalidation"] == invalidation


@pytest.mark.asyncio
@pytest.mark.parametrize("org_role", [OrganizationRole.OWNER, OrganizationRole.ADMIN])
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "/ui/api/organizations/org-1/tier-assignments", None),
        (
            "POST",
            "/ui/api/organizations/org-1/tier-assignments",
            {"tier_id": "tier-1", "tier_version_id": "version-1"},
        ),
        (
            "PATCH",
            "/ui/api/organizations/org-1/tier-assignments/assignment-1",
            {"enabled": False},
        ),
        (
            "DELETE",
            "/ui/api/organizations/org-1/tier-assignments/assignment-1",
            None,
        ),
    ],
)
async def test_org_tier_assignment_routes_require_platform_admin(
    client,
    test_app,
    monkeypatch,
    org_role,
    method,
    path,
    payload,
) -> None:
    _install_assignment_services(test_app, _FakeTierAssignmentRepository())
    _set_auth_context(monkeypatch, _make_context(org_role=org_role))

    request_kwargs = {"json": payload} if payload is not None else {}
    response = await client.request(method, path, **request_kwargs)

    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("org_role", [OrganizationRole.OWNER, OrganizationRole.ADMIN])
async def test_org_roles_cannot_assign_tier_through_organization_create(
    client,
    test_app,
    monkeypatch,
    org_role,
) -> None:
    _set_auth_context(monkeypatch, _make_context(org_role=org_role))

    response = await client.post(
        "/ui/api/organizations",
        json={
            "organization_name": "Unauthorized tier assignment",
            "primary_tier": {"tier_id": "tier-1"},
        },
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_org_admin_cannot_send_tier_fields_through_organization_settings(
    client,
    test_app,
    monkeypatch,
) -> None:
    test_app.state.prisma_manager = SimpleNamespace(client=object())
    _set_auth_context(monkeypatch, _make_context(org_role=OrganizationRole.ADMIN))

    response = await client.put(
        "/ui/api/organizations/org-1",
        json={"primary_tier": {"tier_id": "tier-2"}},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == (
        "Only platform admins can manage organization tier assignments"
    )


@pytest.mark.asyncio
async def test_platform_admin_must_use_tier_assignment_endpoint(
    client,
    test_app,
) -> None:
    test_app.state.prisma_manager = SimpleNamespace(client=object())

    response = await client.put(
        "/ui/api/organizations/org-1",
        headers=_headers(test_app),
        json={"primary_tier": {"tier_id": "tier-2"}},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Tier assignments cannot be changed through organization settings; "
        "use the organization tier-assignment endpoints"
    )


@pytest.mark.asyncio
async def test_org_admin_cannot_change_model_specific_organization_policy(
    client,
    test_app,
    monkeypatch,
) -> None:
    test_app.state.prisma_manager = SimpleNamespace(client=object())
    _set_auth_context(monkeypatch, _make_context(org_role=OrganizationRole.ADMIN))

    response = await client.put(
        "/ui/api/organizations/org-1",
        json={"model_rpm_limit": None, "model_tpm_limit": None},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == (
        "Only platform admins can update model-specific organization policy"
    )


@pytest.mark.asyncio
async def test_org_tier_assignment_primary_conflict_returns_409(client, test_app) -> None:
    repository = _FakeTierAssignmentRepository()
    repository.upsert_error = "organization can only have one active primary tier assignment"
    audit, key_service = _install_assignment_services(test_app, repository)

    response = await client.post(
        "/ui/api/organizations/org-1/tier-assignments",
        headers=_headers(test_app),
        json={"tier_id": "tier-2", "assignment_type": "primary"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "organization can only have one active primary tier assignment"
    )
    assert key_service.org_invalidation_attempts == []
    assert key_service.org_invalidations == []
    assert test_app.state.cache_invalidation_outbox_repository.enqueues == []
    assert audit.sync_calls == []


@pytest.mark.asyncio
async def test_org_tier_assignment_disabled_tier_conflict_returns_409(client, test_app) -> None:
    repository = _FakeTierAssignmentRepository()
    repository.upsert_error = "enabled tier assignments require an enabled tier"
    audit, key_service = _install_assignment_services(test_app, repository)

    response = await client.post(
        "/ui/api/organizations/org-1/tier-assignments",
        headers=_headers(test_app),
        json={"tier_id": "tier-disabled", "assignment_type": "addon"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == ("enabled tier assignments require an enabled tier")
    assert key_service.org_invalidation_attempts == []
    assert test_app.state.cache_invalidation_outbox_repository.enqueues == []
    assert audit.sync_calls == []


@pytest.mark.asyncio
async def test_org_tier_assignment_disabled_tier_database_race_returns_409(
    client,
    test_app,
) -> None:
    repository = _FakeTierAssignmentRepository()
    repository.upsert_exception = RuntimeError("enabled tier assignments require an enabled tier")
    audit, key_service = _install_assignment_services(test_app, repository)

    response = await client.post(
        "/ui/api/organizations/org-1/tier-assignments",
        headers=_headers(test_app),
        json={"tier_id": "tier-disabled", "assignment_type": "addon"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == ("enabled tier assignments require an enabled tier")
    assert key_service.org_invalidation_attempts == []
    assert test_app.state.cache_invalidation_outbox_repository.enqueues == []
    assert audit.sync_calls == []


@pytest.mark.asyncio
async def test_org_tier_assignment_create_defaults_assignment_type_when_omitted(
    client,
    test_app,
) -> None:
    repository = _FakeTierAssignmentRepository()
    _install_assignment_services(test_app, repository)

    response = await client.post(
        "/ui/api/organizations/org-1/tier-assignments",
        headers=_headers(test_app),
        json={"tier_id": "tier-1", "enabled": False},
    )

    assert response.status_code == 200
    assert response.json()["assignment_type"] == "primary"
    assert repository.upsert_calls[0]["assignment_type"] == "primary"


@pytest.mark.asyncio
@pytest.mark.parametrize("assignment_type", ["", "   "])
async def test_org_tier_assignment_create_rejects_blank_assignment_type(
    client,
    test_app,
    assignment_type: str,
) -> None:
    repository = _FakeTierAssignmentRepository()
    audit, key_service = _install_assignment_services(test_app, repository)

    response = await client.post(
        "/ui/api/organizations/org-1/tier-assignments",
        headers=_headers(test_app),
        json={"tier_id": "tier-1", "assignment_type": assignment_type},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "assignment_type is required"
    assert repository.upsert_calls == []
    assert key_service.org_invalidation_attempts == []
    assert key_service.org_invalidations == []
    assert audit.sync_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field_name", ["tier_id", "assignment_type", "enabled", "weight"])
async def test_org_tier_assignment_patch_rejects_null_for_non_nullable_fields(
    client,
    test_app,
    field_name: str,
) -> None:
    repository = _FakeTierAssignmentRepository()
    repository.seed_assignment(assignment_id="assignment-1")
    _install_assignment_services(test_app, repository)

    response = await client.patch(
        "/ui/api/organizations/org-1/tier-assignments/assignment-1",
        headers=_headers(test_app),
        json={field_name: None},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == f"{field_name} cannot be null"
    assert repository.upsert_calls == []


@pytest.mark.asyncio
async def test_org_tier_assignment_patch_preserves_assignment_type_when_omitted(
    client,
    test_app,
) -> None:
    repository = _FakeTierAssignmentRepository()
    repository.seed_assignment(assignment_id="assignment-1", assignment_type="addon")
    _install_assignment_services(test_app, repository)

    response = await client.patch(
        "/ui/api/organizations/org-1/tier-assignments/assignment-1",
        headers=_headers(test_app),
        json={"enabled": False},
    )

    assert response.status_code == 200
    assert response.json()["assignment_type"] == "addon"
    assert repository.upsert_calls[0]["assignment_type"] == "addon"
    assert repository.locked_assignment_reads == [("assignment-1", "org-1")]


@pytest.mark.asyncio
@pytest.mark.parametrize("assignment_type", ["", "   "])
async def test_org_tier_assignment_patch_rejects_blank_assignment_type(
    client,
    test_app,
    assignment_type: str,
) -> None:
    repository = _FakeTierAssignmentRepository()
    repository.seed_assignment(assignment_id="assignment-1")
    audit, key_service = _install_assignment_services(test_app, repository)

    response = await client.patch(
        "/ui/api/organizations/org-1/tier-assignments/assignment-1",
        headers=_headers(test_app),
        json={"assignment_type": assignment_type},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "assignment_type is required"
    assert repository.upsert_calls == []
    assert key_service.org_invalidation_attempts == []
    assert key_service.org_invalidations == []
    assert audit.sync_calls == []


@pytest.mark.asyncio
async def test_org_tier_assignment_disabled_payload_does_not_require_active_version(
    client,
    test_app,
) -> None:
    repository = _FakeTierAssignmentRepository()
    repository.require_active_version_for_enabled = True
    _install_assignment_services(test_app, repository)

    response = await client.post(
        "/ui/api/organizations/org-1/tier-assignments",
        headers=_headers(test_app),
        json={
            "tier_id": "tier-1",
            "tier_version_id": "draft-version",
            "assignment_type": "addon",
            "enabled": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert repository.upsert_calls[0]["enabled"] is False
    assert repository.upsert_calls[0]["tier_version_id"] == "draft-version"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("repository_error", "expected_status", "expected_detail"),
    [
        ("tier_id must reference an existing tier", 404, "tier_id must reference an existing tier"),
        (
            "tier_version_id must reference an existing tier version",
            404,
            "tier_version_id must reference an existing tier version",
        ),
        ("tier_version_id must belong to tier_id", 400, "tier_version_id must belong to tier_id"),
    ],
)
async def test_org_tier_assignment_reference_errors_are_mapped(
    client,
    test_app,
    repository_error: str,
    expected_status: int,
    expected_detail: str,
) -> None:
    repository = _FakeTierAssignmentRepository()
    repository.upsert_error = repository_error
    _install_assignment_services(test_app, repository)

    response = await client.post(
        "/ui/api/organizations/org-1/tier-assignments",
        headers=_headers(test_app),
        json={
            "tier_id": "tier-1",
            "tier_version_id": "version-1",
            "assignment_type": "addon",
            "enabled": False,
        },
    )

    assert response.status_code == expected_status
    assert response.json()["detail"] == expected_detail


@pytest.mark.asyncio
async def test_org_tier_assignment_invalid_effective_window_returns_400(
    client,
    test_app,
) -> None:
    _install_assignment_services(test_app, _FakeTierAssignmentRepository())

    response = await client.post(
        "/ui/api/organizations/org-1/tier-assignments",
        headers=_headers(test_app),
        json={
            "tier_id": "tier-1",
            "starts_at": "2030-02-01T00:00:00Z",
            "ends_at": "2030-01-01T00:00:00Z",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "starts_at must be before ends_at"


@pytest.mark.asyncio
async def test_org_tier_assignment_cross_org_assignment_returns_404(client, test_app) -> None:
    repository = _FakeTierAssignmentRepository()
    repository.organizations.add("org-2")
    repository.seed_assignment(assignment_id="assignment-2", organization_id="org-2")
    audit, key_service = _install_assignment_services(test_app, repository)

    response = await client.patch(
        "/ui/api/organizations/org-1/tier-assignments/assignment-2",
        headers=_headers(test_app),
        json={"enabled": False},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Tier assignment not found"
    assert repository.locked_assignment_reads == [("assignment-2", "org-1")]
    assert key_service.org_invalidation_attempts == []
    assert key_service.org_invalidations == []
    assert audit.sync_calls == []


@pytest.mark.asyncio
async def test_org_tier_assignment_cross_org_delete_returns_404(client, test_app) -> None:
    repository = _FakeTierAssignmentRepository()
    repository.organizations.add("org-2")
    repository.seed_assignment(assignment_id="assignment-2", organization_id="org-2")
    audit, key_service = _install_assignment_services(test_app, repository)

    response = await client.delete(
        "/ui/api/organizations/org-1/tier-assignments/assignment-2",
        headers=_headers(test_app),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Tier assignment not found"
    assert repository.locked_assignment_reads == [("assignment-2", "org-1")]
    assert repository.delete_for_org_calls == []
    assert "assignment-2" in repository.assignments
    assert key_service.org_invalidation_attempts == []
    assert key_service.org_invalidations == []
    assert audit.sync_calls == []


@pytest.mark.asyncio
async def test_org_tier_assignment_missing_organization_returns_404(client, test_app) -> None:
    _install_assignment_services(test_app, _FakeTierAssignmentRepository())

    response = await client.get(
        "/ui/api/organizations/org-missing/tier-assignments",
        headers=_headers(test_app),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Organization not found"


@pytest.mark.asyncio
async def test_org_tier_assignment_records_cache_invalidation_failure_in_audit(
    client,
    test_app,
) -> None:
    repository = _FakeTierAssignmentRepository()
    audit, key_service = _install_assignment_services(
        test_app,
        repository,
        key_service=_RecordingKeyService(fail=True),
    )

    response = await client.post(
        "/ui/api/organizations/org-1/tier-assignments",
        headers=_headers(test_app),
        json={"tier_id": "tier-1", "assignment_type": "addon", "enabled": False},
    )

    assert response.status_code == 200
    assert key_service.org_invalidation_attempts == ["org-1"]
    assert key_service.org_invalidations == []
    assert test_app.state.cache_invalidation_outbox_repository.enqueues == [
        {
            "scope_type": "organization",
            "scope_id": "org-1",
            "reason": "organization_tier_assignment_create",
            "metadata": {"assignment_id": "assignment-1"},
            "max_attempts": 10,
        }
    ]
    expected_cache_invalidation = {
        "attempted": True,
        "invalidated": False,
        "queued": True,
        "reason": "scheduled_for_worker",
        "error_type": "RuntimeError",
        "immediate_attempted": True,
        "immediate_invalidated": False,
        "immediate_reason": "immediate_invalidation_failed",
        "immediate_error_type": "RuntimeError",
    }
    response_payloads = _audit_response_payloads(audit)
    _assert_cache_invalidation_subset(
        response_payloads[0]["cache_invalidation"],
        expected_cache_invalidation,
    )
    event = _audit_events(audit)[0]
    assert event.organization_id == "org-1"
    _assert_cache_invalidation_subset(
        event.metadata["cache_invalidation"],
        expected_cache_invalidation,
    )


@pytest.mark.asyncio
async def test_org_tier_assignment_records_cache_invalidation_timeout_in_audit(
    client,
    test_app,
) -> None:
    repository = _FakeTierAssignmentRepository()
    audit, key_service = _install_assignment_services(
        test_app,
        repository,
        key_service=_RecordingKeyService(delay_seconds=0.05),
        immediate_timeout_seconds=0.001,
    )

    response = await client.post(
        "/ui/api/organizations/org-1/tier-assignments",
        headers=_headers(test_app),
        json={"tier_id": "tier-1", "assignment_type": "addon", "enabled": False},
    )

    assert response.status_code == 200
    assert key_service.org_invalidation_attempts == ["org-1"]
    assert key_service.org_invalidations == []
    assert test_app.state.cache_invalidation_outbox_repository.enqueues == [
        {
            "scope_type": "organization",
            "scope_id": "org-1",
            "reason": "organization_tier_assignment_create",
            "metadata": {"assignment_id": "assignment-1"},
            "max_attempts": 10,
        }
    ]
    expected_cache_invalidation = {
        "attempted": True,
        "invalidated": False,
        "queued": True,
        "reason": "scheduled_for_worker",
        "error_type": "TimeoutError",
        "immediate_attempted": True,
        "immediate_invalidated": False,
        "immediate_reason": "immediate_invalidation_timeout",
        "immediate_error_type": "TimeoutError",
    }
    response_payloads = _audit_response_payloads(audit)
    _assert_cache_invalidation_subset(
        response_payloads[0]["cache_invalidation"],
        expected_cache_invalidation,
    )
    event = _audit_events(audit)[0]
    assert event.organization_id == "org-1"
    _assert_cache_invalidation_subset(
        event.metadata["cache_invalidation"],
        expected_cache_invalidation,
    )


@pytest.mark.asyncio
async def test_org_tier_assignment_returns_503_when_cache_invalidation_cannot_be_scheduled(
    client,
    test_app,
) -> None:
    repository = _FakeTierAssignmentRepository()
    audit, key_service = _install_assignment_services(
        test_app,
        repository,
        key_service=_RecordingKeyService(fail=True),
        outbox_repository=_RecordingCacheInvalidationOutboxRepository(fail=True),
    )

    response = await client.post(
        "/ui/api/organizations/org-1/tier-assignments",
        headers=_headers(test_app),
        json={"tier_id": "tier-1", "assignment_type": "addon", "enabled": False},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Cache invalidation could not be scheduled"
    assert key_service.org_invalidation_attempts == []
    assert key_service.org_invalidations == []
    assert repository.assignments == {}
    assert repository.tx_started == 1
    assert repository.tx_committed == 0
    assert repository.tx_rolled_back == 1
    assert audit.sync_calls == []
