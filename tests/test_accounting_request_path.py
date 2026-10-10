import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.requests import Request

from src.billing.accounting_protocol import (
    AccountingOutcome,
    DispatchPermit,
    FinalizationReceipt,
    ReserveDecision,
)
from src.billing.accounting_service import AccountingProtocolService
from src.billing.provider_allowance import ProviderRequestBounds
from src.billing.spend_ingestion import SpendIngestionConfig, SpendIngestionService
from src.billing.spend_operations import SpendPersistenceUnavailable
from src.cache.backends.base import CacheEntry
from src.cache.middleware import CacheMiddleware
from src.models.errors import BudgetExceededError
from src.models.responses import UserAPIKeyAuth
from src.models.requests import ChatCompletionRequest
from src.router.router import Deployment
from src.telemetry.provider_request_bounds import validated_provider_request_bounds
from src.telemetry.spend_operation import durable_provider_call, operation_handle


class _AccountingRepository:
    def __init__(self, *, decision=ReserveDecision.DISPATCH):
        self.reservations = []
        self.finalizations = []
        self.decision = decision

    async def reserve_batch(self, values, *, expires_at):
        self.reservations.extend(values)
        return [
            DispatchPermit(
                protocol_generation=value.protocol_generation,
                operation_id=value.operation_id,
                decision=self.decision,
                dispatch_token=value.owner_token
                if self.decision is ReserveDecision.DISPATCH
                else None,
                accounting_partition=0 if self.decision is ReserveDecision.DISPATCH else None,
            )
            for value in values
        ]

    async def finalize_batch(self, values, *, expires_at):
        self.finalizations.extend(values)
        return [
            FinalizationReceipt(
                protocol_generation=value.protocol_generation,
                operation_id=value.operation_id,
                event_sequence=index + 1,
                outcome=value.outcome,
            )
            for index, value in enumerate(values)
        ]


def _request(accounting, ingestion, *, body=None):
    payload = json.dumps(body or {"model": "gpt-test", "max_tokens": 10}).encode()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": payload, "more_body": False}

    app = SimpleNamespace(
        state=SimpleNamespace(
            spend_tracking_service=ingestion,
            accounting_protocol_service=accounting,
            accounting_max_provider_attempts=3,
            tier_policy_service=None,
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
        },
        receive,
    )
    request.state.user_api_key = UserAPIKeyAuth(
        api_key="key-1",
        user_id="user-1",
        team_id="team-1",
        organization_id="org-1",
    )
    return request


def _deployment(*, input_rate="0.01", output_rate="0.02"):
    return Deployment(
        deployment_id=f"deployment-{input_rate}-{output_rate}",
        model_name="gpt-test",
        deltallm_params={"model": "gpt-test", "custom_llm_provider": "openai"},
        model_info={
            "max_input_tokens": 100,
            "max_output_tokens": 20,
            "input_cost_per_token": input_rate,
            "output_cost_per_token": output_rate,
            "cost_per_request": "0.1",
        },
    )


async def test_provider_dispatch_and_terminal_success_each_require_one_durable_ack():
    repository = _AccountingRepository()
    accounting = AccountingProtocolService(
        repository,
        generation=7,
        max_batch_size=8,
        dwell_seconds=0,
    )
    accounting.start()
    ingestion = SpendIngestionService(
        db_client=None,
        writer=MagicMock(),
        config=SpendIngestionConfig(),
        accounting=accounting,
    )
    request = _request(accounting, ingestion)
    execute = AsyncMock(return_value="provider-result")
    try:
        result = await durable_provider_call(
            request,
            model="gpt-test",
            call_type="completion",
            deployment=_deployment(),
            bounds=ProviderRequestBounds(max_output_tokens=10),
            execute=execute,
        )
        handle = operation_handle(request)
        assert result == "provider-result"
        assert handle is not None
        assert repository.reservations[0].allowance == Decimal("3.900000000000000000")
        await ingestion.log_spend(
            event_id=str(handle.reservation.operation_id),
            operation=handle,
            request_id="request-1",
            api_key="key-1",
            user_id="user-1",
            team_id="team-1",
            organization_id="org-1",
            owner_account_id=None,
            end_user_id=None,
            model="gpt-test",
            call_type="completion",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
            cost=0.3,
        )
        assert repository.finalizations[0].outcome is AccountingOutcome.COMPLETED
        assert repository.finalizations[0].exact_charge == Decimal("0.3")
        assert repository.finalizations[0].unresolved_attempts == 0
        execute.assert_awaited_once()
    finally:
        await accounting.close()


