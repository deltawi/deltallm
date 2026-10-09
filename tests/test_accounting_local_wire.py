"""Compact proof clocks cannot extend warm dispatch or change financial facts."""

import asyncio
from datetime import timedelta
import json
from uuid import uuid4

import pytest

from src.billing.accounting_local_leases import LocalDispatchPermit
from src.billing.accounting_local_receipts import LocalReceiptStore
from src.billing.accounting_local_terminal import LocalTerminalOwner
from src.billing.accounting_local_wire import (
    compact_local_batch,
    expand_compact_local_permits,
    restore_wire_terminals,
    wire_local_terminals,
)
from tests.test_accounting_local_handles import values
from tests.test_accounting_local_issue import deadline
from tests.test_accounting_local_terminal import Persistence
from tests.test_accounting_local_leases import terminal


@pytest.mark.parametrize("receiver_origin", [10, 100000000])
@pytest.mark.parametrize("clock_shift", [-3600, 0, 3600])
async def test_warm_reply_uses_remaining_lifetime_and_receiver_clock(receiver_origin, clock_shift):
    _, fields, _ = values()
    proof = fields["proof"]
    shifted = proof.grant.model_copy(
        update={
            "observed_at": proof.grant.observed_at + timedelta(seconds=clock_shift),
            "dispatch_expires_at": proof.grant.dispatch_expires_at + timedelta(seconds=clock_shift),
            "expires_at": proof.grant.expires_at + timedelta(seconds=clock_shift),
        }
    )
    proof = proof.model_copy(
        update={
            "grant": shifted,
            "reservation": proof.reservation.model_copy(
                update={"expires_at": proof.reservation.expires_at + timedelta(seconds=clock_shift)}
            ),
        }
    )
    fields["proof"] = proof
    permit = LocalDispatchPermit(**fields)
    body = compact_local_batch([permit], observed_monotonic=shifted.observed_monotonic + 28)
    assert b"observed_monotonic" not in body and b"reservation" not in body
    expanded = expand_compact_local_permits(
        body,
        [proof.reservation],
        observed_monotonic=receiver_origin,
        now=receiver_origin + 1,
    )[0]
    assert expanded.proof.grant.dispatch_deadline == receiver_origin + 2
    assert expanded.proof.reservation == proof.reservation
    assert expanded.dispatch_token == permit.dispatch_token


async def test_network_delay_cannot_restore_an_expired_warm_dispatch():
    proof, fields, _ = values()
    body = compact_local_batch(
        [LocalDispatchPermit(**fields)], observed_monotonic=proof.grant.observed_monotonic + 28
    )
    with pytest.raises(ValueError, match="no live dispatch"):
        expand_compact_local_permits(body, [proof.reservation], observed_monotonic=10, now=12)


async def test_network_delay_cannot_outlive_the_reserved_operation():
    proof, fields, _ = values()
    reservation = proof.reservation.model_copy(
        update={"expires_at": proof.grant.observed_at + timedelta(seconds=0.5)}
    )
    fields["proof"] = proof.model_copy(update={"reservation": reservation})
    body = compact_local_batch(
        [LocalDispatchPermit(**fields)], observed_monotonic=proof.grant.observed_monotonic
    )
    with pytest.raises(ValueError, match="no live dispatch"):
        expand_compact_local_permits(body, [reservation], observed_monotonic=10, now=11)


@pytest.mark.parametrize(
    "failure", ["operation", "generation", "token", "ordinal", "proof", "length"]
)
async def test_mismatched_compact_reply_cannot_dispatch(failure):
    proof, fields, _ = values()
    encoded = compact_local_batch(
        [LocalDispatchPermit(**fields)], observed_monotonic=proof.grant.observed_monotonic
    )
    wire = json.loads(encoded)
    if failure == "operation":
        wire[0]["operation_id"] = str(uuid4())
    elif failure == "generation":
        wire[0]["protocol_generation"] = 8
    elif failure == "token":
        wire[0]["dispatch_token"] = str(uuid4())
    elif failure == "ordinal":
        wire[0]["permit_ordinal"] = 4
    elif failure == "proof":
        wire[0]["grant"] = None
    else:
        wire = []
    with pytest.raises(ValueError):
        expand_compact_local_permits(
            json.dumps(wire).encode(), [proof.reservation], observed_monotonic=10, now=11
        )


