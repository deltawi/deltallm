from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from src.auth.roles import OrganizationRole, PlatformRole
from src.db.organization_deletion_records import (
    OrganizationDeletionCounts,
    OrganizationDeletionJobRecord,
    OrganizationDeletionPlanRecord,
)
from src.services.organization_deletion_types import (
    OrganizationDeletionConflictError,
    OrganizationDeletionExpediteResult,
    OrganizationDeletionMutationResult,
    OrganizationDeletionPlan,
)
from src.models.platform_auth import PlatformAuthContext


class _FakeOrganizationDeletionService:
    def __init__(self) -> None:
        now = datetime.now(tz=UTC)
        self.plan = OrganizationDeletionPlan(
            record=OrganizationDeletionPlanRecord(
                organization_id="org-1",
                organization_name="Example Org",
                lifecycle_state="active",
                lifecycle_version=0,
                deletion_requested_at=None,
                deletion_not_before_at=None,
                deletion_job_id=None,
                counts=OrganizationDeletionCounts(teams=2, api_keys=3),
            ),
            plan_token="a" * 64,
            recovery_window_hours=24,
            requests_enabled=True,
        )
        self.job = OrganizationDeletionJobRecord(
            deletion_job_id="delete-1",
            organization_id="org-1",
            status="pending",
            phase="cancel_pending",
            requested_by_account_id=None,
            idempotency_key="request-1",
            request_hash="request-hash",
            plan_token="a" * 64,
            not_before_at=now + timedelta(hours=24),
            created_at=now,
            updated_at=now,
        )
        self.request_calls: list[dict[str, Any]] = []
        self.expedite_calls: list[dict[str, Any]] = []
        self.retry_calls: list[dict[str, Any]] = []
        self.preview_error: Exception | None = None

    async def preview(self, organization_id: str) -> OrganizationDeletionPlan:
        assert organization_id == "org-1"
        if self.preview_error is not None:
            raise self.preview_error
        return self.plan

    async def request_deletion(self, **kwargs: Any) -> OrganizationDeletionMutationResult:
        self.request_calls.append(kwargs)
        return OrganizationDeletionMutationResult(
            job=self.job,
            immediate_invalidation_succeeded=True,
        )

    async def expedite(self, **kwargs: Any) -> OrganizationDeletionExpediteResult:
        self.expedite_calls.append(kwargs)
        self.job = OrganizationDeletionJobRecord(
            **{
                **self.job.__dict__,
                "not_before_at": datetime.now(tz=UTC),
                "expedited_at": datetime.now(tz=UTC),
                "updated_at": datetime.now(tz=UTC),
            }
        )
        return OrganizationDeletionExpediteResult(
            job=self.job,
            idempotency_resolution="applied",
        )

    async def get_job(self, **kwargs: Any) -> OrganizationDeletionJobRecord:
        assert kwargs == {"organization_id": "org-1", "deletion_job_id": "delete-1"}
        return self.job

    async def retry_failed(self, **kwargs: Any) -> OrganizationDeletionMutationResult:
        self.retry_calls.append(kwargs)
        return OrganizationDeletionMutationResult(
            job=self.job,
            immediate_invalidation_succeeded=True,
        )


def _headers(test_app) -> dict[str, str]:  # noqa: ANN001
    setattr(test_app.state.settings, "master_key", "mk-test")
    return {"Authorization": "Bearer mk-test"}


def _set_auth_context(monkeypatch: pytest.MonkeyPatch, context: PlatformAuthContext) -> None:
    monkeypatch.setattr(
        "src.middleware.admin.get_platform_auth_context",
        lambda request: context,
    )
    monkeypatch.setattr(
        "src.api.admin.endpoints.organization_deletion.get_platform_auth_context",
        lambda request: context,
    )


@pytest.mark.asyncio
async def test_deletion_plan_requires_authentication_and_returns_impact(client, test_app):
    service = _FakeOrganizationDeletionService()
    test_app.state.organization_deletion_service = service

    unauthorized = await client.get("/ui/api/organizations/org-1/deletion-plan")
    response = await client.get(
        "/ui/api/organizations/org-1/deletion-plan",
        headers=_headers(test_app),
    )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.json()["counts"]["teams"] == 2
    assert response.json()["blocking_dependencies"] == []
    assert response.json()["requests_enabled"] is True
    assert response.json()["can_request"] is True
    assert response.json()["lifecycle_protocol_version"] == 2
    assert response.json()["retained_history"] == [
        "spend_events",
        "audit_events",
        "terminal_batch_records",
        "batch_files_until_expiry",
    ]