async def test_failover_cannot_exceed_the_original_durable_allowance():
    repository = _AccountingRepository()
    accounting = AccountingProtocolService(repository, generation=7, dwell_seconds=0)
    accounting.start()
    ingestion = SpendIngestionService(
        db_client=None,
        writer=MagicMock(),
        config=SpendIngestionConfig(),
        accounting=accounting,
    )
    request = _request(accounting, ingestion)
    first = AsyncMock(side_effect=RuntimeError("upstream failed"))
    second = AsyncMock(return_value="must-not-run")
    try:
        with pytest.raises(RuntimeError, match="upstream failed"):
            await durable_provider_call(
                request,
                model="gpt-test",
                call_type="completion",
                deployment=_deployment(),
                bounds=ProviderRequestBounds(max_output_tokens=10),
                execute=first,
            )
        with pytest.raises(SpendPersistenceUnavailable):
            await durable_provider_call(
                request,
                model="gpt-test",
                call_type="completion",
                deployment=_deployment(input_rate="1", output_rate="2"),
                bounds=ProviderRequestBounds(max_output_tokens=10),
                execute=second,
            )
        second.assert_not_awaited()
        assert len(repository.reservations) == 1
    finally:
        await accounting.close()


async def test_admission_uses_transformed_shape_without_reading_original_body():
    repository = _AccountingRepository()
    accounting = AccountingProtocolService(repository, generation=7, dwell_seconds=0)
    accounting.start()
    ingestion = SpendIngestionService(
        db_client=None, writer=MagicMock(), config=SpendIngestionConfig(), accounting=accounting
    )
    request = _request(accounting, ingestion, body={"model": "gpt-test", "max_tokens": 1, "n": 1})
    request.json = AsyncMock(side_effect=AssertionError("must not parse the body again"))
    payload = ChatCompletionRequest(
        model="gpt-test", messages=[{"role": "user", "content": "test"}], max_tokens=100, n=3
    )
    execute = AsyncMock(return_value="provider-result")
    try:
        await durable_provider_call(
            request,
            model=payload.model,
            call_type="completion",
            deployment=_deployment(),
            bounds=validated_provider_request_bounds(payload),
            execute=execute,
        )
        assert repository.reservations[0].allowance == Decimal("27.3")
        request.json.assert_not_awaited()
        execute.assert_awaited_once()
    finally:
        await accounting.close()


async def test_accounting_dispatch_without_validated_bounds_fails_closed():
    repository = _AccountingRepository()
    accounting = AccountingProtocolService(repository, generation=7, dwell_seconds=0)
    accounting.start()
    ingestion = SpendIngestionService(
        db_client=None, writer=MagicMock(), config=SpendIngestionConfig(), accounting=accounting
    )
    execute = AsyncMock()
    try:
        with pytest.raises(SpendPersistenceUnavailable):
            await durable_provider_call(
                _request(accounting, ingestion),
                model="gpt-test",
                call_type="completion",
                deployment=_deployment(),
                execute=execute,
            )
        assert repository.reservations == []
        execute.assert_not_awaited()
    finally:
        await accounting.close()


