from __future__ import annotations

import ast
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.billing.selector_charge import (
    AcceptedSelectorCharge,
    SelectorChargeAttribution,
    SelectorPriceSnapshot,
    SelectorTokenReceipt,
)
from src.billing.spend import SpendTrackingService
from src.billing.spend_ingestion import (
    SelectorChargeUnavailableError,
    SpendIngestionConfig,
    SpendIngestionOverloadedError,
    SpendIngestionService,
)
from src.db.spend_ingestion import SpendEnqueueResult


def make_selector_charge(**price_overrides: object) -> AcceptedSelectorCharge:
    now = datetime.now(UTC)
    return AcceptedSelectorCharge(
        attribution=SelectorChargeAttribution(
            operation_id=uuid4(),
            api_key="verified-key-hash",
            user_id="verified-user",
            team_id="verified-team",
            organization_id="verified-org",
            owner_account_id="verified-owner",
            model_group="customer-model-group",
            deployment_id="concrete-selector",
            deployment_model="small-model",
            provider="openai",
        ),
        pricing=SelectorPriceSnapshot(
            **{
                "source": "deployment-pricing",
                "version": "revision-7",
                "input_cost_per_token": Decimal("0.000000150000000001"),
                "output_cost_per_token": Decimal("0.000000600000000003"),
                "cost_per_request": Decimal("0.000000000000000005"),
                **price_overrides,
            }
        ),
        usage=SelectorTokenReceipt(prompt_tokens=100, completion_tokens=5, total_tokens=105),
        started_at=now,
        finished_at=now + timedelta(milliseconds=30),
    )


def test_selector_customer_pays_exact_provider_cost_without_markup() -> None:
    charge = make_selector_charge()
    with localcontext() as context:
        context.prec = 5  # Ambient application Decimal precision cannot change billing.
        assert charge.customer_charge == Decimal("0.000018000000000120")
        assert charge.provider_cost == charge.customer_charge
    row = (
        SpendTrackingService(None)
        .prepare_batch_event(
            event_id=charge.attribution.component_event_id,
            event_type="spend",
            payload=charge.spend_payload(),
        )
        .row
    )
    assert row["spend_exact"] == row["provider_cost_exact"] == "0.000018000000000120"
    assert (
        row["model"] == "customer-model-group"
    )  # The caller's model budget, not hidden deployment.
    assert row["deployment_model"] == "small-model"
    assert row["call_type"] == "model_router_selector"
    assert row["metadata"]["component_purpose"] == "selector:v1"
    assert row["metadata"]["parent_event_id"] == str(charge.attribution.operation_id)
    assert row["id"] != row["request_id"]


def test_selector_receipt_replay_freezes_identity_attribution_prices_and_currency() -> None:
    charge = make_selector_charge()
    replay = AcceptedSelectorCharge.model_validate_json(charge.model_dump_json())
    assert replay == charge
    assert replay.attribution.component_event_id == charge.attribution.component_event_id
    assert replay.customer_charge == charge.customer_charge
    assert replay.spend_payload() == charge.spend_payload()
    assert (
        make_selector_charge().attribution.component_event_id
        != charge.attribution.component_event_id
    )
    assert replay.pricing.currency == "USD"
    assert replay.pricing.rounding == "ROUND_HALF_EVEN"
    with pytest.raises(ValidationError):
        replay.pricing.input_cost_per_token = Decimal(0)
    with pytest.raises(ValidationError):
        replay.attribution.team_id = "other-team"


@pytest.mark.parametrize("kind", ["unknown", "not_attempted"])
def test_unknown_or_unattempted_usage_cannot_become_a_billable_receipt(kind: str) -> None:
    with pytest.raises(ValidationError):
        SelectorTokenReceipt(kind=kind, prompt_tokens=0, completion_tokens=0, total_tokens=0)


