import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4, uuid5

import pytest
from pydantic import ValidationError

from src.billing.accounting.accounting_protocol import (
    AccountingAttribution,
    AccountingFinalization,
    AccountingOutcome,
    AccountingReservation,
    AccountingScope,
    BudgetWindowRef,
    DispatchPermit,
    ReserveDecision,
    request_fingerprint,
)
from src.billing.accounting.reporting.accounting_projection import (
    AccountingCompatibilityProjector,
    AccountingProjectionConfig,
)
from src.billing.accounting.durable_microbatch import DurableBatchClosed, DurableMicrobatcher
from src.billing.accounting.accounting_service import AccountingProtocolService
from src.billing.accounting.accounting_finalization import (
    accounting_audit_envelope as _accounting_audit_envelope,
)
from src.db.accounting.reporting.accounting_projection import (
    AccountingProjectionClaim,
    AccountingProjectionEvent,
    AccountingProjectionRepository,
)
from src.db.accounting.accounting_protocol import (
    AccountingProtocolRepository,
    AccountingProtocolUnavailable,
    AccountingResultFailure,
)
from src.db.runtime.telemetry_acceptance import AcceptanceFailure
from src.metrics.prometheus import get_prometheus_registry


def metric_sample(name: str, **labels: str) -> float:
    return get_prometheus_registry().get_sample_value(name, labels) or 0.0


def reservation(*, generation: int = 7) -> AccountingReservation:
    operation_id = uuid4()
    return AccountingReservation(
        protocol_generation=generation,
        operation_id=operation_id,
        owner_token=uuid4(),
        request_fingerprint=request_fingerprint(
            operation_kind="chat", payload={"operation_id": operation_id}
        ),
        attribution=AccountingAttribution(
            api_key="key-1",
            user_id="user-1",
            team_id="team-1",
            organization_id="org-1",
            model="model-group",
            deployment_id="deployment-1",
            provider="openai",
            call_type="chat",
        ),
        allowance=Decimal("1.25"),
        windows=(
            BudgetWindowRef(
                window_id=uuid4(),
                scope_type=AccountingScope.ORGANIZATION,
                scope_id="org-1",
                policy_generation=3,
            ),
        ),
        pricing_snapshot={"version": "price-v1"},
        audit_envelope={"action": "provider.dispatch"},
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )


def finalization(
    reserved: AccountingReservation, *, outcome: AccountingOutcome = AccountingOutcome.COMPLETED
) -> AccountingFinalization:
    completed = outcome is AccountingOutcome.COMPLETED
    return AccountingFinalization(
        protocol_generation=reserved.protocol_generation,
        operation_id=reserved.operation_id,
        owner_token=reserved.owner_token,
        request_fingerprint=reserved.request_fingerprint,
        component_id="provider-attempt-1",
        event_id=uuid4(),
        outcome=outcome,
        exact_charge=Decimal("0.75") if completed else None,
        spend_payload={"request_id": str(reserved.operation_id)} if completed else None,
        audit_envelope={"action": "provider.completed"},
        occurred_at=datetime.now(UTC),
        uncertainty_reason="provider_ack_lost" if outcome is AccountingOutcome.UNCERTAIN else None,
    )


def test_fingerprint_is_canonical_and_does_not_depend_on_mapping_order():
    assert request_fingerprint(operation_kind="chat", payload={"a": 1, "b": 2}) == (
        request_fingerprint(operation_kind="chat", payload={"b": 2, "a": 1})
    )
    assert request_fingerprint(operation_kind="chat", payload={"a": 1}) != (
        request_fingerprint(operation_kind="embedding", payload={"a": 1})
    )


def test_reservation_rejects_duplicate_scope_or_oversized_envelopes():
    item = reservation()
    with pytest.raises(ValidationError, match="duplicate budget scopes"):
        AccountingReservation.model_validate(
            {**item.model_dump(), "windows": (item.windows[0], item.windows[0])}
        )
    with pytest.raises(ValidationError, match="audit envelope exceeds"):
        AccountingReservation.model_validate(
            {**item.model_dump(), "audit_envelope": {"value": "x" * 70_000}}
        )