@pytest.mark.parametrize("runtime", [None, object()])
@pytest.mark.parametrize("path", ["provider", "cache"])
async def test_configured_accounting_cannot_fall_back_when_its_owner_is_missing(runtime, path):
    ingestion = SpendIngestionService(
        db_client=None, writer=MagicMock(), config=SpendIngestionConfig()
    )
    request = _request(runtime, ingestion)
    request.app.state.accounting_protocol_enabled = True
    execute = AsyncMock()
    with pytest.raises(SpendPersistenceUnavailable):
        if path == "provider":
            await durable_provider_call(
                request,
                model="gpt-test",
                call_type="completion",
                deployment=_deployment(),
                bounds=ProviderRequestBounds(max_output_tokens=10),
                execute=execute,
            )
        else:
            await CacheMiddleware(AsyncMock())._record_cache_hit_accounting(
                request, "/v1/chat/completions", "gpt-test", "cache:test", _priced_cache_entry()
            )
    execute.assert_not_awaited()
    ingestion.writer.log_spend.assert_not_called()


def _priced_cache_entry():
    return CacheEntry(
        response={"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
        model="gpt-test",
        cached_at=0,
        ttl=60,
        deployment_id="cached-deployment",
        provider="openai",
        deployment_model="gpt-test",
        pricing={
            "input_cost_per_token": "0.01",
            "output_cost_per_token": "0.02",
            "input_cost_per_token_cache_hit": "0.002",
            "output_cost_per_token_cache_hit": "0.003",
            "cost_per_request": "0.1",
        },
    )


async def test_paid_cache_hit_uses_the_same_reservation_and_finalization_owner():
    repository = _AccountingRepository()
    accounting = AccountingProtocolService(repository, generation=7, dwell_seconds=0)
    accounting.start()
    ingestion = SpendIngestionService(
        db_client=None, writer=MagicMock(), config=SpendIngestionConfig(), accounting=accounting
    )
    request = _request(accounting, ingestion)
    request.json = AsyncMock(side_effect=AssertionError("cache billing must not read the body"))
    try:
        await CacheMiddleware(AsyncMock())._record_cache_hit_accounting(
            request, "/v1/chat/completions", "gpt-test", "cache:test", _priced_cache_entry()
        )
        assert len(repository.reservations) == 1
        assert repository.reservations[0].allowance == Decimal("0.135")
        assert repository.reservations[0].attribution.api_key == "key-1"
        assert (
            repository.reservations[0].audit_envelope["payload"]["event"]["action"]
            == "ACCOUNTING_CACHE_RESERVED"
        )
        assert len(repository.finalizations) == 1
        terminal = repository.finalizations[0]
        assert terminal.exact_charge == Decimal("0.135")
        assert terminal.spend_payload["cache_hit"] is True
        assert terminal.spend_payload["metadata"]["provider_cost"] == 0
        assert terminal.audit_envelope["payload"]["event"]["metadata"]["cache_hit"] is True
        ingestion.writer.log_spend.assert_not_called()
        request.json.assert_not_awaited()
    finally:
        await accounting.close()


async def test_exhausted_budget_cannot_serve_a_paid_cache_hit():
    repository = _AccountingRepository(decision=ReserveDecision.BUDGET_EXHAUSTED)
    accounting = AccountingProtocolService(repository, generation=7, dwell_seconds=0)
    accounting.start()
    ingestion = SpendIngestionService(
        db_client=None, writer=MagicMock(), config=SpendIngestionConfig(), accounting=accounting
    )
    try:
        with pytest.raises(BudgetExceededError):
            await CacheMiddleware(AsyncMock())._record_cache_hit_accounting(
                _request(accounting, ingestion),
                "/v1/chat/completions",
                "gpt-test",
                "cache:test",
                _priced_cache_entry(),
            )
        assert len(repository.reservations) == 1
        assert repository.finalizations == []
        ingestion.writer.log_spend.assert_not_called()
    finally:
        await accounting.close()
