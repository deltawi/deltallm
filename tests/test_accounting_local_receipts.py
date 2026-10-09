"""Immutable issue proofs stay inside finite entry and retained-byte bounds."""

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


async def test_recovery_scan_rotates_only_one_bounded_slice_without_removing_proofs():
    store = LocalReceiptStore(max_entries=300, max_retained_bytes=8 * 1024 * 1024)
    receipts = [terminal().receipt for _ in range(300)]
    for receipt in receipts:
        assert store.retain(receipt)
    assert store.recovery_candidates() == tuple(
        item.reservation.operation_id for item in receipts[:256]
    )
    second = store.recovery_candidates(limit=44)
    assert second == tuple(item.reservation.operation_id for item in receipts[256:])
    assert store.entries == 300
    with pytest.raises(ValueError, match="1 to 256"):
        store.recovery_candidates(limit=257)


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
