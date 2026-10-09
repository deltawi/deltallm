"""Local persistence has a fixed call bound and rejects different recovery proofs."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.billing.accounting.permits.accounting_local_leases import (
    LocalPermitFinalization,
    LocalPermitReceipt,
    LocalPermitReturn,
)
from src.db.accounting.accounting_calls import AccountingProtocolUnavailable
from src.db.accounting.permits.accounting_local_leases import AccountingLocalLeaseRepository
from src.db.accounting.permits.accounting_local_lease_results import (
    allocation_result,
    finalization_payload,
)
from tests.test_accounting_permits import allocation
from tests.test_accounting_protocol import finalization


def funding_row(item, *, shift=0):
    observed = datetime.now(UTC) + timedelta(seconds=shift)
    return {
        "allocation_fence_token": str(item.fence_token),
        "decision": "dispatch",
        "grant_id": str(uuid4()),
        "grantee_id": f"lease-test:{item.fence_token}",
        "fence_token": str(item.fence_token),
        "accounting_partition": 0,
        "allowance_exact": str(item.reservation.allowance),
        "operation_limit": 4,
        "expires_at": observed + timedelta(minutes=6),
        "dispatch_expires_at": observed + timedelta(seconds=30),
        "observed_at": observed,
    }


def grant(item, row=None):
    return allocation_result(
        item, row or funding_row(item), "lease-test", asyncio.get_running_loop().time()
    )


def terminal():
    item = allocation()
    receipt = LocalPermitReceipt(grant=grant(item), permit_ordinal=0, reservation=item.reservation)
    return LocalPermitFinalization(receipt=receipt, finalization=finalization(item.reservation))


def suffix():
    item = allocation()
    return LocalPermitReturn(grant=grant(item), first_unused_ordinal=1)


def returned_row(item):
    return {
        "grant_id": item.grant.grant_id,
        "fence_token": str(item.grant.fence_token),
        "first_unused_ordinal": item.first_unused_ordinal,
        "returned_operations": item.grant.operation_limit - item.first_unused_ordinal,
    }


def finalized_row(item):
    return {
        "operation_id": str(item.finalization.operation_id),
        "event_sequence": 1,
        "outcome": item.finalization.outcome.value,
        "replayed": False,
    }


def owner(db):
    return AccountingLocalLeaseRepository(db, owner_id="lease-test", statement_budget_seconds=0.05)


def deadline():
    return asyncio.get_running_loop().time() + 1


@pytest.mark.parametrize("phase", ["allocate", "return", "finalize"])
@pytest.mark.parametrize("count", [1, 8, 32, 256])
async def test_one_bulk_call_keeps_input_order_for_every_phase(phase, count):
    factory, response = {
        "allocate": (allocation, funding_row),
        "return": (suffix, returned_row),
        "finalize": (terminal, finalized_row),
    }[phase]
    items = [factory() for _ in range(count)]
    rows = [response(item) for item in items]
    db = MagicMock(query_raw=AsyncMock(return_value=list(reversed(rows))))
    results = await getattr(owner(db), f"{phase}_batch")(items, expires_at=deadline())
    db.query_raw.assert_awaited_once()
    assert len(results) == count
    if phase == "allocate":
        assert [result.fence_token for result in results] == [item.fence_token for item in items]
    elif phase == "return":
        assert results == [3] * count
    else:
        assert [result.operation_id for result in results] == [
            item.finalization.operation_id for item in items
        ]


@pytest.mark.parametrize("phase", ["allocate", "return", "finalize"])
async def test_empty_phase_never_calls_the_database(phase):
    db = MagicMock(query_raw=AsyncMock())
    assert await getattr(owner(db), f"{phase}_batch")([], expires_at=deadline()) == []
    db.query_raw.assert_not_awaited()


@pytest.mark.parametrize("phase", ["allocate", "return", "finalize"])
@pytest.mark.parametrize("failure", [TimeoutError, asyncio.CancelledError])
async def test_timeout_has_six_calls_and_cancellation_has_one(phase, failure):
    factory = {"allocate": allocation, "return": suffix, "finalize": terminal}[phase]
    db = MagicMock(query_raw=AsyncMock(side_effect=failure()))
    error = (
        asyncio.CancelledError
        if failure is asyncio.CancelledError
        else AccountingProtocolUnavailable
    )
    with pytest.raises(error):
        await getattr(owner(db), f"{phase}_batch")([factory()], expires_at=deadline())
    assert db.query_raw.await_count == (1 if failure is asyncio.CancelledError else 6)


@pytest.mark.parametrize("shift", [-3600, 0, 3600])
async def test_monotonic_deadlines_do_not_depend_on_host_database_clock_alignment(shift):
    item = allocation()
    row = funding_row(item, shift=shift)
    db = MagicMock(query_raw=AsyncMock(return_value=[row]))
    before = asyncio.get_running_loop().time()
    funded = (await owner(db).allocate_batch([item], expires_at=deadline()))[0]
    after = asyncio.get_running_loop().time()
    assert before + 30 <= funded.dispatch_deadline <= after + 30
    assert before + 360 <= funded.recovery_deadline <= after + 360


@pytest.mark.parametrize("shift", [-3600, 0, 3600])
@pytest.mark.parametrize("ack", ["normal", "recovered"])
async def test_expired_dispatch_proof_is_return_only_without_extending_its_horizon(shift, ack):
    item = allocation()
    row = funding_row(item, shift=shift)
    row["observed_at"] += timedelta(seconds=31)
    if ack == "recovered":
        proof = grant(item, row)
        row = {**row, **database_grant(proof), "subject_matches": True}
    db = MagicMock(
        query_raw=AsyncMock(side_effect=[TimeoutError(), [row]] if ack == "recovered" else [[row]])
    )
    before = asyncio.get_running_loop().time()
    funded = (await owner(db).allocate_batch([item], expires_at=deadline()))[0]
    assert funded.dispatch_expires_at == row["dispatch_expires_at"]
    assert funded.dispatch_deadline < before
    assert funded.recovery_deadline > before + 300
    assert LocalPermitReturn(grant=funded, first_unused_ordinal=0).first_unused_ordinal == 0
    assert db.query_raw.await_count == (2 if ack == "recovered" else 1)


@pytest.mark.parametrize("invalid", ["recovery", "dispatch"])
async def test_expired_recovery_or_extended_dispatch_ack_cannot_enter_the_local_owner(invalid):
    item = allocation()
    row = funding_row(item)
    if invalid == "recovery":
        row["observed_at"] = row["expires_at"]
    else:
        row["dispatch_expires_at"] = row["observed_at"] + timedelta(seconds=301)
    db = MagicMock(query_raw=AsyncMock(return_value=[row]))
    with pytest.raises(AccountingProtocolUnavailable):
        await owner(db).allocate_batch([item], expires_at=deadline())
    db.query_raw.assert_awaited_once()


@pytest.mark.parametrize(
    "field,value",
    [
        ("dispatch_expires_at", None),
        ("grantee_id", "wrong"),
        ("operation_limit", 5),
        ("allowance_exact", "9"),
        ("observed_at", None),
    ],
)
async def test_invalid_funding_ack_never_retries_or_dispatches(field, value):
    item = allocation()
    db = MagicMock(query_raw=AsyncMock(return_value=[{**funding_row(item), field: value}]))
    with pytest.raises(AccountingProtocolUnavailable):
        await owner(db).allocate_batch([item], expires_at=deadline())
    db.query_raw.assert_awaited_once()


@pytest.mark.parametrize("phase", ["allocate", "return", "finalize"])
async def test_invalid_duplicate_or_oversized_batch_fails_before_sql(phase):
    factory = {"allocate": allocation, "return": suffix, "finalize": terminal}[phase]
    item = factory()
    db = MagicMock(query_raw=AsyncMock())
    for items in ([item, item], [factory() for _ in range(257)]):
        with pytest.raises(ValueError):
            await getattr(owner(db), f"{phase}_batch")(items, expires_at=deadline())
    db.query_raw.assert_not_awaited()


async def test_issue_receipt_and_finalization_contracts_reject_wrong_identity_or_lifetime():
    item = terminal()
    receipt = item.receipt
    for updates in (
        {"owner_token": uuid4()},
        {"operation_id": uuid4()},
        {"request_fingerprint": "0" * 64},
        {"protocol_generation": 8},
    ):
        with pytest.raises(ValidationError, match="issue receipt"):
            LocalPermitFinalization(
                receipt=receipt, finalization=item.finalization.model_copy(update=updates)
            )
    with pytest.raises(ValidationError, match="recovery lifetime"):
        LocalPermitReceipt(
            grant=receipt.grant,
            permit_ordinal=0,
            reservation=receipt.reservation.model_copy(
                update={"expires_at": receipt.grant.expires_at + timedelta(seconds=1)}
            ),
        )
    with pytest.raises(ValidationError, match="grant capacity"):
        LocalPermitReturn(grant=receipt.grant, first_unused_ordinal=5)


async def test_terminal_payload_contains_the_complete_immutable_issue_and_finalization():
    item = terminal()
    payload = finalization_payload(item)
    assert payload["reservation"] == item.receipt.reservation.model_dump(mode="json")
    assert payload["finalization"] == item.finalization.model_dump(mode="json")
    assert payload["permit_ordinal"] == 0


def database_grant(grant):
    return {
        "grant_id": grant.grant_id,
        "protocol_name": "primary",
        "generation": grant.protocol_generation,
        "grantee_id": grant.grantee_id,
        "fence_token": str(grant.fence_token),
        "accounting_partition": grant.accounting_partition,
        "operation_limit": grant.operation_limit,
        "unit_allowance_exact": str(grant.allowance),
        "expires_at": grant.expires_at,
        "dispatch_expires_at": grant.dispatch_expires_at,
        "dispatch_mode": "preissued",
        "local_dispatch": True,
        "state": "active",
        "consumed_operations": 0,
        "returned_operations": 0,
        "returned_exact": "0",
    }


def recovery_case(phase):
    if phase == "allocate":
        item = allocation()
        row = funding_row(item)
        proof = grant(item, row)
        return item, {**row, **database_grant(proof), "subject_matches": True}
    if phase == "return":
        item = suffix()
        return item, {
            **database_grant(item.grant),
            "returned_operations": 3,
            "returned_exact": str(item.grant.allowance * 3),
        }
    item = terminal()
    receipt = item.receipt
    intent = item.finalization
    return item, {
        "operation_id": str(intent.operation_id),
        "owner_token": str(intent.owner_token),
        "request_fingerprint": intent.request_fingerprint,
        "snapshot": receipt.reservation.model_dump(mode="json"),
        "accounting_protocol": "primary",
        "accounting_generation": intent.protocol_generation,
        "accounting_partition": receipt.grant.accounting_partition,
        "accounting_state": "finalized",
        "accounting_grant_id": receipt.grant.grant_id,
        "accounting_permit_ordinal": 0,
        "accounting_grant_fence_token": str(receipt.grant.fence_token),
        "sequence": 1,
        "event_id": str(intent.event_id),
        "component_id": intent.component_id,
        "event_protocol": "primary",
        "event_generation": intent.protocol_generation,
        "event_operation_id": str(intent.operation_id),
        "event_type": "finalized",
        "outcome": intent.outcome.value,
        "payload_json": {
            "spend": intent.spend_payload,
            "exact_charge": str(intent.exact_charge),
            "uncertainty_reason": None,
            "unresolved_attempts": 0,
        },
        "audit_envelope_json": intent.audit_envelope,
        "occurred_at": intent.occurred_at,
    }


@pytest.mark.parametrize("phase", ["allocate", "return", "finalize"])
async def test_complete_lost_ack_recovers_in_one_indexed_query(phase):
    item, row = recovery_case(phase)
    db = MagicMock(query_raw=AsyncMock(side_effect=[TimeoutError(), [row]]))
    results = await getattr(owner(db), f"{phase}_batch")([item], expires_at=deadline())
    assert len(results) == 1
    assert db.query_raw.await_count == 2
    assert "CROSS JOIN LATERAL" in db.query_raw.await_args.args[0]
    assert "OFFSET 0" in db.query_raw.await_args.args[0]
    if phase == "finalize":
        assert results[0].replayed is True


@pytest.mark.parametrize(
    "phase,field,value",
    [
        ("allocate", "local_dispatch", False),
        ("allocate", "subject_matches", False),
        ("allocate", "consumed_operations", 1),
        ("allocate", "generation", 8),
        ("allocate", "grantee_id", "wrong"),
        ("allocate", "fence_token", str(uuid4())),
        ("return", "returned_operations", 2),
        ("return", "returned_exact", "9"),
        ("return", "local_dispatch", False),
        ("return", "generation", 8),
        ("return", "grantee_id", "wrong"),
        ("return", "fence_token", str(uuid4())),
        ("finalize", "owner_token", str(uuid4())),
        ("finalize", "snapshot", {}),
        ("finalize", "accounting_permit_ordinal", 1),
        ("finalize", "event_operation_id", str(uuid4())),
        ("finalize", "event_generation", 8),
        ("finalize", "event_type", "reserved"),
        ("finalize", "event_id", str(uuid4())),
        ("finalize", "audit_envelope_json", {}),
        ("finalize", "payload_json", {"spend": {}, "exact_charge": "9"}),
        ("finalize", "occurred_at", datetime.now(UTC) - timedelta(hours=1)),
    ],
)
async def test_recovery_rejects_every_changed_financial_identity_without_retry(phase, field, value):
    item, row = recovery_case(phase)
    db = MagicMock(query_raw=AsyncMock(side_effect=[TimeoutError(), [{**row, field: value}]]))
    with pytest.raises(AccountingProtocolUnavailable):
        await getattr(owner(db), f"{phase}_batch")([item], expires_at=deadline())
    assert db.query_raw.await_count == 2


@pytest.mark.parametrize("phase", ["allocate", "return", "finalize"])
async def test_expired_phase_never_acquires_a_connection(phase):
    factory = {"allocate": allocation, "return": suffix, "finalize": terminal}[phase]
    db = MagicMock(query_raw=AsyncMock())
    with pytest.raises(AccountingProtocolUnavailable):
        await getattr(owner(db), f"{phase}_batch")(
            [factory()], expires_at=asyncio.get_running_loop().time() - 1
        )
    db.query_raw.assert_not_awaited()


@pytest.mark.parametrize("phase", ["allocate", "return", "finalize"])
async def test_mixed_generation_fails_before_sql(phase):
    factory = {"allocate": allocation, "return": suffix, "finalize": terminal}[phase]
    first, second = factory(), factory()
    if phase == "allocate":
        second = second.model_copy(
            update={"reservation": second.reservation.model_copy(update={"protocol_generation": 8})}
        )
    elif phase == "return":
        second = second.model_copy(
            update={"grant": second.grant.model_copy(update={"protocol_generation": 8})}
        )
    else:
        second = second.model_copy(
            update={
                "finalization": second.finalization.model_copy(update={"protocol_generation": 8})
            }
        )
    db = MagicMock(query_raw=AsyncMock())
    with pytest.raises(ValueError, match="generations"):
        await getattr(owner(db), f"{phase}_batch")([first, second], expires_at=deadline())
    db.query_raw.assert_not_awaited()


@pytest.mark.parametrize("phase", ["allocate", "finalize"])
async def test_serialized_byte_limit_rejects_large_valid_batches_before_sql(phase):
    entries = []
    for _ in range(20):
        item = allocation()
        item = item.model_copy(
            update={
                "reservation": item.reservation.model_copy(
                    update={"audit_envelope": {"data": "x" * 60_000}}
                )
            }
        )
        if phase == "finalize":
            receipt = LocalPermitReceipt(
                grant=grant(item), permit_ordinal=0, reservation=item.reservation
            )
            item = LocalPermitFinalization(
                receipt=receipt, finalization=finalization(item.reservation)
            )
        entries.append(item)
    db = MagicMock(query_raw=AsyncMock())
    with pytest.raises(ValueError, match="serialized size"):
        await getattr(owner(db), f"{phase}_batch")(entries, expires_at=deadline())
    db.query_raw.assert_not_awaited()