@pytest.mark.parametrize(
    "overrides",
    [
        {"total_tokens": 106},
        {"prompt_tokens": -1},
        {"completion_tokens": True},
        {"cached_input_tokens": 101},
        {"prompt_tokens": 2**31},
        {"prompt_tokens": 100.0},
    ],
)
def test_selector_receipt_rejects_inconsistent_or_unrepresentable_usage(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        SelectorTokenReceipt(
            **{"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105, **overrides}
        )


@pytest.mark.parametrize(
    "rate",
    [
        0.1,
        "0.1",
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("1e20"),
        Decimal("1e-19"),
    ],
)
def test_pricing_rejects_float_missing_precision_and_invalid_money(rate: object) -> None:
    with pytest.raises(ValidationError):
        make_selector_charge(input_cost_per_token=rate)


def test_selector_does_not_charge_full_input_rate_when_cache_discount_usage_is_missing() -> None:
    with pytest.raises(ValidationError, match="missing required cached-input usage"):
        make_selector_charge(input_cost_per_token_cache_hit=Decimal("0.00000001"))
    charge = make_selector_charge()
    pricing = SelectorPriceSnapshot.model_validate(
        {**charge.pricing.model_dump(), "input_cost_per_token_cache_hit": Decimal("0.00000001")}
    )
    usage = SelectorTokenReceipt(
        prompt_tokens=100, completion_tokens=5, total_tokens=105, cached_input_tokens=80
    )
    assert pricing.cost(usage) == Decimal("0.000006800000000040")


def test_zero_price_requires_explicit_rates_and_valid_receipt() -> None:
    charge = make_selector_charge(
        input_cost_per_token=Decimal(0),
        output_cost_per_token=Decimal(0),
        cost_per_request=Decimal(0),
    )
    assert charge.customer_charge == 0
    data = charge.pricing.model_dump()
    del data["output_cost_per_token"]
    with pytest.raises(ValidationError):
        SelectorPriceSnapshot.model_validate(data)


def test_selector_charge_rejects_naive_reversed_timestamps_and_cost_overflow() -> None:
    charge = make_selector_charge()
    for update in (
        {"started_at": datetime(2026, 9, 8)},
        {"finished_at": charge.started_at - timedelta(seconds=1)},
    ):
        with pytest.raises(ValidationError):
            AcceptedSelectorCharge.model_validate({**charge.model_dump(), **update})
    with pytest.raises(ValidationError, match="NUMERIC"):
        make_selector_charge(input_cost_per_token=Decimal("99999999999999999999"))


def test_provider_exact_amount_survives_legacy_mirror_float_roundtrip() -> None:
    charge = make_selector_charge()
    payload = charge.spend_payload()
    payload["cost_exact"] = "9007199254740992.000000000000000001"
    payload["provider_cost_exact"] = "9007199254740992.000000000000000003"
    prepared = SpendTrackingService(None).prepare_batch_event(
        event_id="event", event_type="spend", payload=payload
    )
    assert prepared.row["spend_exact"] == payload["cost_exact"]
    assert prepared.row["provider_cost_exact"] == payload["provider_cost_exact"]
    assert prepared.row["spend"] == prepared.row["provider_cost"] == 9007199254740992.0


def test_legacy_provider_cost_payload_still_works() -> None:
    payload = make_selector_charge().spend_payload()
    del payload["provider_cost_exact"]
    payload["metadata"]["provider_cost"] = 0.1
    row = (
        SpendTrackingService(None)
        .prepare_batch_event(event_id="event", event_type="spend", payload=payload)
        .row
    )
    assert row["provider_cost_exact"] == "0.100000000000000000"


@pytest.mark.asyncio
async def test_selector_ingress_uses_existing_outbox_and_stable_child_event() -> None:
    service = SpendIngestionService(
        db_client=object(),
        writer=SpendTrackingService(None),
        config=SpendIngestionConfig(enabled=True),
    )
    service.repository.enqueue = AsyncMock(
        return_value=SpendEnqueueResult(status="accepted", pending_count=1)
    )
    charge = make_selector_charge()
    await service.log_selector_charge(charge, expires_at=asyncio.get_running_loop().time() + 1)
    await service.log_selector_charge(
        AcceptedSelectorCharge.model_validate_json(charge.model_dump_json()),
        expires_at=asyncio.get_running_loop().time() + 1,
    )
    assert service.repository.enqueue.await_count == 2
    first, second = service.repository.enqueue.await_args_list
    assert first == second
    assert first.kwargs["event_id"] == charge.attribution.component_event_id
    payload = first.kwargs["payload"]
    assert payload["cost_exact"] == payload["provider_cost_exact"] == "0.000018000000000120"
    assert payload["start_time"].endswith("+00:00")
    assert "verified-key-hash" not in repr(charge)
    assert not any(
        term in json.dumps(payload)
        for term in ("messages", "prompt_text", "api_base", "authorization")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["disabled", "closed", "missing_db"])
async def test_selector_ingress_never_falls_back_to_non_durable_billing(state: str) -> None:
    service = SpendIngestionService(
        db_client=None if state == "missing_db" else object(),
        writer=SpendTrackingService(None),
        config=SpendIngestionConfig(enabled=state != "disabled"),
    )
    service._closed = state == "closed"
    with pytest.raises(SelectorChargeUnavailableError):
        await service.log_selector_charge(
            make_selector_charge(), expires_at=asyncio.get_running_loop().time() + 1
        )


def test_exact_charge_contract_is_small_typed_and_has_no_io_or_edge_dependencies() -> None:
    root = Path(__file__).parents[1]
    source = (root / "src/billing/selector_charge.py").read_text()
    assert len(source.splitlines()) < 500
    for node in ast.walk(ast.parse(source)):
        assert not isinstance(node, ast.AsyncFunctionDef)
        if isinstance(node, ast.FunctionDef):
            assert node.end_lineno - node.lineno < 80
        if isinstance(node, ast.Name):
            assert node.id not in {"Any", "Request", "getattr", "hasattr"}
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith(
                ("src.db", "fastapi", "redis", "httpx", "src.api", "src.router")
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("expires_at", [0.0, float("nan"), float("inf"), True])
async def test_invalid_or_expired_selector_charge_deadline_does_not_enqueue(expires_at) -> None:
    service = SpendIngestionService(
        db_client=object(),
        writer=SpendTrackingService(None),
        config=SpendIngestionConfig(enabled=True),
    )
    service.repository.enqueue = AsyncMock()
    with pytest.raises((ValueError, SelectorChargeUnavailableError)):
        await service.log_selector_charge(make_selector_charge(), expires_at=expires_at)
    service.repository.enqueue.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_selector_receipt_timeout_and_cancellation_propagate_without_detached_work(
    cancel,
) -> None:
    service = SpendIngestionService(
        db_client=object(),
        writer=SpendTrackingService(None),
        config=SpendIngestionConfig(enabled=True),
    )
    started, closed = asyncio.Event(), asyncio.Event()

    async def blocked_enqueue(**kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    service.repository.enqueue = AsyncMock(side_effect=blocked_enqueue)
    task = asyncio.create_task(
        service.log_selector_charge(
            make_selector_charge(),
            expires_at=asyncio.get_running_loop().time() + (1 if cancel else 0.01),
        )
    )
    try:
        await started.wait()
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else SelectorChargeUnavailableError):
            await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert closed.is_set()
    assert service.repository.enqueue.await_count == 1


@pytest.mark.asyncio
async def test_selector_receipt_capacity_rejection_propagates() -> None:
    service = SpendIngestionService(
        db_client=object(),
        writer=SpendTrackingService(None),
        config=SpendIngestionConfig(enabled=True, overload_policy="fail_closed"),
    )
    service.repository.enqueue = AsyncMock(
        return_value=SpendEnqueueResult(status="full", pending_count=100_000)
    )
    with pytest.raises(SpendIngestionOverloadedError):
        await service.log_selector_charge(
            make_selector_charge(), expires_at=asyncio.get_running_loop().time() + 1
        )
    assert service.repository.enqueue.await_count == 1


@pytest.mark.asyncio
async def test_exact_ledger_batch_addition_is_independent_of_ambient_decimal_precision() -> None:
    db = SimpleNamespace(
        query_raw=AsyncMock(return_value=[{"id": "first"}, {"id": "second"}]),
        execute_raw=AsyncMock(return_value=1),
    )
    writer = SpendTrackingService(db)
    first = make_selector_charge().spend_payload()
    second = dict(first)
    first["cost_exact"] = "9007199254740992.000000000000000001"
    second["cost_exact"] = "0.000000000000000002"
    with localcontext() as context:
        context.prec = 5
        await writer.log_batch_once([("first", "spend", first), ("second", "spend", second)])
    key_update = next(
        call
        for call in db.execute_raw.await_args_list
        if "UPDATE deltallm_verificationtoken" in call.args[0]
    )
    assert key_update.args[2] == ["9007199254740992.000000000000000003"]


def test_selector_charging_is_not_invoked_by_production_request_paths() -> None:
    for path in (Path(__file__).parents[1] / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr != "log_selector_charge", path


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [TimeoutError("database detail"), RuntimeError("private database detail")]
)
async def test_selector_receipt_dependency_failure_is_not_a_selector_safe_default(error) -> None:
    service = SpendIngestionService(
        db_client=object(),
        writer=SpendTrackingService(None),
        config=SpendIngestionConfig(enabled=True),
    )
    service.repository.enqueue = AsyncMock(side_effect=error)
    with pytest.raises(SelectorChargeUnavailableError) as raised:
        await service.log_selector_charge(
            make_selector_charge(), expires_at=asyncio.get_running_loop().time() + 1
        )
    assert not isinstance(raised.value, TimeoutError)
    assert raised.value.status_code == 503
    assert "database detail" not in str(raised.value)


def test_legacy_spend_preparation_is_one_bounded_io_free_mapping_seam() -> None:
    root = Path(__file__).parents[1] / "src/billing"
    source = (root / "spend_preparation.py").read_text()
    assert len(source.splitlines()) < 200
    for node in ast.walk(ast.parse(source)):
        assert not isinstance(node, ast.AsyncFunctionDef)
        if isinstance(node, ast.FunctionDef):
            assert node.end_lineno - node.lineno < 80
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith(("src.db", "fastapi", "redis", "httpx"))
    # The only dynamic boundary is the pre-existing historical outbox shape;
    # keep it contained here instead of growing another mapper in the writer.
    mapper = next(
        node
        for node in ast.walk(ast.parse((root / "spend.py").read_text()))
        if isinstance(node, ast.FunctionDef) and node.name == "prepare_batch_event"
    )
    assert len(mapper.body) == 2  # docstring and delegation
    assert isinstance(mapper.body[1], ast.Return)
