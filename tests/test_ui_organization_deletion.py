from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from src.db.organization_deletion_records import (
    OrganizationDeletionCounts,
    OrganizationDeletionJobRecord,
    OrganizationDeletionPlanRecord,
)
from src.services.organization_deletion_types import (
    OrganizationDeletionConflictError,
    OrganizationDeletionMutationResult,
    OrganizationDeletionPlan,
)


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


def _headers(test_app) -> dict[str, str]:  # noqa: ANN001
    setattr(test_app.state.settings, "master_key", "mk-test")
    return {"Authorization": "Bearer mk-test"}


@pytest.mark.asyncio
async def test_deletion_plan_is_platform_admin_only_and_returns_impact(client, test_app):
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