@pytest.mark.parametrize(
    "outcome,updates,error",
    [
        (AccountingOutcome.COMPLETED, {"exact_charge": None}, "exact charge"),
        (
            AccountingOutcome.NOT_DISPATCHED,
            {"spend_payload": {"unexpected": True}},
            "cannot contain provider spend",
        ),
        (AccountingOutcome.UNCERTAIN, {"uncertainty_reason": None}, "bounded reason"),
    ],
)
def test_finalization_outcome_shape_is_fail_closed(outcome, updates, error):
    item = finalization(reservation(), outcome=outcome)
    with pytest.raises(ValidationError, match=error):
        AccountingFinalization.model_validate({**item.model_dump(), **updates})


async def test_microbatch_coalesces_and_preserves_input_result_order():
    calls: list[list[int]] = []
    queue_depths: list[int] = []
    queue_waits: list[float] = []

    async def handler(values):
        calls.append(list(values))
        return [value * 10 for value in values]

    batcher = DurableMicrobatcher(
        handler,
        max_batch_size=4,
        max_pending=8,
        dwell_seconds=0.01,
        observe_queue_wait=queue_waits.append,
        set_queue_depth=queue_depths.append,
    )
    batcher.start()
    results = await asyncio.gather(*(batcher.submit(value) for value in range(4)))
    await batcher.close()
    assert results == [0, 10, 20, 30]
    assert calls == [[0, 1, 2, 3]]
    assert len(queue_waits) == 4
    assert all(wait >= 0 for wait in queue_waits)
    assert max(queue_depths) == 4
    assert queue_depths[-1] == 0


async def test_microbatch_cancellation_before_collection_does_not_retain_work():
    release = asyncio.Event()
    calls: list[list[int]] = []

    async def handler(values):
        calls.append(list(values))
        await release.wait()
        return list(values)

    batcher = DurableMicrobatcher(handler, max_batch_size=2, max_pending=2, dwell_seconds=0)
    batcher.start()
    first = asyncio.create_task(batcher.submit(1))
    await asyncio.sleep(0)
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    release.set()
    await batcher.close()
    assert calls in ([], [[1]])
    assert batcher.pending == 0
    with pytest.raises(DurableBatchClosed):
        await batcher.submit(2)


async def test_repository_reserve_batch_uses_one_atomic_grant_admission():
    items = [reservation(), reservation()]
    db = MagicMock()
    db.query_raw = AsyncMock(
        return_value=[
            {
                "operation_id": str(item.operation_id),
                "decision": "dispatch",
                "dispatch_token": str(item.owner_token),
                "accounting_partition": index,
            }
            for index, item in enumerate(items)
        ]
    )
    repository = AccountingProtocolRepository(db)
    results = await repository.reserve_batch(
        items, expires_at=asyncio.get_running_loop().time() + 1
    )
    db.query_raw.assert_awaited_once()
    assert "deltallm_accounting_admit_grant_batch" in db.query_raw.call_args.args[0]
    assert db.query_raw.call_args.args[2:5] == ("gateway", 32, 30)
    assert [result.operation_id for result in results] == [item.operation_id for item in items]
    assert all(result.decision is ReserveDecision.DISPATCH for result in results)


@pytest.mark.parametrize(
    "rows,ready", [([], False), ([{"ready": False}], False), ([{"ready": True}], True)]
)
async def test_protocol_readiness_requires_the_active_durable_generation(rows, ready):
    db = MagicMock(query_raw=AsyncMock(return_value=rows))
    assert await AccountingProtocolRepository(db).protocol_ready(7) is ready
    db.query_raw.assert_awaited_once()
    query, generation = db.query_raw.call_args.args
    assert generation == 7
    assert "writer_version=2 AND state='active'" in query


async def test_service_readiness_checks_its_database_owner():
    repository = MagicMock(protocol_ready=AsyncMock(return_value=False))
    service = AccountingProtocolService(repository, generation=7)
    assert await service.readiness_probe() is False
    repository.protocol_ready.assert_not_awaited()
    service.start()
    try:
        assert await service.readiness_probe() is False
        repository.protocol_ready.assert_awaited_once_with(7)
        repository.protocol_ready.return_value = True
        assert await service.readiness_probe() is True
    finally:
        await service.close()


