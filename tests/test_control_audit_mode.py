from __future__ import annotations

from time import perf_counter
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from src.api.audit import emit_control_audit_event
from src.services.audit_service import RequiredAuditPersistenceError


class _RecordingAuditService:
    def __init__(self) -> None:
        self.sync_calls: list[tuple[object, list[object], object | None]] = []
        self.async_calls: list[tuple[object, list[object], bool]] = []

    async def record_event_sync(  # noqa: ANN201
        self,
        event,  # noqa: ANN001
        *,
        payloads=None,  # noqa: ANN001
        repository=None,  # noqa: ANN001
    ):
        self.sync_calls.append((event, list(payloads or []), repository))

    def record_event(self, event, *, payloads=None, critical=False):  # noqa: ANN001, ANN201
        self.async_calls.append((event, list(payloads or []), critical))


class _FailingSyncAuditService(_RecordingAuditService):
    async def record_event_sync(  # noqa: ANN201
        self,
        event,  # noqa: ANN001
        *,
        payloads=None,  # noqa: ANN001
        repository=None,  # noqa: ANN001
    ):
        del event, payloads, repository
        raise RuntimeError("audit database unavailable")


def _build_request(app: FastAPI) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/ui/api/keys",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "app": app,
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_control_audit_default_is_sync_for_critical():
    app = FastAPI()
    app.state.audit_service = _RecordingAuditService()
    # Intentionally omit app_config.general_settings to verify safe default behavior.
    app.state.app_config = SimpleNamespace()
    request = _build_request(app)

    await emit_control_audit_event(
        request=request,
        request_start=perf_counter(),
        action="ADMIN_KEY_CREATE",
        status="success",
        critical=True,
    )

    assert len(app.state.audit_service.sync_calls) == 1
    assert len(app.state.audit_service.async_calls) == 0


@pytest.mark.asyncio
async def test_control_audit_can_be_queued_when_sync_disabled():
    app = FastAPI()
    app.state.audit_service = _RecordingAuditService()
    app.state.app_config = SimpleNamespace(
        general_settings=SimpleNamespace(
            audit_control_sync_enabled=False,
            audit_control_sync_actions=[],
        )
    )
    request = _build_request(app)

    await emit_control_audit_event(
        request=request,
        request_start=perf_counter(),
        action="ADMIN_KEY_CREATE",
        status="success",
        critical=True,
    )

    assert len(app.state.audit_service.sync_calls) == 0
    assert len(app.state.audit_service.async_calls) == 1
    assert app.state.audit_service.async_calls[0][2] is True


@pytest.mark.asyncio
async def test_control_audit_allowlist_keeps_sync_when_disabled():
    app = FastAPI()
    app.state.audit_service = _RecordingAuditService()
    app.state.app_config = SimpleNamespace(
        general_settings=SimpleNamespace(
            audit_control_sync_enabled=False,
            audit_control_sync_actions=["ADMIN_KEY_CREATE"],
        )
    )
    request = _build_request(app)

    await emit_control_audit_event(
        request=request,
        request_start=perf_counter(),
        action="ADMIN_KEY_CREATE",
        status="success",
        critical=True,
    )

    assert len(app.state.audit_service.sync_calls) == 1
    assert len(app.state.audit_service.async_calls) == 0


@pytest.mark.asyncio
async def test_transactional_control_audit_forces_sync_on_bound_repository():
    app = FastAPI()
    app.state.audit_service = _RecordingAuditService()
    app.state.app_config = SimpleNamespace(
        general_settings=SimpleNamespace(
            audit_control_sync_enabled=False,
            audit_control_sync_actions=[],
        )
    )
    request = _build_request(app)
    transactional_repository = object()

    await emit_control_audit_event(
        request=request,
        request_start=perf_counter(),
        action="ADMIN_BATCH_WEBHOOK_REPLAY",
        status="success",
        critical=True,
        transactional_repository=transactional_repository,  # type: ignore[arg-type]
    )

    assert len(app.state.audit_service.sync_calls) == 1
    assert app.state.audit_service.sync_calls[0][2] is transactional_repository
    assert len(app.state.audit_service.async_calls) == 0


@pytest.mark.asyncio
async def test_force_sync_overrides_queued_mode_and_preserves_event_id():
    app = FastAPI()
    app.state.audit_service = _RecordingAuditService()
    app.state.app_config = SimpleNamespace(
        general_settings=SimpleNamespace(
            audit_control_sync_enabled=False,
            audit_control_sync_actions=[],
        )
    )
    request = _build_request(app)

    await emit_control_audit_event(
        request=request,
        request_start=perf_counter(),
        action="ADMIN_UI_BRANDING_RESET",
        status="attempted",
        critical=True,
        force_sync=True,
        event_id="reset-attempt-event",
    )

    assert len(app.state.audit_service.sync_calls) == 1
    event = app.state.audit_service.sync_calls[0][0]
    assert getattr(event, "event_id") == "reset-attempt-event"
    assert len(app.state.audit_service.async_calls) == 0


@pytest.mark.asyncio
async def test_force_sync_normalizes_persistence_failure():
    app = FastAPI()
    app.state.audit_service = _FailingSyncAuditService()
    app.state.app_config = SimpleNamespace()
    request = _build_request(app)

    with pytest.raises(RequiredAuditPersistenceError):
        await emit_control_audit_event(
            request=request,
            request_start=perf_counter(),
            action="ADMIN_UI_BRANDING_RESET",
            status="attempted",
            force_sync=True,
        )