@pytest.mark.asyncio
async def test_deletion_request_requires_ack_and_idempotency_header(client, test_app):
    service = _FakeOrganizationDeletionService()
    test_app.state.organization_deletion_service = service
    payload = {
        "confirmation_name": "Example Org",
        "plan_token": "a" * 64,
        "acknowledge_running_work_cancellation": True,
    }
    headers = _headers(test_app)

    missing_header = await client.post(
        "/ui/api/organizations/org-1/deletion-requests",
        headers=headers,
        json=payload,
    )
    unacknowledged = await client.post(
        "/ui/api/organizations/org-1/deletion-requests",
        headers={**headers, "Idempotency-Key": "request-1"},
        json={**payload, "acknowledge_running_work_cancellation": False},
    )

    assert missing_header.status_code == 422
    assert unacknowledged.status_code == 400
    assert service.request_calls == []


@pytest.mark.asyncio
async def test_deletion_request_passes_confirmation_and_idempotency(client, test_app):
    service = _FakeOrganizationDeletionService()
    test_app.state.organization_deletion_service = service

    response = await client.post(
        "/ui/api/organizations/org-1/deletion-requests",
        headers={**_headers(test_app), "Idempotency-Key": "request-1"},
        json={
            "confirmation_name": "Example Org",
            "plan_token": "a" * 64,
            "acknowledge_running_work_cancellation": True,
        },
    )

    assert response.status_code == 202
    assert response.json()["immediate_invalidation_succeeded"] is True
    assert service.request_calls[0]["idempotency_key"] == "request-1"
    assert service.request_calls[0]["confirmation_name"] == "Example Org"


@pytest.mark.asyncio
async def test_deletion_conflict_uses_structured_error(client, test_app):
    service = _FakeOrganizationDeletionService()
    service.preview_error = OrganizationDeletionConflictError(
        "deletion already underway",
        code="organization_deletion_in_progress",
    )
    test_app.state.organization_deletion_service = service

    response = await client.get(
        "/ui/api/organizations/org-1/deletion-plan",
        headers=_headers(test_app),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "organization_deletion_in_progress",
        "message": "deletion already underway",
    }


@pytest.mark.asyncio
async def test_expedite_requires_acknowledgement_and_passes_confirmation(client, test_app):
    service = _FakeOrganizationDeletionService()
    test_app.state.organization_deletion_service = service
    path = "/ui/api/organizations/org-1/deletion-requests/delete-1/expedite"
    headers = {**_headers(test_app), "Idempotency-Key": "expedite-1"}

    missing_header = await client.post(
        path,
        headers=_headers(test_app),
        json={
            "confirmation_name": "Example Org",
            "acknowledge_immediate_irreversible_deletion": True,
        },
    )

    unacknowledged = await client.post(
        path,
        headers=headers,
        json={
            "confirmation_name": "Example Org",
            "acknowledge_immediate_irreversible_deletion": False,
        },
    )
    response = await client.post(
        path,
        headers=headers,
        json={
            "confirmation_name": "Example Org",
            "acknowledge_immediate_irreversible_deletion": True,
        },
    )

    assert missing_header.status_code == 422
    assert unacknowledged.status_code == 400
    assert response.status_code == 202
    assert response.json()["restore_allowed"] is False
    assert response.json()["recovery_window_waived"] is True
    assert response.json()["idempotency_resolution"] == "applied"
    assert service.expedite_calls == [
        {
            "organization_id": "org-1",
            "deletion_job_id": "delete-1",
            "confirmation_name": "Example Org",
            "idempotency_key": "expedite-1",
            "expedited_by_account_id": None,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [OrganizationRole.OWNER, OrganizationRole.ADMIN])
async def test_expedite_allows_scoped_organization_owner_and_admin(
    client,
    test_app,
    monkeypatch,
    role,
) -> None:
    service = _FakeOrganizationDeletionService()
    test_app.state.organization_deletion_service = service
    _set_auth_context(
        monkeypatch,
        PlatformAuthContext(
            account_id="account-org-admin",
            email="org-admin@example.com",
            role=PlatformRole.ORG_USER,
            organization_memberships=[{"organization_id": "org-1", "role": role}],
            team_memberships=[],
        ),
    )

    response = await client.post(
        "/ui/api/organizations/org-1/deletion-requests/delete-1/expedite",
        headers={"Idempotency-Key": "expedite-org-admin"},
        json={
            "confirmation_name": "Example Org",
            "acknowledge_immediate_irreversible_deletion": True,
        },
    )

    assert response.status_code == 202
    assert service.expedite_calls[0]["expedited_by_account_id"] == "account-org-admin"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("membership_organization_id", "role"),
    [
        ("org-2", OrganizationRole.ADMIN),
        ("org-1", OrganizationRole.MEMBER),
        ("org-1", OrganizationRole.BILLING),
        ("org-1", OrganizationRole.AUDITOR),
    ],
)
async def test_expedite_denies_cross_tenant_and_lower_organization_roles(
    client,
    test_app,
    monkeypatch,
    membership_organization_id,
    role,
) -> None:
    service = _FakeOrganizationDeletionService()
    test_app.state.organization_deletion_service = service
    _set_auth_context(
        monkeypatch,
        PlatformAuthContext(
            account_id="account-denied",
            email="denied@example.com",
            role=PlatformRole.ORG_USER,
            organization_memberships=[
                {"organization_id": membership_organization_id, "role": role}
            ],
            team_memberships=[],
        ),
    )

    response = await client.post(
        "/ui/api/organizations/org-1/deletion-requests/delete-1/expedite",
        headers={"Idempotency-Key": "expedite-denied"},
        json={
            "confirmation_name": "Example Org",
            "acknowledge_immediate_irreversible_deletion": True,
        },
    )

    assert response.status_code == 403
    assert service.expedite_calls == []