async def test_repository_does_not_persist_denied_grant_admission():
    item = reservation()
    db = MagicMock()
    db.query_raw = AsyncMock(
        return_value=[
            {
                "operation_id": str(item.operation_id),
                "decision": "budget_exhausted",
            }
        ]
    )

    (result,) = await AccountingProtocolRepository(db).reserve_batch(
        [item], expires_at=asyncio.get_running_loop().time() + 1
    )

    db.query_raw.assert_awaited_once()
    assert "deltallm_accounting_admit_grant_batch" in db.query_raw.call_args.args[0]
    assert result.decision is ReserveDecision.BUDGET_EXHAUSTED
    assert result.dispatch_token is None


async def test_repository_recovers_ambiguous_reservation_before_provider_dispatch():
    item = reservation()
    db = MagicMock()
    db.query_raw = AsyncMock(
        side_effect=[
            TimeoutError(),
            [
                {
                    "operation_id": str(item.operation_id),
                    "owner_token": str(item.owner_token),
                    "request_fingerprint": item.request_fingerprint,
                    "accounting_protocol": "primary",
                    "accounting_generation": item.protocol_generation,
                    "accounting_partition": 3,
                    "accounting_state": "reserved",
                }
            ],
        ]
    )

    (result,) = await AccountingProtocolRepository(db).reserve_batch(
        [item], expires_at=asyncio.get_running_loop().time() + 1
    )

    assert result.decision is ReserveDecision.DISPATCH
    assert result.dispatch_token == item.owner_token
    assert result.accounting_partition == 3
    assert db.query_raw.await_count == 2
    assert "deltallm_billing_operations" in db.query_raw.await_args_list[1].args[0]


async def test_repository_reserves_a_recovery_window_after_primary_timeout():
    item = reservation()
    primary_started = asyncio.Event()

    async def query(query, *_parameters):
        if "deltallm_accounting_admit_grant_batch" in query:
            primary_started.set()
            await asyncio.sleep(1)
        assert primary_started.is_set()
        return [
            {
                "operation_id": str(item.operation_id),
                "owner_token": str(item.owner_token),
                "request_fingerprint": item.request_fingerprint,
                "accounting_protocol": "primary",
                "accounting_generation": item.protocol_generation,
                "accounting_partition": 3,
                "accounting_state": "reserved",
            }
        ]

    db = MagicMock()
    db.query_raw = AsyncMock(side_effect=query)
    loop = asyncio.get_running_loop()
    started = loop.time()
    (result,) = await AccountingProtocolRepository(
        db,
        statement_budget_seconds=0.05,
    ).reserve_batch([item], expires_at=started + 0.1)

    assert result.decision is ReserveDecision.DISPATCH
    assert db.query_raw.await_count == 2
    assert loop.time() - started < 0.15


async def test_repository_never_recovers_a_different_reservation_identity():
    item = reservation()
    db = MagicMock()
    db.query_raw = AsyncMock(
        side_effect=[
            TimeoutError(),
            [
                {
                    "operation_id": str(item.operation_id),
                    "owner_token": str(uuid4()),
                    "request_fingerprint": item.request_fingerprint,
                    "accounting_protocol": "primary",
                    "accounting_generation": item.protocol_generation,
                    "accounting_partition": 3,
                    "accounting_state": "reserved",
                }
            ],
        ]
    )

    with pytest.raises(AccountingProtocolUnavailable) as failure:
        await AccountingProtocolRepository(db).reserve_batch(
            [item], expires_at=asyncio.get_running_loop().time() + 1
        )

    assert failure.value.reason == AccountingResultFailure.INVALID_RESULT.value
    assert db.query_raw.await_count == 2