async def test_terminal_crosses_process_clocks_and_keeps_the_exact_financial_identity():
    value = terminal()
    receipts = LocalReceiptStore(max_entries=1, max_retained_bytes=100000)
    assert receipts.retain(value.receipt)
    body = wire_local_terminals([value], generation=7)
    assert b"observed_monotonic" not in body
    remote = restore_wire_terminals(body, generation=7, observed_monotonic=100000000)
    assert remote[0].receipt.grant.observed_monotonic != value.receipt.grant.observed_monotonic
    owner = LocalTerminalOwner(Persistence(), receipts, generation=7)
    assert len(await owner.finalize_batch(remote, expires_at=deadline())) == 1
    assert receipts.entries == receipts.retained_bytes == 0
    assert len(await owner.finalize_batch(remote, expires_at=deadline())) == 1


@pytest.mark.parametrize(
    "field", ["fence_token", "grant_id", "dispatch_expires_at", "permit_ordinal"]
)
async def test_clock_independence_does_not_allow_financial_proof_changes(field):
    value = terminal()
    receipts = LocalReceiptStore(max_entries=1, max_retained_bytes=100000)
    assert receipts.retain(value.receipt)
    charge = receipts.retained_bytes
    wire = json.loads(wire_local_terminals([value], generation=7))
    if field == "permit_ordinal":
        wire[0][field] = 1
    else:
        wire[0]["grant"][field] = {
            "fence_token": str(uuid4()),
            "grant_id": "different-grant",
            "dispatch_expires_at": (
                value.receipt.grant.dispatch_expires_at + timedelta(seconds=1)
            ).isoformat(),
        }[field]
    remote = restore_wire_terminals(
        json.dumps(wire).encode(), generation=7, observed_monotonic=100000000
    )
    with pytest.raises(ValueError, match="does not match"):
        await LocalTerminalOwner(Persistence(), receipts, generation=7).finalize_batch(
            remote, expires_at=deadline()
        )
    assert receipts.entries == 1 and receipts.retained_bytes == charge


async def test_wire_terminal_rejects_nan_duplicate_and_stale_generation():
    value = terminal()
    with pytest.raises(ValueError):
        wire_local_terminals([value, value], generation=7)
    with pytest.raises(ValueError):
        restore_wire_terminals(
            wire_local_terminals([value], generation=7), generation=8, observed_monotonic=10
        )
    value.finalization.audit_envelope["invalid"] = float("nan")
    with pytest.raises(ValueError):
        wire_local_terminals([value], generation=7)


async def test_a_rebased_observation_keeps_one_existing_receipt_charge():
    value = terminal()
    receipts = LocalReceiptStore(max_entries=1, max_retained_bytes=100000)
    assert receipts.retain(value.receipt)
    charge = receipts.retained_bytes
    remote = restore_wire_terminals(
        wire_local_terminals([value], generation=7), generation=7, observed_monotonic=100000000
    )
    assert receipts.retain(remote[0].receipt)
    assert receipts.entries == 1 and receipts.retained_bytes == charge


async def test_terminal_wire_can_preserve_an_old_proof_for_durable_replay():
    value = terminal()
    grant = value.receipt.grant
    old = grant.model_copy(
        update={"observed_monotonic": max(0, asyncio.get_running_loop().time() - 3600)}
    )
    value = value.model_copy(update={"receipt": value.receipt.model_copy(update={"grant": old})})
    body = wire_local_terminals([value], generation=7)
    restored = restore_wire_terminals(body, generation=7, observed_monotonic=10)
    assert restored[0].receipt.grant.expires_at == grant.expires_at
    assert restored[0].finalization == value.finalization
