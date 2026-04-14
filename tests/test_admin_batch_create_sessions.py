from __future__ import annotations

from datetime import UTC, datetime
import pytest

from src.api.admin.endpoints.common import AuthScope
from src.audit.actions import AuditAction
from src.auth.roles import Permission
from src.batch.create.admin_service import BatchCreateSessionExpireResult, BatchCreateSessionRetryResult
from src.batch.create.models import BatchCreateSessionRecord, BatchCreateSessionStatus
from src.batch.create.promoter import BatchCreatePromotionResult


def _session(*, status: str = BatchCreateSessionStatus.FAILED_RETRYABLE) -> BatchCreateSessionRecord:
    now = datetime.now(tz=UTC)
    return BatchCreateSessionRecord(
        session_id="session-1",
        target_batch_id="batch-1",
        status=status,
        endpoint="/v1/embeddings",
        input_file_id="file-1",
        staged_storage_backend="local",
        staged_storage_key="batch-create-stage/2026/04/14/session-1.jsonl",
        staged_checksum="abc",
        staged_bytes=128,
        expected_item_count=3,
        inferred_model="text-embedding-3-small",
        metadata=None,
        requested_service_tier=None,
        effective_service_tier=None,
        service_tier_source=None,
        scheduling_scope_key="team:team-1",
        priority_quota_scope_key="team:team-1",
        idempotency_scope_key=None,
        idempotency_key=None,
        last_error_code="pending_limit_exceeded" if status != BatchCreateSessionStatus.COMPLETED else None,
        last_error_message="cap reached" if status != BatchCreateSessionStatus.COMPLETED else None,
        promotion_attempt_count=2,
        created_by_api_key="sk-session-test-key-1234",
        created_by_user_id="user-1",
        created_by_team_id="team-1",
        created_by_organization_id="org-1",
        created_at=now,
        completed_at=now if status == BatchCreateSessionStatus.COMPLETED else None,
        last_attempt_at=now,
        expires_at=None,
    )


class _FakeSessionAdminDB:
    def __init__(self, session: BatchCreateSessionRecord | None = None) -> None:
        self.session = session or _session()

    def _row(self) -> dict[str, object]:
        session = self.session
        assert session is not None
        return {
            "session_id": session.session_id,
            "target_batch_id": session.target_batch_id,
            "status": session.status,
            "endpoint": session.endpoint,
            "input_file_id": session.input_file_id,
            "expected_item_count": session.expected_item_count,
            "inferred_model": session.inferred_model,
            "requested_service_tier": session.requested_service_tier,
            "effective_service_tier": session.effective_service_tier,
            "created_by_api_key": session.created_by_api_key,
            "created_by_user_id": session.created_by_user_id,
            "created_by_team_id": session.created_by_team_id,
            "created_by_organization_id": session.created_by_organization_id,
            "last_error_code": session.last_error_code,
            "last_error_message": session.last_error_message,
            "promotion_attempt_count": session.promotion_attempt_count,
            "created_at": session.created_at,
            "completed_at": session.completed_at,
            "expires_at": session.expires_at,
            "team_alias": "Team One",
            "organization_id": "org-1",
        }

    async def query_raw(self, query: str, *params):
        if "COUNT(*) AS total" in query and "FROM deltallm_batch_create_session s" in query:
            return [{"total": 1 if self.session is not None else 0}]
        if "FROM deltallm_batch_create_session s" in query and "LEFT JOIN deltallm_teamtable t" in query:
            if self.session is None:
                return []
            if "WHERE s.session_id = $1" in query:
                return [self._row()]
            return [self._row()]
        if "SELECT organization_id FROM deltallm_teamtable" in query:
            return [{"organization_id": "org-1"}]
        return []


class _AdminServiceStub:
    def __init__(self) -> None:
        self.retry_calls: list[str] = []
        self.expire_calls: list[str] = []
        self.retry_result = BatchCreateSessionRetryResult(
            session=_session(status=BatchCreateSessionStatus.COMPLETED),
            promotion=BatchCreatePromotionResult(
                session_id="session-1",
                batch_id="batch-1",
                promoted=True,
                job=None,
            ),
        )
        self.expire_result = BatchCreateSessionExpireResult(
            session=_session(status=BatchCreateSessionStatus.EXPIRED),
            artifact_deleted=False,
        )

    async def retry_session(self, session_id: str) -> BatchCreateSessionRetryResult:
        self.retry_calls.append(session_id)
        return self.retry_result

    async def expire_session(self, session_id: str) -> BatchCreateSessionExpireResult:
        self.expire_calls.append(session_id)
        return self.expire_result