async def test_repository_rejects_mixed_generation_without_touching_database():
    db = MagicMock()
    db.query_raw = AsyncMock()
    repository = AccountingProtocolRepository(db)
    with pytest.raises(ValueError, match="mix protocol generations"):
        await repository.reserve_batch(
            [reservation(generation=1), reservation(generation=2)],
            expires_at=asyncio.get_running_loop().time() + 1,
        )
    db.query_raw.assert_not_awaited()


async def test_repository_timeout_is_classified_after_bounded_retries():
    db = MagicMock()
    db.query_raw = AsyncMock(side_effect=TimeoutError())
    before = metric_sample(
        "deltallm_accounting_database_call_seconds_count",
        operation="admit_grant",
        outcome="error",
    )
    with pytest.raises(AccountingProtocolUnavailable) as failure:
        await AccountingProtocolRepository(db).reserve_batch(
            [reservation()], expires_at=asyncio.get_running_loop().time() + 1
        )
    assert failure.value.reason == AcceptanceFailure.DEADLINE.value
    assert db.query_raw.await_count == 6
    assert (
        metric_sample(
            "deltallm_accounting_database_call_seconds_count",
            operation="admit_grant",
            outcome="error",
        )
        == before + 3
    )


async def test_repository_incomplete_result_has_bounded_reason():
    db = MagicMock()
    db.query_raw = AsyncMock(return_value=[])

    with pytest.raises(AccountingProtocolUnavailable) as failure:
        await AccountingProtocolRepository(db).reserve_batch(
            [reservation()], expires_at=asyncio.get_running_loop().time() + 1
        )

    assert failure.value.reason == AccountingResultFailure.INCOMPLETE_RESULT.value


async def test_repository_can_use_direct_reservations_for_bounded_rollback():
    item = reservation()
    db = MagicMock()
    db.query_raw = AsyncMock(
        return_value=[
            {
                "operation_id": str(item.operation_id),
                "decision": "dispatch",
                "dispatch_token": str(item.owner_token),
                "accounting_partition": 0,
            }
        ]
    )
    repository = AccountingProtocolRepository(db, grants_enabled=False)

    await repository.reserve_batch([item], expires_at=asyncio.get_running_loop().time() + 1)

    assert "deltallm_accounting_reserve_batch" in db.query_raw.call_args.args[0]


async def test_finalization_recovers_committed_result_after_lost_ack():
    reserved = reservation()
    nested_event_id = uuid4()
    nested_time = datetime.now(UTC)
    terminal = finalization(reserved).model_copy(
        update={
            "spend_payload": {
                "cost": Decimal("0.75"),
                "event_id": nested_event_id,
            },
            "audit_envelope": {"occurred_at": nested_time},
        }
    )
    serialized = terminal.model_dump(mode="json")
    db = MagicMock()
    db.query_raw = AsyncMock(
        side_effect=[
            TimeoutError(),
            [
                {
                    "operation_id": str(reserved.operation_id),
                    "owner_token": str(reserved.owner_token),
                    "request_fingerprint": reserved.request_fingerprint,
                    "accounting_protocol": "primary",
                    "accounting_generation": reserved.protocol_generation,
                    "sequence": 9,
                    "event_id": str(terminal.event_id),
                    "component_id": terminal.component_id,
                    "outcome": terminal.outcome.value,
                    "payload_json": {
                        "spend": serialized["spend_payload"],
                        "exact_charge": str(terminal.exact_charge),
                        "uncertainty_reason": terminal.uncertainty_reason,
                        "unresolved_attempts": terminal.unresolved_attempts,
                    },
                    "audit_envelope_json": serialized["audit_envelope"],
                }
            ],
        ]
    )

    (receipt,) = await AccountingProtocolRepository(
        db,
        statement_budget_seconds=0.05,
    ).finalize_batch(
        [terminal],
        expires_at=asyncio.get_running_loop().time() + 1,
    )

    assert receipt.replayed
    assert receipt.event_sequence == 9
    assert db.query_raw.await_count == 2
    assert "deltallm_accounting_finalize_grant_batch" in db.query_raw.await_args_list[0].args[0]
    assert "deltallm_accounting_events" in db.query_raw.await_args_list[1].args[0]


