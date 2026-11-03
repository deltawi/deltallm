"""Immutable issue proofs stay inside finite entry and retained-byte bounds."""

import json
from uuid import uuid4

import pytest

from src.billing.accounting.permits.accounting_local_leases import LocalPermitReceipt
from src.billing.accounting.accounting_protocol import AccountingOutcome, FinalizationReceipt
from tests.test_preissued_permit_bytes import retained_object_bytes
from collections import OrderedDict
from tests.test_accounting_local_leases import terminal

from src.billing.accounting.permits.accounting_local_receipts import (
    LocalReceiptStore,
    RetainedLocalReceipt,
    reservation_bytes,
)
from src.billing.accounting.journal.accounting_terminal_receipts import JournalReceipt
from src.billing.accounting.journal.accounting_terminal_snapshots import FrozenLocalTerminal


def acknowledgement(receipt):
    return FinalizationReceipt(
        protocol_generation=receipt.reservation.protocol_generation,
        operation_id=receipt.reservation.operation_id,
        event_sequence=1,
        outcome=AccountingOutcome.COMPLETED,
    )


@pytest.mark.parametrize("bound", ["entries", "bytes"])
async def test_full_store_keeps_every_existing_proof_and_cannot_evict(bound):
    first, second = terminal().receipt, terminal().receipt
    charge = RetainedLocalReceipt(
        first.grant, 0, reservation_bytes(first.reservation)
    ).retained_bytes
    store = LocalReceiptStore(
        max_entries=1 if bound == "entries" else 2,
        max_retained_bytes=charge if bound == "bytes" else 8 * 1024 * 1024,
    )
    assert store.retain(first)
    assert not store.retain(second)
    assert store.get(first.reservation.operation_id) == first
    assert store.get(second.reservation.operation_id) is None
    assert store.retained_bytes == charge
    assert store.entries == 1
    assert store.acknowledge(first, acknowledgement(first))
    assert store.retained_bytes == 0
    assert not store.acknowledge(first, acknowledgement(first))


async def test_nested_request_mutation_cannot_change_the_retained_receipt():
    first = terminal().receipt
    store = LocalReceiptStore(max_entries=2, max_retained_bytes=8 * 1024 * 1024)
    assert store.retain(first)
    restored = store.get(first.reservation.operation_id)
    first.reservation.pricing_snapshot["version"] = "changed"
    first.reservation.audit_envelope["action"] = "changed"
    assert store.get(first.reservation.operation_id) == restored
    with pytest.raises(ValueError, match="identity cannot change"):
        store.matches(first.reservation)
    with pytest.raises(ValueError, match="does not match"):
        store.acknowledge(first, acknowledgement(first))
    assert store.acknowledge(restored, acknowledgement(restored))


async def test_duplicate_proof_is_idempotent_and_different_ordinal_is_not():
    first = terminal().receipt
    store = LocalReceiptStore(max_entries=2, max_retained_bytes=8 * 1024 * 1024)
    assert store.retain(first)
    charge = store.retained_bytes
    assert store.retain(first)
    assert store.retained_bytes == charge
    changed = LocalPermitReceipt(grant=first.grant, permit_ordinal=1, reservation=first.reservation)
    with pytest.raises(ValueError, match="identity cannot change"):
        store.retain(changed)
    assert store.entries == 1
    assert store.retained_bytes == charge


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def journal_receipt(receipt, *, sequence=1):
    return JournalReceipt(
        protocol_generation=receipt.reservation.protocol_generation,
        operation_id=receipt.reservation.operation_id,
        journal_sequence=sequence,
        outcome=AccountingOutcome.COMPLETED,
        replayed=True,
    )


def ids(values):
    return tuple(value.operation_id for value in values)


async def test_release_scan_takes_only_due_proofs_in_bounded_rotating_slices():
    clock = Clock()
    store = LocalReceiptStore(max_entries=301, max_retained_bytes=8 * 1024 * 1024, clock=clock)
    receipts = [terminal().receipt for _ in range(300)]
    for receipt in receipts:
        assert store.retain(receipt)
    expected = tuple(item.reservation.operation_id for item in receipts)
    clock.now += 9.9
    assert store.release_candidates(minimum_age_seconds=10) == ()
    clock.now += 0.1
    assert ids(store.release_candidates(minimum_age_seconds=10)) == expected[:256]
    late = terminal().receipt
    assert store.retain(late)
    assert ids(store.release_candidates(minimum_age_seconds=10)) == expected[256:]
    # Every proof was just checked or issued, so nothing is due again yet.
    assert store.release_candidates(minimum_age_seconds=10) == ()
    clock.now += 10
    assert ids(store.release_candidates(minimum_age_seconds=10, limit=3)) == expected[:3]
    assert store.entries == 301
    for arguments in ({"limit": 257}, {"limit": 0}):
        with pytest.raises(ValueError, match="1 to 256"):
            store.release_candidates(minimum_age_seconds=10, **arguments)
    for age in (0, float("nan"), 3601):
        with pytest.raises(ValueError, match="age is invalid"):
            store.release_candidates(minimum_age_seconds=age)


