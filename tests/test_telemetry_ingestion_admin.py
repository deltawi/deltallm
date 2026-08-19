from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.api.admin.endpoints.common import AuthScope
from src.audit.actions import AuditAction
from src.services.telemetry_replay import (
    TelemetryReplayService,
    TelemetryReplayUnavailableError,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("queue_name", ["spend", "audit"])
async def test_platform_admin_can_replay_blocked_telemetry_event(
    queue_name: str,
    client,
    test_app,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetry_db = object()
    test_app.state.settings.master_key = "mk-test"
    test_app.state.telemetry_prisma_manager = SimpleNamespace(client=telemetry_db)
    replay_calls: list[tuple[object, str, str]] = []
    audits: list[dict[str, object]] = []

    class FakeReplayService:
        def __init__(self, db: object, *, audit_service: object | None) -> None:
            del audit_service
            self.db = db

        async def replay_blocked(
            self,
            *,
            queue_name: str,
            event_id: str,
            replayed_by: str,
            audit_writer,
        ) -> bool:  # noqa: ANN001
            del queue_name
            replay_calls.append((self.db, event_id, replayed_by))
            await audit_writer(object())
            return True

    async def capture_audit(**kwargs: object) -> None:
        audits.append(kwargs)

    monkeypatch.setattr(
        "src.api.admin.endpoints.telemetry_ingestion.get_auth_scope",
        lambda *args, **kwargs: AuthScope(  # noqa: ARG005
            is_platform_admin=True,
            account_id="account-1",
        ),
    )
    monkeypatch.setattr(
        "src.api.admin.endpoints.telemetry_ingestion.TelemetryReplayService",
        FakeReplayService,
    )
    monkeypatch.setattr(
        "src.api.admin.endpoints.telemetry_ingestion.emit_admin_mutation_audit",
        capture_audit,
    )

    response = await client.post(
        f"/ui/api/telemetry-ingestion/{queue_name}/event-1/replay",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "replayed": True,
        "queue_name": queue_name,
        "event_id": "event-1",
    }
    assert replay_calls == [(telemetry_db, "event-1", "account-1")]
    assert audits[0]["action"] == AuditAction.ADMIN_TELEMETRY_INGESTION_REPLAY
    assert audits[0]["resource_id"] == "event-1"


@pytest.mark.asyncio
async def test_replay_rejects_non_blocked_telemetry_event(
    client,
    test_app,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_app.state.settings.master_key = "mk-test"
    test_app.state.telemetry_prisma_manager = SimpleNamespace(client=object())

    class FakeReplayService:
        def __init__(self, _db: object, *, audit_service: object | None) -> None:
            del audit_service

        async def replay_blocked(self, **_kwargs: object) -> bool:
            return False

    monkeypatch.setattr(
        "src.api.admin.endpoints.telemetry_ingestion.get_auth_scope",
        lambda *args, **kwargs: AuthScope(is_platform_admin=True),  # noqa: ARG005
    )
    monkeypatch.setattr(
        "src.api.admin.endpoints.telemetry_ingestion.TelemetryReplayService",
        FakeReplayService,
    )

    response = await client.post(
        "/ui/api/telemetry-ingestion/spend/event-1/replay",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Only blocked required telemetry events can be replayed"


@pytest.mark.asyncio
async def test_replay_requires_telemetry_database(client, test_app) -> None:
    test_app.state.settings.master_key = "mk-test"
    test_app.state.telemetry_prisma_manager = SimpleNamespace(client=None)

    response = await client.post(
        "/ui/api/telemetry-ingestion/audit/event-1/replay",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Telemetry database unavailable"


class _ReplayTransaction:
    def __init__(self, state: dict[str, bool]) -> None:
        self.state = state
        self.replayed = state["replayed"]

    def is_transaction(self) -> bool:
        return True


class _ReplayTransactionContext:
    def __init__(self, state: dict[str, bool]) -> None:
        self.state = state
        self.transaction = _ReplayTransaction(state)

    async def __aenter__(self) -> _ReplayTransaction:
        return self.transaction

    async def __aexit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
        del exc, traceback
        if exc_type is None:
            self.state["replayed"] = self.transaction.replayed


class _ReplayDatabase:
    def __init__(self) -> None:
        self.state = {"replayed": False}

    def tx(self) -> _ReplayTransactionContext:
        return _ReplayTransactionContext(self.state)


@pytest.mark.asyncio
async def test_replay_and_required_audit_commit_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _ReplayDatabase()
    audit_transactions: list[object] = []

    class FakeRepository:
        def __init__(self, db: _ReplayTransaction) -> None:
            self.db = db

        async def replay_blocked(self, *, event_id: str, replayed_by: str) -> bool:
            assert event_id == "event-1"
            assert replayed_by == "account-1"
            self.db.replayed = True
            return True

    async def write_audit(repository) -> None:  # noqa: ANN001
        audit_transactions.append(repository.prisma)

    monkeypatch.setattr("src.services.telemetry_replay.SpendIngestionRepository", FakeRepository)
    service = TelemetryReplayService(database, audit_service=object())

    replayed = await service.replay_blocked(
        queue_name="spend",
        event_id="event-1",
        replayed_by="account-1",
        audit_writer=write_audit,
    )

    assert replayed is True
    assert database.state["replayed"] is True
    assert len(audit_transactions) == 1
    assert isinstance(audit_transactions[0], _ReplayTransaction)


@pytest.mark.asyncio
async def test_email_delivery_audit_replay_uses_same_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _ReplayDatabase()
    audit_transactions: list[object] = []

    class FakeRepository:
        def __init__(self, db: _ReplayTransaction) -> None:
            self.db = db

        async def replay_blocked_delivery_audit(self, *, email_id: str, replayed_by: str) -> bool:
            assert email_id == "email-1"
            assert replayed_by == "account-1"
            self.db.replayed = True
            return True

    async def write_audit(repository) -> None:  # noqa: ANN001
        audit_transactions.append(repository.prisma)

    monkeypatch.setattr("src.services.telemetry_replay.EmailOutboxRepository", FakeRepository)
    service = TelemetryReplayService(database, audit_service=object())

    assert await service.replay_blocked(
        queue_name="email_delivery_audit",
        event_id="email-1",
        replayed_by="account-1",
        audit_writer=write_audit,
    )
    assert database.state["replayed"] is True
    assert isinstance(audit_transactions[0], _ReplayTransaction)


@pytest.mark.asyncio
async def test_unknown_email_resolution_uses_same_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _ReplayDatabase()
    audit_transactions: list[object] = []

    class FakeRepository:
        def __init__(self, db: _ReplayTransaction) -> None:
            self.db = db

        async def resolve_unknown_delivery(self, *, email_id: str, resolution: str) -> bool:
            assert email_id == "email-1"
            assert resolution == "sent"
            self.db.replayed = True
            return True

    async def write_audit(repository) -> None:  # noqa: ANN001
        audit_transactions.append(repository.prisma)

    monkeypatch.setattr("src.services.telemetry_replay.EmailOutboxRepository", FakeRepository)
    service = TelemetryReplayService(database, audit_service=object())

    assert await service.resolve_unknown_email_delivery(
        email_id="email-1",
        resolution="sent",
        audit_writer=write_audit,
    )
    assert database.state["replayed"] is True
    assert isinstance(audit_transactions[0], _ReplayTransaction)


@pytest.mark.asyncio
async def test_required_audit_failure_rolls_back_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _ReplayDatabase()

    class FakeRepository:
        def __init__(self, db: _ReplayTransaction) -> None:
            self.db = db

        async def replay_blocked(self, *, event_id: str, replayed_by: str) -> bool:
            del event_id, replayed_by
            self.db.replayed = True
            return True

    async def fail_audit(_repository) -> None:  # noqa: ANN001
        raise RuntimeError("audit insert failed")

    monkeypatch.setattr("src.services.telemetry_replay.AuditIngestionRepository", FakeRepository)
    service = TelemetryReplayService(database, audit_service=object())

    with pytest.raises(TelemetryReplayUnavailableError):
        await service.replay_blocked(
            queue_name="audit",
            event_id="event-1",
            replayed_by="account-1",
            audit_writer=fail_audit,
        )

    assert database.state["replayed"] is False


@pytest.mark.asyncio
async def test_replay_cancellation_rolls_back_and_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _ReplayDatabase()

    class FakeRepository:
        def __init__(self, db: _ReplayTransaction) -> None:
            self.db = db

        async def replay_blocked(self, *, event_id: str, replayed_by: str) -> bool:
            del event_id, replayed_by
            self.db.replayed = True
            return True

    async def cancel_audit(_repository) -> None:  # noqa: ANN001
        raise asyncio.CancelledError

    monkeypatch.setattr("src.services.telemetry_replay.SpendIngestionRepository", FakeRepository)
    service = TelemetryReplayService(database, audit_service=object())

    with pytest.raises(asyncio.CancelledError):
        await service.replay_blocked(
            queue_name="spend",
            event_id="event-1",
            replayed_by="account-1",
            audit_writer=cancel_audit,
        )

    assert database.state["replayed"] is False


@pytest.mark.asyncio
async def test_replay_fails_before_mutation_when_required_audit_is_unavailable() -> None:
    database = _ReplayDatabase()
    service = TelemetryReplayService(database, audit_service=None)

    async def write_audit(_repository) -> None:  # noqa: ANN001
        raise AssertionError("audit writer must not run")

    with pytest.raises(TelemetryReplayUnavailableError):
        await service.replay_blocked(
            queue_name="spend",
            event_id="event-1",
            replayed_by="account-1",
            audit_writer=write_audit,
        )

    assert database.state["replayed"] is False
