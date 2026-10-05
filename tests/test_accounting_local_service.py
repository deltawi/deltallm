"""Shared local accounting keeps proof identity and bounded byte queues."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.billing.accounting_local_cursors import LocalCursorStore
from src.billing.accounting_local_issuer import LocalPermitIssuer
from src.billing.accounting_local_leases import LocalAccountingHandle, LocalDispatchPermit
from src.billing.accounting_local_receipts import LocalReceiptStore
from src.billing.accounting_local_service import LocalAccountingService
from src.billing.accounting_local_terminal import LocalTerminalOwner
from src.billing.accounting_protocol import (
    AccountingOperationHandle,
    DispatchPermit,
    ReserveDecision,
)
from src.billing.accounting_service import AccountingProtocolService
from src.billing.durable_microbatch import DurableBatchClosed, DurableBatchFull
from src.billing.provider_allowance import ProviderRequestBounds
from src.billing.spend_ingestion import SpendIngestionConfig, SpendIngestionService
from src.billing.spend_operations import SpendPersistenceUnavailable
from src.cache.middleware import CacheMiddleware
from src.telemetry.spend_operation import (
    admit_accounting_reservation,
    durable_provider_call,
    operation_handle,
)
from tests.test_accounting_local_handles import values as handle_values
from tests.test_accounting_local_issuer import Funding, items
from tests.test_accounting_local_leases import funding_row, grant
from tests.test_accounting_local_terminal import Persistence
from tests.test_accounting_protocol import finalization
from tests.test_accounting_request_path import (
    _AccountingRepository,
    _deployment,
    _priced_cache_entry,
    _request,
)


class FundingOwner(Funding):
    async def allocate_batch(self, allocations, *, expires_at):
        self.calls.append(tuple(allocations))
        results = []
        for allocation in allocations:
            row = funding_row(allocation)
            row["operation_limit"] = allocation.target_operations
            row["expires_at"] = row["observed_at"] + timedelta(minutes=16)
            results.append(grant(allocation, row))
        return results


def state(**settings):
    funding, persistence = FundingOwner(), Persistence()
    cursors = LocalCursorStore(generation=7, max_entries=1024, max_retained_bytes=8 * 1024 * 1024)
    receipts = LocalReceiptStore(max_entries=1024, max_retained_bytes=8 * 1024 * 1024)
    issuer = LocalPermitIssuer(funding, cursors, receipts, target_operations=32)
    terminal = LocalTerminalOwner(persistence, receipts, generation=7)
    service = LocalAccountingService(
        _AccountingRepository(), generation=7, issuer=issuer, terminal=terminal, **settings
    )
    return funding, persistence, cursors, receipts, issuer, service


def handle(permit):
    _, _, fields = handle_values()
    return LocalAccountingHandle(
        reservation=permit.proof.reservation,
        proof=permit.proof,
        dispatch_token=permit.dispatch_token,
        accounting_partition=permit.accounting_partition,
        attempts=fields["attempts"],
    )


def ingestion(service):
    return SpendIngestionService(
        db_client=None, writer=MagicMock(), config=SpendIngestionConfig(), accounting=service
    )


async def test_shared_queues_batch_terminals_and_issue_warm_requests_without_database_calls():
    funding, persistence, _, receipts, _, service = state()
    service.start()
    try:
        permits = await asyncio.gather(*(service.reserve(item) for item in items(64)))
        assert all(isinstance(permit, LocalDispatchPermit) for permit in permits)
        assert len(funding.calls) == 2 and receipts.entries == 64
        operations = [handle(permit) for permit in permits]
        results = await asyncio.gather(
            *(
                service.finalize_operation(operation, finalization(operation.reservation))
                for operation in operations
            )
        )
        assert len(results) == 64 and len(persistence.calls) == 8
        assert (
            receipts.entries == receipts.retained_bytes == service.finalizations.retained_bytes == 0
        )
    finally:
        await service.close()


async def test_provider_retry_keeps_one_local_issue_and_terminal_contains_its_proof():
    funding, persistence, _, receipts, _, service = state(dwell_seconds=0)
    spend = ingestion(service)
    request = _request(service, spend)
    service.start()
    try:
        with pytest.raises(RuntimeError, match="upstream failed"):
            await durable_provider_call(
                request,
                model="gpt-test",
                call_type="completion",
                deployment=_deployment(),
                bounds=ProviderRequestBounds(max_output_tokens=10),
                execute=AsyncMock(side_effect=RuntimeError("upstream failed")),
            )
        first = operation_handle(request)
        await durable_provider_call(
            request,
            model="gpt-test",
            call_type="completion",
            deployment=_deployment(),
            bounds=ProviderRequestBounds(max_output_tokens=10),
            execute=AsyncMock(return_value="ok"),
        )
        reused = operation_handle(request)
        assert isinstance(reused, LocalAccountingHandle)
        assert reused.proof == first.proof and len(reused.attempts) == 2
        assert len(funding.calls) == receipts.entries == 1
        await spend._finalize_accounting_spend(
            reused, event_id=str(reused.reservation.operation_id), payload={"cost_exact": "0.3"}
        )
        written = persistence.calls[0][0]
        assert written.receipt == first.proof and written.finalization.unresolved_attempts == 1
        assert receipts.entries == receipts.retained_bytes == 0
        spend.writer.log_spend.assert_not_called()
    finally:
        await service.close()


async def test_paid_cache_keeps_local_proof_and_uses_the_same_terminal_owner():
    funding, persistence, _, receipts, _, service = state(dwell_seconds=0)
    spend = ingestion(service)
    request = _request(service, spend)
    request.json = AsyncMock(side_effect=AssertionError("must not read the body"))
    service.start()
    try:
        await CacheMiddleware(AsyncMock())._record_cache_hit_accounting(
            request, "/v1/chat/completions", "gpt-test", "cache:test", _priced_cache_entry()
        )
        operation = operation_handle(request)
        written = persistence.calls[0][0]
        assert isinstance(operation, LocalAccountingHandle)
        assert written.receipt == operation.proof
        assert written.finalization.spend_payload["cache_hit"] is True
        assert (
            written.finalization.audit_envelope["payload"]["event"]["metadata"]["cache_hit"] is True
        )
        assert len(funding.calls) == len(persistence.calls) == 1
        assert receipts.entries == receipts.retained_bytes == 0
        spend.writer.log_spend.assert_not_called()
        request.json.assert_not_awaited()
    finally:
        await service.close()


async def test_missing_local_dispatch_proof_fails_closed_before_provider_work():
    _, _, _, _, _, service = state()
    reservation = items(1)[0]
    permit = DispatchPermit(
        protocol_generation=7,
        operation_id=reservation.operation_id,
        decision=ReserveDecision.DISPATCH,
        dispatch_token=reservation.owner_token,
        accounting_partition=0,
    )
    service.reserve = AsyncMock(return_value=permit)
    _, _, fields = handle_values()
    with pytest.raises(SpendPersistenceUnavailable):
        await admit_accounting_reservation(
            service, reservation=reservation, attempt=fields["attempts"][0]
        )


@pytest.mark.parametrize("direction", ["assigned_to_local", "local_to_assigned", "no_proof"])
async def test_terminal_cannot_switch_accounting_owners(direction):
    _, persistence, _, _, _, service = state()
    proof, _, fields = handle_values()
    operation = LocalAccountingHandle(**fields)
    record = finalization(proof.reservation)
    with pytest.raises(ValueError):
        if direction == "assigned_to_local":
            assigned = AccountingOperationHandle.model_validate(
                operation.model_dump(exclude={"proof"})
            )
            await service.finalize_operation(assigned, record)
        elif direction == "local_to_assigned":
            await AccountingProtocolService(
                _AccountingRepository(), generation=7
            ).finalize_operation(operation, record)
        else:
            await service.finalize(record)
    assert persistence.calls == [] and service.finalizations.retained_bytes == 0


@pytest.mark.parametrize("failure", ["generation", "store"])
async def test_service_owners_must_share_generation_and_receipt_store(failure):
    _, persistence, _, receipts, issuer, _ = state()
    if failure == "store":
        receipts = LocalReceiptStore(max_entries=1, max_retained_bytes=100000)
    terminal = LocalTerminalOwner(
        persistence, receipts, generation=8 if failure == "generation" else 7
    )
    with pytest.raises(ValueError, match="must share"):
        LocalAccountingService(
            _AccountingRepository(), generation=7, issuer=issuer, terminal=terminal
        )


async def test_large_local_terminals_split_inside_the_complete_wire_byte_bound():
    _, persistence, _, receipts, _, service = state(dwell_seconds=0.01)
    service.start()
    try:
        permits = await asyncio.gather(*(service.reserve(item) for item in items(8)))
        operations = [handle(permit) for permit in permits]
        records = [finalization(operation.reservation) for operation in operations]
        for record in records:
            record.spend_payload["data"] = "s" * 200000
            record.audit_envelope["data"] = "a" * 64000
        await asyncio.gather(
            *(
                service.finalize_operation(operation, record)
                for operation, record in zip(operations, records, strict=True)
            )
        )
        assert [len(batch) for batch in persistence.calls] == [3, 3, 2]
        assert (
            receipts.entries == receipts.retained_bytes == service.finalizations.retained_bytes == 0
        )
    finally:
        await service.close()


async def test_full_terminal_byte_queue_keeps_issued_proof_and_permits_an_exact_retry():
    _, persistence, _, receipts, _, service = state(
        dwell_seconds=0, max_finalization_retained_bytes=8000
    )
    service.start()
    try:
        permit = await service.reserve(items(1)[0])
        operation = handle(permit)
        record = finalization(operation.reservation)
        record.spend_payload["large"] = "x" * 10000
        with pytest.raises(DurableBatchFull):
            await service.finalize_operation(operation, record)
        assert receipts.entries == 1 and persistence.calls == []
        record.spend_payload.pop("large")
        await service.finalize_operation(operation, record)
        assert receipts.entries == receipts.retained_bytes == 0
    finally:
        await service.close()


async def test_close_rejects_new_local_issue():
    funding, _, _, _, _, service = state()
    service.start()
    await service.close()
    with pytest.raises(DurableBatchClosed):
        await service.reserve(items(1)[0])
    assert funding.calls == []