async def test_finalization_recovery_rejects_mismatched_durable_identity():
    reserved = reservation()
    terminal = finalization(reserved)
    db = MagicMock()
    db.query_raw = AsyncMock(
        side_effect=[
            TimeoutError(),
            [
                {
                    "operation_id": str(reserved.operation_id),
                    "owner_token": str(uuid4()),
                    "request_fingerprint": reserved.request_fingerprint,
                    "accounting_protocol": "primary",
                    "accounting_generation": reserved.protocol_generation,
                    "sequence": 9,
                    "event_id": str(terminal.event_id),
                    "component_id": terminal.component_id,
                    "outcome": terminal.outcome.value,
                    "payload_json": {
                        "spend": terminal.spend_payload,
                        "exact_charge": str(terminal.exact_charge),
                        "uncertainty_reason": terminal.uncertainty_reason,
                        "unresolved_attempts": terminal.unresolved_attempts,
                    },
                    "audit_envelope_json": terminal.audit_envelope,
                }
            ],
        ]
    )

    with pytest.raises(AccountingProtocolUnavailable) as failure:
        await AccountingProtocolRepository(db).finalize_batch(
            [terminal], expires_at=asyncio.get_running_loop().time() + 1
        )

    assert failure.value.reason == AccountingResultFailure.INVALID_RESULT.value
    assert db.query_raw.await_count == 2


async def test_projection_repository_restores_aware_database_timestamp():
    db = MagicMock()
    db.query_raw = AsyncMock(
        return_value=[
            {
                "sequence": 9,
                "event_id": str(uuid4()),
                "operation_id": str(uuid4()),
                "accounting_partition": 3,
                "outcome": "completed",
                "payload_json": {},
                "audit_envelope_json": {},
                "occurred_at": "2026-09-26T10:30:00.123Z",
            }
        ]
    )
    claim = AccountingProjectionClaim(
        projection_name="legacy-spend-audit-v1",
        protocol_generation=7,
        accounting_partition=3,
        worker_id="worker-1",
        lease_token=str(uuid4()),
        last_sequence=8,
    )

    (event,) = await AccountingProjectionRepository(db).read_batch(claim, limit=64)

    assert event.occurred_at == datetime(2026, 9, 26, 10, 30, 0, 123000, tzinfo=UTC)


async def test_accounting_audit_projection_uses_stable_uuid_ids():
    item = reservation()
    final_event_id = uuid4()
    envelope = _accounting_audit_envelope(
        SimpleNamespace(reservation=item),
        event_id=final_event_id,
        status="success",
        metadata={},
    )
    expected = uuid5(final_event_id, "accounting-audit:v1")
    assert UUID(envelope["event_id"]) == expected

    audit = MagicMock()
    audit.enqueue_bundle = AsyncMock(
        return_value=SimpleNamespace(statuses={str(expected): "accepted"})
    )
    projector = AccountingCompatibilityProjector(
        spend=MagicMock(),
        audit=audit,
        config=AccountingProjectionConfig(generation=7, worker_id="worker-1"),
    )
    event = AccountingProjectionEvent(
        sequence=9,
        event_id=str(final_event_id),
        operation_id=str(item.operation_id),
        accounting_partition=3,
        outcome="uncertain",
        payload={},
        audit_envelope={
            **envelope,
            "event_id": f"{final_event_id}:legacy-invalid",
        },
        occurred_at=datetime.now(UTC),
    )

    await projector.project(event)

    (projected,) = audit.enqueue_bundle.await_args.kwargs["envelopes"]
    assert UUID(projected.event_id) == expected