async def test_finalized_release_removes_only_the_exact_retained_issue():
    clock = Clock()
    store = LocalReceiptStore(max_entries=4, max_retained_bytes=8 * 1024 * 1024, clock=clock)
    first, second = terminal().receipt, terminal().receipt
    assert store.retain(first) and store.retain(second)
    kept = store.retained_bytes
    clock.now += 1
    proofs = store.release_candidates(minimum_age_seconds=1)
    with pytest.raises(ValueError, match="does not match"):
        store.release_finalized(((proofs[0], journal_receipt(second)),))
    with pytest.raises(ValueError, match="does not match"):
        store.release_finalized(((proofs[0], acknowledgement(first)),))
    with pytest.raises(ValueError, match="repeats"):
        store.release_finalized(((proofs[0], journal_receipt(first)),) * 2)
    assert store.entries == 2 and store.retained_bytes == kept
    assert store.release_finalized(((proofs[0], journal_receipt(first)),)) == 1
    assert store.get(first.reservation.operation_id) is None
    assert store.get(second.reservation.operation_id) == second
    assert store.retained_bytes == kept - proofs[0].retained_bytes
    assert store.release_finalized(((proofs[0], journal_receipt(first)),)) == 0
    # A proof this process already finalized and issued again is not the one
    # the lookup checked, so the newer issue stays.
    assert store.acknowledge(second, acknowledgement(second))
    assert store.retain(second)
    assert store.release_finalized(((proofs[1], journal_receipt(second)),)) == 0
    assert store.entries == 1


async def test_expired_release_waits_for_the_database_grant_expiry_and_grace():
    store = LocalReceiptStore(max_entries=2, max_retained_bytes=8 * 1024 * 1024, clock=Clock())
    receipt = terminal().receipt
    assert store.retain(receipt)
    store._touched[receipt.reservation.operation_id] = -1.0
    (proof,) = store.release_candidates(minimum_age_seconds=1)
    deadline = receipt.grant.recovery_deadline
    with pytest.raises(ValueError, match="not expired"):
        store.release_expired((proof,), now=deadline + 59.9, grace_seconds=60)
    assert store.entries == 1
    for now, grace in ((float("inf"), 60), (deadline + 61, -1), (deadline + 61, float("nan"))):
        with pytest.raises(ValueError, match="invalid"):
            store.release_expired((proof,), now=now, grace_seconds=grace)
    assert store.release_expired((proof,), now=deadline + 60, grace_seconds=60) == 1
    assert store.entries == store.retained_bytes == 0
    assert store.release_expired((proof,), now=deadline + 60, grace_seconds=60) == 0


async def test_fill_ratio_uses_the_tighter_bound():
    receipt = terminal().receipt
    charge = RetainedLocalReceipt.freeze(receipt).retained_bytes
    store = LocalReceiptStore(max_entries=4, max_retained_bytes=2 * charge)
    assert store.fill_ratio == 0
    assert store.retain(receipt)
    assert store.fill_ratio == 0.5
    assert store.max_entries == 4 and store.max_retained_bytes == 2 * charge


async def test_terminal_identity_matches_the_journal_identity():
    value = terminal()
    proof = RetainedLocalReceipt.freeze(value.receipt)
    identity = proof.terminal_identity()
    journal = json.loads(FrozenLocalTerminal(value, generation=7).journal_identity_json)
    assert identity.model_dump(mode="json") == {
        key: journal[key]
        for key in (
            "operation_id",
            "grant_id",
            "grantee_id",
            "fence_token",
            "permit_ordinal",
            "reservation_sha256",
        )
    }


async def test_unknown_operation_has_no_proof():
    store = LocalReceiptStore(max_entries=1, max_retained_bytes=8 * 1024 * 1024)
    assert store.get(uuid4()) is None


@pytest.mark.parametrize("characters", [1, 256])
@pytest.mark.parametrize("envelope", ["small", "large", "nested"])
async def test_charge_covers_the_complete_typed_graph(characters, envelope):
    first = terminal().receipt
    if envelope == "large":
        first.reservation.pricing_snapshot["data"] = "p" * 32000
        first.reservation.audit_envelope["data"] = "a" * 64000
    elif envelope == "nested":
        first.reservation.pricing_snapshot["data"] = [{"a": 1}] * 1000
        first.reservation.audit_envelope["data"] = [{"a": ["😀", 1]}] * 1000
    first = LocalPermitReceipt(
        grant=first.grant.model_copy(
            update={
                "grant_id": "😁" * characters,
                "grantee_id": "😂" * characters,
            }
        ),
        permit_ordinal=first.permit_ordinal,
        reservation=first.reservation,
    )
    retained = RetainedLocalReceipt.freeze(first)
    graph = OrderedDict([(first.reservation.operation_id, retained)])
    assert retained.retained_bytes >= retained_object_bytes(graph)


