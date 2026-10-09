"""Permit batches keep one call per phase and exact recovery identities."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.billing.accounting.accounting_protocol import (
    PreissuedPermitAllocation,
    PreissuedPermitClaim,
    PreissuedPermitGrant,
    ReserveDecision,
)
from src.db.accounting_calls import AccountingProtocolUnavailable
from tests.accounting_adapters.permit_repository import AccountingPermitRepository
from tests.test_accounting_protocol import reservation


def allocation():
    return PreissuedPermitAllocation(
        reservation=reservation(), fence_token=uuid4(), target_operations=4
    )


def allocation_row(item, *, owner="permit-test"):
    return {
        "allocation_fence_token": str(item.fence_token),
        "decision": "dispatch",
        "grant_id": str(uuid4()),
        "grantee_id": f"{owner}:{item.fence_token}",
        "fence_token": str(item.fence_token),
        "accounting_partition": 0,
        "allowance_exact": str(item.reservation.allowance),
        "operation_limit": 4,
        "expires_at": datetime.now(UTC) + timedelta(seconds=30),
    }


def grant(item):
    row = allocation_row(item)
    return PreissuedPermitGrant(
        protocol_generation=item.reservation.protocol_generation,
        grant_id=row["grant_id"],
        grantee_id=row["grantee_id"],
        fence_token=item.fence_token,
        accounting_partition=0,
        allowance=item.reservation.allowance,
        operation_limit=4,
        expires_at=row["expires_at"],
    )


def claim():
    item = allocation()
    return PreissuedPermitClaim(grant=grant(item), permit_ordinal=0, reservation=item.reservation)


def claim_row(item, *, replay=False):
    return {
        "operation_id": str(item.reservation.operation_id),
        "decision": "replay" if replay else "dispatch",
        "dispatch_token": None if replay else str(item.reservation.owner_token),
        "accounting_partition": None if replay else item.grant.accounting_partition,
    }


def recovered_allocation_row(item):
    return {
        **allocation_row(item),
        "generation": item.reservation.protocol_generation,
        "state": "active",
        "dispatch_mode": "preissued",
        "subject_matches": True,
    }


def recovered_claim_row(item):
    reservation = item.reservation
    return {
        "operation_id": str(reservation.operation_id),
        "owner_token": str(reservation.owner_token),
        "request_fingerprint": reservation.request_fingerprint,
        "snapshot": reservation.model_dump(mode="json"),
        "accounting_protocol": "primary",
        "accounting_generation": reservation.protocol_generation,
        "accounting_state": "reserved",
        "accounting_partition": item.grant.accounting_partition,
        "accounting_grant_id": item.grant.grant_id,
        "accounting_permit_ordinal": item.permit_ordinal,
        "accounting_grant_fence_token": str(item.grant.fence_token),
    }


def repository(db):
    return AccountingPermitRepository(db, owner_id="permit-test", statement_budget_seconds=0.05)


def deadline():
    return asyncio.get_running_loop().time() + 1


@pytest.mark.parametrize("count", [1, 8, 32, 256])
async def test_refill_across_subjects_uses_one_call_and_keeps_input_order(count):
    items = [allocation() for _ in range(count)]
    rows = [allocation_row(item) for item in items]
    db = MagicMock(query_raw=AsyncMock(return_value=list(reversed(rows))))
    results = await repository(db).allocate_batch(items, expires_at=deadline())
    assert [item.fence_token for item in results] == [item.fence_token for item in items]
    db.query_raw.assert_awaited_once()
    query, generation, owner, ttl, payload = db.query_raw.await_args.args
    assert "allocate_permit_grants_batch" in query
    assert (generation, owner, ttl) == (7, "permit-test", 30)
    assert json.loads(payload) == [item.model_dump(mode="json") for item in items]


@pytest.mark.parametrize("count", [1, 8, 32, 256])
async def test_claim_across_grants_uses_one_call_and_keeps_input_order(count):
    items = [claim() for _ in range(count)]
    db = MagicMock(query_raw=AsyncMock(return_value=[claim_row(item) for item in reversed(items)]))
    results = await repository(db).claim_batch(items, expires_at=deadline())
    assert [item.operation_id for item in results] == [
        item.reservation.operation_id for item in items
    ]
    assert all(item.decision is ReserveDecision.DISPATCH for item in results)
    db.query_raw.assert_awaited_once()
    assert "claim_permits_batch" in db.query_raw.await_args.args[0]


async def test_empty_batches_do_not_call_the_database():
    db = MagicMock(query_raw=AsyncMock())
    owner = repository(db)
    assert await owner.allocate_batch([], expires_at=deadline()) == []
    assert await owner.claim_batch([], expires_at=deadline()) == []
    db.query_raw.assert_not_awaited()


@pytest.mark.parametrize("kind", ["duplicate", "size", "generation", "bytes"])
async def test_invalid_refill_batch_fails_before_sql(kind):
    item = allocation()
    items = [item, item]
    if kind == "size":
        items = [allocation() for _ in range(257)]
    elif kind == "generation":
        second = allocation()
        items = [
            item,
            second.model_copy(
                update={
                    "reservation": second.reservation.model_copy(update={"protocol_generation": 8})
                }
            ),
        ]
    elif kind == "bytes":
        items = [allocation() for _ in range(20)]
        items = [
            entry.model_copy(
                update={
                    "reservation": entry.reservation.model_copy(
                        update={"audit_envelope": {"payload": "x" * 60_000}}
                    )
                }
            )
            for entry in items
        ]
    db = MagicMock(query_raw=AsyncMock())
    with pytest.raises(ValueError):
        await repository(db).allocate_batch(items, expires_at=deadline())
    db.query_raw.assert_not_awaited()


async def test_duplicate_grant_ordinal_fails_before_sql():
    first = claim()
    other = first.model_copy(update={"reservation": reservation()})
    db = MagicMock(query_raw=AsyncMock())
    with pytest.raises(ValueError, match="grant ordinal"):
        await repository(db).claim_batch([first, other], expires_at=deadline())
    db.query_raw.assert_not_awaited()


@pytest.mark.parametrize(
    "field,value",
    [
        ("grantee_id", "another-owner"),
        ("fence_token", str(uuid4())),
        ("operation_limit", 5),
        ("allowance_exact", "9"),
        ("accounting_partition", 64),
        ("expires_at", datetime.now(UTC) - timedelta(seconds=1)),
    ],
)
async def test_invalid_grant_result_cannot_dispatch_or_retry(field, value):
    item = allocation()
    db = MagicMock(query_raw=AsyncMock(return_value=[{**allocation_row(item), field: value}]))
    with pytest.raises(AccountingProtocolUnavailable, match="protocol unavailable"):
        await repository(db).allocate_batch([item], expires_at=deadline())
    db.query_raw.assert_awaited_once()


@pytest.mark.parametrize("decision", ["budget_exhausted", "capacity_exhausted"])
async def test_refill_denial_has_no_grant_or_fallback(decision):
    item = allocation()
    row = {key: None for key in allocation_row(item)}
    row.update(allocation_fence_token=str(item.fence_token), decision=decision)
    db = MagicMock(query_raw=AsyncMock(return_value=[row]))
    assert await repository(db).allocate_batch([item], expires_at=deadline()) == [
        ReserveDecision(decision)
    ]
    db.query_raw.assert_awaited_once()


async def test_exact_refill_recovers_after_lost_ack_with_one_query():
    item = allocation()
    row = recovered_allocation_row(item)
    db = MagicMock(query_raw=AsyncMock(side_effect=[TimeoutError(), [row]]))
    result = await repository(db).allocate_batch([item], expires_at=deadline())
    assert result[0].fence_token == item.fence_token
    assert db.query_raw.await_count == 2
    assert "subject_key=deltallm_accounting_grant_subject" in db.query_raw.await_args.args[0]


@pytest.mark.parametrize(
    "field,value",
    [
        ("subject_matches", False),
        ("generation", 8),
        ("grantee_id", "another-owner"),
        ("dispatch_mode", "assigned"),
        ("state", "closed"),
    ],
)
async def test_refill_recovery_rejects_a_different_contract(field, value):
    item = allocation()
    row = {**recovered_allocation_row(item), field: value}
    db = MagicMock(query_raw=AsyncMock(side_effect=[TimeoutError(), [row]]))
    with pytest.raises(AccountingProtocolUnavailable):
        await repository(db).allocate_batch([item], expires_at=deadline())
    assert db.query_raw.await_count == 2


async def test_claim_ack_loss_recovers_only_the_exact_owned_claim():
    item = claim()
    db = MagicMock(query_raw=AsyncMock(side_effect=[TimeoutError(), [recovered_claim_row(item)]]))
    result = await repository(db).claim_batch([item], expires_at=deadline())
    assert result[0].dispatch_token == item.reservation.owner_token
    assert db.query_raw.await_count == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("owner_token", str(uuid4())),
        ("accounting_grant_id", "other"),
        ("accounting_permit_ordinal", 1),
        ("accounting_grant_fence_token", str(uuid4())),
        ("accounting_partition", 1),
        ("snapshot", {}),
        ("request_fingerprint", "0" * 64),
        ("accounting_state", "invalid"),
    ],
)
async def test_claim_recovery_never_promotes_a_different_identity(field, value):
    item = claim()
    row = {**recovered_claim_row(item), field: value}
    db = MagicMock(query_raw=AsyncMock(side_effect=[TimeoutError(), [row]]))
    with pytest.raises(AccountingProtocolUnavailable):
        await repository(db).claim_batch([item], expires_at=deadline())
    assert db.query_raw.await_count == 2


async def test_terminal_claim_recovery_never_grants_dispatch():
    item = claim()
    row = {**recovered_claim_row(item), "accounting_state": "finalized"}
    db = MagicMock(query_raw=AsyncMock(side_effect=[TimeoutError(), [row]]))
    result = await repository(db).claim_batch([item], expires_at=deadline())
    assert result[0].decision is ReserveDecision.REPLAY
    assert result[0].dispatch_token is None


async def test_repeated_timeout_has_a_fixed_attempt_and_recovery_bound():
    db = MagicMock(query_raw=AsyncMock(side_effect=TimeoutError()))
    with pytest.raises(AccountingProtocolUnavailable):
        await repository(db).claim_batch([claim()], expires_at=deadline())
    assert db.query_raw.await_count == 6


async def test_cancellation_is_propagated_without_recovery_or_retry():
    db = MagicMock(query_raw=AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await repository(db).claim_batch([claim()], expires_at=deadline())
    db.query_raw.assert_awaited_once()


async def test_expired_caller_never_acquires_a_database_connection():
    db = MagicMock(query_raw=AsyncMock())
    with pytest.raises(AccountingProtocolUnavailable):
        await repository(db).claim_batch([claim()], expires_at=asyncio.get_running_loop().time())
    db.query_raw.assert_not_awaited()


@pytest.mark.parametrize("change", ["generation", "allowance", "ordinal"])
def test_typed_claim_rejects_an_invalid_capacity_contract(change):
    item = claim()
    fields = item.model_dump()
    if change == "generation":
        fields["reservation"]["protocol_generation"] = 8
    elif change == "allowance":
        fields["reservation"]["allowance"] = Decimal("9")
    else:
        fields["permit_ordinal"] = 4
    with pytest.raises(ValidationError):
        PreissuedPermitClaim.model_validate(fields)