@pytest.mark.asyncio
async def test_list_batch_create_sessions_endpoint_returns_capabilities(client, test_app, monkeypatch):
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeSessionAdminDB()})()
    test_app.state.batch_create_session_admin_service = _AdminServiceStub()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.batch_create_sessions.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(  # noqa: ARG005
            is_platform_admin=False,
            team_ids=["team-1"],
            team_permissions_by_id={"team-1": {Permission.KEY_READ, Permission.KEY_UPDATE}},
        ),
    )

    response = await client.get("/ui/api/batch-create-sessions", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["pagination"]["total"] == 1
    assert payload["data"][0]["session_id"] == "session-1"
    assert payload["data"][0]["capabilities"] == {"view": True, "retry": True, "expire": True}


@pytest.mark.asyncio
async def test_list_batch_create_sessions_endpoint_hides_unavailable_admin_actions(client, test_app, monkeypatch):
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeSessionAdminDB()})()
    test_app.state.batch_create_session_admin_service = None
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.batch_create_sessions.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(  # noqa: ARG005
            is_platform_admin=False,
            team_ids=["team-1"],
            team_permissions_by_id={"team-1": {Permission.KEY_READ, Permission.KEY_UPDATE}},
        ),
    )

    response = await client.get("/ui/api/batch-create-sessions", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"][0]["capabilities"] == {"view": True, "retry": False, "expire": False}


@pytest.mark.asyncio
async def test_get_batch_create_session_endpoint_enforces_scope(client, test_app, monkeypatch):
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeSessionAdminDB()})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.batch_create_sessions.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(  # noqa: ARG005
            is_platform_admin=False,
            team_ids=["team-2"],
            team_permissions_by_id={"team-2": {Permission.KEY_READ}},
        ),
    )

    response = await client.get("/ui/api/batch-create-sessions/session-1", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_retry_batch_create_session_endpoint_calls_service_and_audits(client, test_app, monkeypatch):
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeSessionAdminDB()})()
    test_app.state.batch_create_session_admin_service = _AdminServiceStub()
    setattr(test_app.state.settings, "master_key", "mk-test")
    recorded: list[dict[str, object]] = []

    monkeypatch.setattr(
        "src.api.admin.endpoints.batch_create_sessions.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(  # noqa: ARG005
            is_platform_admin=True,
            account_id="acct-1",
        ),
    )

    async def _capture_audit(**kwargs):  # noqa: ANN003
        recorded.append(kwargs)

    monkeypatch.setattr("src.api.admin.endpoints.batch_create_sessions.emit_admin_mutation_audit", _capture_audit)

    response = await client.post("/ui/api/batch-create-sessions/session-1/retry", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    assert response.json() == {
        "session_id": "session-1",
        "target_batch_id": "batch-1",
        "status": "completed",
        "retried": True,
        "promotion_result": "promoted",
    }
    assert test_app.state.batch_create_session_admin_service.retry_calls == ["session-1"]
    assert recorded[0]["action"] == AuditAction.ADMIN_BATCH_CREATE_SESSION_RETRY


@pytest.mark.asyncio
async def test_retry_batch_create_session_endpoint_returns_503_when_admin_service_missing(client, test_app, monkeypatch):
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeSessionAdminDB()})()
    test_app.state.batch_create_session_admin_service = None
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.batch_create_sessions.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(  # noqa: ARG005
            is_platform_admin=True,
            account_id="acct-1",
        ),
    )

    response = await client.post("/ui/api/batch-create-sessions/session-1/retry", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 503
    assert response.json()["detail"] == "Batch create-session admin service unavailable"


@pytest.mark.asyncio
async def test_expire_batch_create_session_endpoint_calls_service_and_audits(client, test_app, monkeypatch):
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeSessionAdminDB()})()
    test_app.state.batch_create_session_admin_service = _AdminServiceStub()
    setattr(test_app.state.settings, "master_key", "mk-test")
    recorded: list[dict[str, object]] = []

    monkeypatch.setattr(
        "src.api.admin.endpoints.batch_create_sessions.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(  # noqa: ARG005
            is_platform_admin=True,
            account_id="acct-1",
        ),
    )

    async def _capture_audit(**kwargs):  # noqa: ANN003
        recorded.append(kwargs)

    monkeypatch.setattr("src.api.admin.endpoints.batch_create_sessions.emit_admin_mutation_audit", _capture_audit)

    response = await client.post("/ui/api/batch-create-sessions/session-1/expire", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    assert response.json() == {
        "session_id": "session-1",
        "target_batch_id": "batch-1",
        "status": "expired",
        "expired": True,
        "artifact_deleted": False,
    }
    assert test_app.state.batch_create_session_admin_service.expire_calls == ["session-1"]
    assert recorded[0]["action"] == AuditAction.ADMIN_BATCH_CREATE_SESSION_EXPIRE