async def test_original_and_restored_mutations_never_change_retained_bytes():
    first = terminal().receipt
    first.reservation.pricing_snapshot["nested"] = {"value": [1, {"value": "original"}]}
    store = LocalReceiptStore(max_entries=1, max_retained_bytes=8 * 1024 * 1024)
    assert store.retain(first)
    size = store.retained_bytes
    restored = store.get(first.reservation.operation_id)
    first.reservation.pricing_snapshot["nested"]["value"][1]["value"] = "first mutation"
    restored.reservation.pricing_snapshot["nested"]["value"][1]["value"] = "second mutation"
    original = store.get(first.reservation.operation_id)
    assert original.reservation.pricing_snapshot["nested"]["value"][1]["value"] == "original"
    assert store.retained_bytes == size


@pytest.mark.parametrize("field", ["operation_id", "protocol_generation"])
async def test_wrong_terminal_ack_keeps_the_issued_receipt(field):
    first = terminal().receipt
    store = LocalReceiptStore(max_entries=1, max_retained_bytes=8 * 1024 * 1024)
    assert store.retain(first)
    changed = acknowledgement(first).model_copy(
        update={field: uuid4() if field == "operation_id" else 8}
    )
    with pytest.raises(ValueError, match="terminal acknowledgement"):
        store.acknowledge(first, changed)
    assert store.get(first.reservation.operation_id) == first


@pytest.mark.parametrize(
    "parameter,value",
    [
        ("max_entries", 0),
        ("max_entries", 100001),
        ("max_retained_bytes", 0),
        ("max_retained_bytes", 64 * 1024 * 1024 + 1),
    ],
)
async def test_constructor_has_finite_entry_and_byte_bounds(parameter, value):
    arguments = dict(max_entries=1, max_retained_bytes=8 * 1024 * 1024)
    arguments[parameter] = value
    with pytest.raises(ValueError, match="capacity"):
        LocalReceiptStore(**arguments)


@pytest.mark.parametrize("parameter,value", [("entries", -1), ("retained_bytes", -1)])
async def test_negative_capacity_estimates_cannot_bypass_the_bound(parameter, value):
    store = LocalReceiptStore(max_entries=1, max_retained_bytes=8 * 1024 * 1024)
    arguments = dict(entries=1, retained_bytes=1)
    arguments[parameter] = value
    with pytest.raises(ValueError, match="negative"):
        store.capacity_for(**arguments)


@pytest.mark.parametrize("field,maximum", [("pricing_snapshot", 32768), ("audit_envelope", 65536)])
async def test_post_construction_oversized_payload_cannot_enter_the_store(field, maximum):
    first = terminal().receipt
    getattr(first.reservation, field)["data"] = "x" * maximum
    store = LocalReceiptStore(max_entries=1, max_retained_bytes=8 * 1024 * 1024)
    with pytest.raises(ValueError, match="size limit"):
        store.retain(first)
    assert store.entries == store.retained_bytes == 0


async def test_nan_cannot_enter_an_immutable_json_proof():
    first = terminal().receipt
    first.reservation.pricing_snapshot["data"] = float("nan")
    store = LocalReceiptStore(max_entries=1, max_retained_bytes=8 * 1024 * 1024)
    with pytest.raises(ValueError):
        store.retain(first)
    assert store.entries == store.retained_bytes == 0


async def test_changed_request_identity_cannot_match_or_release_an_existing_proof():
    first = terminal().receipt
    store = LocalReceiptStore(max_entries=1, max_retained_bytes=8 * 1024 * 1024)
    assert store.retain(first)
    changed = first.reservation.model_copy(update={"owner_token": uuid4()})
    with pytest.raises(ValueError, match="identity cannot change"):
        store.matches(changed)
    assert store.matches(first.reservation)
    assert not store.matches(terminal().receipt.reservation)
    assert store.entries == 1


async def test_scalar_grant_is_copied_and_repr_does_not_expose_fences_or_payloads():
    first = terminal().receipt
    retained = RetainedLocalReceipt.freeze(first)
    assert retained.grant is not first.grant
    for secret in (
        str(first.grant.fence_token),
        first.grant.grantee_id,
        str(first.reservation.owner_token),
        "provider.dispatch",
    ):
        assert secret not in repr(retained)


async def test_byte_total_tracks_many_short_receipts_and_single_removal():
    receipts = [terminal().receipt for _ in range(300)]
    store = LocalReceiptStore(max_entries=300, max_retained_bytes=8 * 1024 * 1024)
    total = sum(RetainedLocalReceipt.freeze(receipt).retained_bytes for receipt in receipts)
    for receipt in receipts:
        assert store.retain(receipt)
    assert store.retained_bytes == total
    assert total >= retained_object_bytes(store._values)
    first = receipts[0]
    assert store.acknowledge(first, acknowledgement(first))
    assert not store.acknowledge(first, acknowledgement(first))
    assert store.retained_bytes == total - RetainedLocalReceipt.freeze(first).retained_bytes
