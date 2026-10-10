from datetime import UTC, datetime
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.requests import Request

from src.billing.accounting_protocol import AccountingOperationHandle
from src.chat.telemetry import emit_precommit_failure
from src.models.errors import BudgetExceededError, ServiceUnavailableError

pytestmark = pytest.mark.hermetic


def _request(
    *,
    accounting_v2: bool,
    operation: object | None = None,
) -> tuple[Request, AsyncMock]:
    failure_log = AsyncMock()
    app = SimpleNamespace(
        state=SimpleNamespace(
            accounting_protocol_enabled=accounting_v2,
            spend_tracking_service=SimpleNamespace(log_request_failure=failure_log),
            settings=SimpleNamespace(openai_base_url=None),
            turn_off_message_logging=True,
        )
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat/completions",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 1),
            "app": app,
        }
    )
    if operation is not None:
        request.state.spend_operation_handle = operation
    return request, failure_log


def _auth() -> SimpleNamespace:
    auth = SimpleNamespace(
        api_key="key-1",
        user_id="user-1",
        team_id="team-1",
        organization_id="org-1",
        owner_account_id=None,
    )
    auth.model_dump = MagicMock(return_value={"api_key": "key-1"})
    return auth


async def _emit(
    request: Request,
    monkeypatch: pytest.MonkeyPatch,
    *,
    exc: Exception,
    audit_failure: Exception | None = None,
) -> tuple[AsyncMock, MagicMock]:
    audit = AsyncMock(side_effect=audit_failure)
    diagnostic = MagicMock()
    monkeypatch.setattr("src.chat.telemetry.emit_text_audit_event", audit)
    monkeypatch.setattr(
        "src.telemetry.request_failures.increment_optional_request_diagnostic",
        diagnostic,
    )
    callback_manager = SimpleNamespace(
        dispatch_failure_callbacks=MagicMock(),
        execute_post_call_failure_hooks=AsyncMock(),
    )
    guardrails = SimpleNamespace(run_post_call_failure=AsyncMock())
    deployment = SimpleNamespace(
        deployment_id="deployment-1",
        deltallm_params={
            "api_base": "http://provider.test/v1",
            "model": "openai/test-model",
        },
    )
    await emit_precommit_failure(
        request=request,
        auth=_auth(),
        payload=SimpleNamespace(model="test-model"),
        primary_deployment=deployment,
        callback_manager=callback_manager,
        guardrail_middleware=guardrails,
        request_data={"model": "test-model"},
        callback_start=datetime.now(UTC),
        request_start=perf_counter(),
        request_id="request-1",
        cache_hit=False,
        cache_key=None,
        audit_action="CHAT_COMPLETION_REQUEST",
        api_provider="openai",
        api_base="http://provider.test/v1",
        exc=exc,
        status_code=int(getattr(exc, "status_code", 500) or 500),
        stream=False,
    )
    guardrails.run_post_call_failure.assert_awaited_once()
    callback_manager.execute_post_call_failure_hooks.assert_awaited_once()
    return audit, diagnostic


@pytest.mark.asyncio
async def test_accounting_pre_dispatch_failure_never_enters_legacy_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, failure_log = _request(accounting_v2=True)

    audit, diagnostic = await _emit(
        request,
        monkeypatch,
        exc=ServiceUnavailableError(message="accounting reservation unavailable"),
    )

    failure_log.assert_not_awaited()
    audit.assert_not_awaited()
    diagnostic.assert_called_once_with(
        route="chat_completions",
        reason="dependency_unavailable",
    )


@pytest.mark.asyncio
async def test_accounting_pre_dispatch_policy_denial_keeps_required_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, failure_log = _request(accounting_v2=True)

    audit, diagnostic = await _emit(request, monkeypatch, exc=BudgetExceededError())

    failure_log.assert_not_awaited()
    audit.assert_awaited_once()
    diagnostic.assert_called_once_with(
        route="chat_completions",
        reason="client_rejection",
    )


@pytest.mark.asyncio
async def test_accounting_dispatched_failure_uses_terminal_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation = AccountingOperationHandle.model_construct()
    request, failure_log = _request(accounting_v2=True, operation=operation)

    audit, diagnostic = await _emit(
        request,
        monkeypatch,
        exc=ServiceUnavailableError(message="provider unavailable"),
    )

    failure_log.assert_awaited_once()
    assert failure_log.await_args.kwargs["operation"] is operation
    audit.assert_awaited_once()
    diagnostic.assert_not_called()


@pytest.mark.asyncio
async def test_required_policy_audit_failure_is_not_shed(monkeypatch: pytest.MonkeyPatch) -> None:
    request, failure_log = _request(accounting_v2=True)
    with pytest.raises(RuntimeError, match="required policy audit"):
        await _emit(
            request,
            monkeypatch,
            exc=BudgetExceededError(),
            audit_failure=RuntimeError("required policy audit failed"),
        )
    failure_log.assert_not_awaited()


@pytest.mark.asyncio
async def test_optional_failure_does_not_call_a_failed_legacy_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, failure_log = _request(accounting_v2=True)
    failure_log.side_effect = RuntimeError("legacy writer unavailable")
    await _emit(request, monkeypatch, exc=ServiceUnavailableError(message="admission unavailable"))
    failure_log.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_failure_ownership_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, failure_log = _request(accounting_v2=False)

    audit, diagnostic = await _emit(
        request,
        monkeypatch,
        exc=ServiceUnavailableError(message="provider unavailable"),
    )

    failure_log.assert_awaited_once()
    assert "operation" not in failure_log.await_args.kwargs
    audit.assert_awaited_once()
    diagnostic.assert_not_called()