@pytest.mark.asyncio
async def test_scoped_organization_admin_can_inspect_and_retry_but_not_start_or_restore(
    client,
    test_app,
    monkeypatch,
) -> None:
    service = _FakeOrganizationDeletionService()
    service.job = OrganizationDeletionJobRecord(**{**service.job.__dict__, "status": "failed"})
    test_app.state.organization_deletion_service = service
    _set_auth_context(
        monkeypatch,
        PlatformAuthContext(
            account_id="account-org-admin",
            email="org-admin@example.com",
            role=PlatformRole.ORG_USER,
            organization_memberships=[{"organization_id": "org-1", "role": OrganizationRole.ADMIN}],
            team_memberships=[],
        ),
    )

    plan = await client.get("/ui/api/organizations/org-1/deletion-plan")
    job = await client.get("/ui/api/organizations/org-1/deletion-requests/delete-1")
    retry = await client.post("/ui/api/organizations/org-1/deletion-requests/delete-1/retry")
    request = await client.post(
        "/ui/api/organizations/org-1/deletion-requests",
        headers={"Idempotency-Key": "request-denied"},
        json={
            "confirmation_name": "Example Org",
            "plan_token": "a" * 64,
            "acknowledge_running_work_cancellation": True,
        },
    )
    restore = await client.post("/ui/api/organizations/org-1/deletion-requests/delete-1/restore")

    assert plan.status_code == 200
    assert plan.json()["can_request"] is False
    assert job.status_code == 200
    assert retry.status_code == 202
    assert service.retry_calls[0]["retried_by_account_id"] == "account-org-admin"
    assert request.status_code == 403
    assert restore.status_code == 403


@pytest.mark.asyncio
async def test_expedite_allows_platform_admin_session(client, test_app, monkeypatch) -> None:
    service = _FakeOrganizationDeletionService()
    test_app.state.organization_deletion_service = service
    _set_auth_context(
        monkeypatch,
        PlatformAuthContext(
            account_id="account-platform-admin",
            email="platform-admin@example.com",
            role=PlatformRole.ADMIN,
            organization_memberships=[],
            team_memberships=[],
        ),
    )

    plan = await client.get("/ui/api/organizations/org-1/deletion-plan")
    response = await client.post(
        "/ui/api/organizations/org-1/deletion-requests/delete-1/expedite",
        headers={"Idempotency-Key": "expedite-admin"},
        json={
            "confirmation_name": "Example Org",
            "acknowledge_immediate_irreversible_deletion": True,
        },
    )

    assert plan.status_code == 200
    assert plan.json()["can_request"] is True
    assert response.status_code == 202
    assert service.expedite_calls[0]["expedited_by_account_id"] == "account-platform-admin"