async def test_accounting_projection_batches_spend_and_audit_per_organization():
    item = reservation()
    spend = MagicMock()
    spend.log_batch_once = AsyncMock(return_value=(set(), {}))
    audit = MagicMock()
    audit.enqueue_bundle = AsyncMock(
        return_value=SimpleNamespace(statuses={"one": "accepted", "two": "duplicate"})
    )
    projector = AccountingCompatibilityProjector(
        spend=spend,
        audit=audit,
        config=AccountingProjectionConfig(generation=7, worker_id="worker-1"),
    )

    def event(sequence: int) -> AccountingProjectionEvent:
        event_id = uuid4()
        payload = {"request_id": str(item.operation_id)}
        return AccountingProjectionEvent(
            sequence=sequence,
            event_id=str(event_id),
            operation_id=str(item.operation_id),
            accounting_partition=sequence,
            outcome="completed",
            payload={"spend": payload},
            audit_envelope={
                "event_id": str(event_id),
                "record_type": "audit_event",
                "organization_id": "org-1",
                "payload": {"sequence": sequence},
                "redacted_payload": {"sequence": sequence},
            },
            occurred_at=datetime.now(UTC),
        )

    await projector.project_batch([event(1), event(2)])

    spend.log_batch_once.assert_awaited_once()
    assert len(spend.log_batch_once.await_args.args[0]) == 2
    audit.enqueue_bundle.assert_awaited_once()
    assert len(audit.enqueue_bundle.await_args.kwargs["envelopes"]) == 2


async def test_projection_repository_claims_multiple_partitions_in_one_call():
    db = MagicMock()
    db.query_raw = AsyncMock(
        return_value=[
            {"accounting_partition": 1, "last_sequence": 10},
            {"accounting_partition": 3, "last_sequence": 20},
        ]
    )

    claims = await AccountingProjectionRepository(db).claim_many(
        projection_name="legacy-spend-audit-v1",
        generation=7,
        worker_id="worker-1",
        lease_seconds=30,
        limit=4,
    )

    db.query_raw.assert_awaited_once()
    assert "EXISTS (SELECT 1 FROM deltallm_accounting_events" in db.query_raw.call_args.args[0]
    assert [claim.accounting_partition for claim in claims] == [1, 3]
    assert len({claim.lease_token for claim in claims}) == 1


async def test_accounting_service_readiness_tracks_both_microbatch_owners():
    repository = MagicMock()
    service = AccountingProtocolService(repository, generation=7)
    assert not service.worker_health.ready
    service.start()
    assert service.worker_health.ready
    service.finalizations.task.cancel()
    await asyncio.gather(service.finalizations.task, return_exceptions=True)
    assert not service.worker_health.ready
    await service.close()


async def test_accounting_service_gives_reservation_recovery_a_bounded_ack_envelope():
    item = reservation()
    permit = DispatchPermit(
        protocol_generation=item.protocol_generation,
        operation_id=item.operation_id,
        decision=ReserveDecision.DISPATCH,
        dispatch_token=item.owner_token,
        accounting_partition=3,
    )
    repository = MagicMock()
    repository.reserve_batch = AsyncMock(return_value=[permit])
    service = AccountingProtocolService(
        repository,
        generation=item.protocol_generation,
        dwell_seconds=0,
        statement_budget_seconds=0.2,
        reservation_ack_budget_seconds=0.8,
    )
    started = asyncio.get_running_loop().time()
    service.start()
    try:
        assert await service.reserve(item) == permit
    finally:
        await service.close()

    expires_at = repository.reserve_batch.await_args.kwargs["expires_at"]
    assert 0.7 <= expires_at - started <= 0.9


async def test_accounting_service_records_bounded_finalization_failure_reason():
    repository = MagicMock()
    repository.finalize_batch = AsyncMock(
        side_effect=AccountingProtocolUnavailable(AcceptanceFailure.POOL_TIMEOUT)
    )
    service = AccountingProtocolService(repository, generation=7, dwell_seconds=0)
    terminal = finalization(reservation())
    labels = {
        "queue": "finalization",
        "phase": "database",
        "reason": AcceptanceFailure.POOL_TIMEOUT.value,
    }
    before = metric_sample("deltallm_accounting_failures_total", **labels)
    service.start()
    try:
        with pytest.raises(AccountingProtocolUnavailable) as failure:
            await service.finalize(terminal)
        assert failure.value.reason == AcceptanceFailure.POOL_TIMEOUT.value
    finally:
        await service.close()

    assert metric_sample("deltallm_accounting_failures_total", **labels) == before + 1
