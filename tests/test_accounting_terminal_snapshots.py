"""Immutable native handoffs preserve full proof validation and byte limits."""

from dataclasses import FrozenInstanceError
from decimal import Decimal
import hashlib
import json
from uuid import uuid4

import pytest

from src.billing.accounting.journal.accounting_journal import journal_batch
from src.billing.accounting.permits.accounting_local_receipts import LocalReceiptStore
from src.billing.accounting.journal.accounting_local_terminal import LocalTerminalOwner
from src.billing.accounting.transport.accounting_local_wire import (
    restore_wire_terminals,
    wire_local_terminals,
)
from src.billing.accounting.accounting_snapshots import finalization_bytes, reservation_bytes
from src.billing.accounting.journal.accounting_terminal_receipts import JournalReceipt
from src.billing.accounting.journal.accounting_terminal_snapshots import (
    FrozenLocalTerminal,
    freeze_terminal_snapshots,
)
from src.billing.accounting.durable_microbatch import DurableBatchFull
from tests.test_accounting_local_leases import terminal
from tests.test_accounting_local_terminal import Persistence
from tests.test_accounting_rpc import deadline, remote, rpc_state
from tests.test_preissued_permit_bytes import retained_object_bytes


async def test_snapshot_has_the_existing_exact_canonical_documents():
    value = terminal()
    snapshot = FrozenLocalTerminal(value, generation=7)
    assert snapshot.reservation_json == reservation_bytes(value.receipt.reservation)
    assert snapshot.retained_receipt.reservation_json is snapshot.reservation_json
    assert snapshot.finalization_json == finalization_bytes(value.finalization)
    identity = json.loads(snapshot.journal_identity_json)
    assert identity["reservation_sha256"] == hashlib.sha256(snapshot.reservation_json).hexdigest()
    assert identity["finalization_sha256"] == hashlib.sha256(snapshot.finalization_json).hexdigest()
    assert snapshot.restore() == value
    assert snapshot.receipt == value.receipt and snapshot.finalization == value.finalization
    assert "observed_monotonic" not in identity


async def test_nested_mutation_and_restored_copy_cannot_change_snapshot():
    value = terminal()
    value.receipt.reservation.audit_envelope["nested"] = {"before": [1, 2]}
    value.finalization.spend_payload["nested"] = {"before": [3, 4]}
    snapshot = FrozenLocalTerminal(value, generation=7)
    before = snapshot.document, snapshot.reservation_json, snapshot.finalization_json
    value.receipt.reservation.audit_envelope["nested"]["before"].append(5)
    value.finalization.spend_payload["nested"]["before"].append(6)
    snapshot.receipt.reservation.audit_envelope["nested"]["before"].append(7)
    snapshot.finalization.spend_payload["nested"]["before"].append(8)
    assert (snapshot.document, snapshot.reservation_json, snapshot.finalization_json) == before
    assert snapshot.restore().receipt.reservation.audit_envelope["nested"] == {"before": [1, 2]}
    with pytest.raises(FrozenInstanceError):
        snapshot.document = b"changed"
    with pytest.raises(ValueError):
        snapshot.retained_receipt.grant.allowance = Decimal(9)


@pytest.mark.parametrize(
    "failure", ["grant", "ordinal", "reservation", "finalization", "identity", "generation", "nan"]
)
async def test_full_graph_rejects_forged_model_copies(failure):
    value = terminal()
    receipt, finalization = value.receipt, value.finalization
    if failure == "grant":
        receipt = receipt.model_copy(
            update={"grant": receipt.grant.model_copy(update={"operation_limit": 0})}
        )
    elif failure == "ordinal":
        receipt = receipt.model_copy(update={"permit_ordinal": True})
    elif failure == "reservation":
        receipt = receipt.model_copy(
            update={
                "reservation": receipt.reservation.model_copy(update={"allowance": Decimal(-1)})
            }
        )
    elif failure == "finalization":
        finalization = finalization.model_copy(update={"exact_charge": Decimal(-1)})
    elif failure == "identity":
        finalization = finalization.model_copy(update={"owner_token": uuid4()})
    elif failure == "generation":
        finalization = finalization.model_copy(update={"protocol_generation": True})
    else:
        finalization.audit_envelope["bad"] = float("nan")
    value = value.model_copy(update={"receipt": receipt, "finalization": finalization})
    with pytest.raises(ValueError):
        FrozenLocalTerminal(value, generation=7)


@pytest.mark.parametrize("body", [b"{}", b"[]", b"null", b"invalid", b"x" * 1_048_576])
async def test_malformed_documents_do_not_create_an_accepted_snapshot(body):
    with pytest.raises((ValueError, DurableBatchFull)):
        FrozenLocalTerminal(body, generation=7)


@pytest.mark.parametrize("generation", [True, 0, -1, 2**63, 8])
async def test_snapshot_keeps_generation_validation(generation):
    with pytest.raises(ValueError):
        FrozenLocalTerminal(terminal(), generation=generation)


@pytest.mark.parametrize("failure", ["duplicate", "entries", "stale", "bytes"])
async def test_snapshot_batch_keeps_identity_entry_and_byte_bounds(failure):
    values = [FrozenLocalTerminal(terminal(), generation=7)]
    if failure == "duplicate":
        values *= 2
    elif failure == "entries":
        values *= 257
    elif failure == "stale":
        with pytest.raises(ValueError):
            freeze_terminal_snapshots(values, generation=8)
        return
    else:
        originals = [terminal() for _ in range(8)]
        for value in originals:
            value.finalization.spend_payload["large"] = "x" * 180_000
        values = [FrozenLocalTerminal(value, generation=7) for value in originals]
    with pytest.raises((ValueError, DurableBatchFull)):
        freeze_terminal_snapshots(values, generation=7)


async def test_native_handoffs_reuse_the_accepted_snapshot_without_restore(monkeypatch):
    snapshot = FrozenLocalTerminal(terminal(), generation=7)

    def forbidden(*args, **kwargs):
        raise AssertionError("native handoff repeated full model conversion")

    monkeypatch.setattr(FrozenLocalTerminal, "__init__", forbidden)
    monkeypatch.setattr(FrozenLocalTerminal, "restore", forbidden)
    monkeypatch.setattr(FrozenLocalTerminal, "receipt", property(forbidden))
    monkeypatch.setattr(FrozenLocalTerminal, "finalization", property(forbidden))
    assert freeze_terminal_snapshots([snapshot], generation=7)[0] is snapshot
    assert b"observed_monotonic" not in wire_local_terminals([snapshot], generation=7)
    batch = journal_batch([snapshot])
    assert batch.values[0] is snapshot
    assert batch.reservations == (snapshot.reservation_json.decode(),)
    assert batch.finalizations == (snapshot.finalization_json.decode(),)


@pytest.mark.parametrize("wide", [False, True])
async def test_retained_charge_covers_snapshot_objects_and_documents(wide):
    value = terminal()
    if wide:
        value.receipt.reservation.audit_envelope["payload"] = "a" * 60_000
        value.finalization.spend_payload["payload"] = "b" * 250_000
    snapshot = FrozenLocalTerminal(value, generation=7)
    assert snapshot.retained_bytes >= retained_object_bytes(snapshot)


async def test_signed_native_cycle_validates_only_at_caller_and_wire_boundaries(monkeypatch):
    app, service, client, _ = await rpc_state()
    persistence, transport = remote(app)
    receipts = LocalReceiptStore(max_entries=32, max_retained_bytes=8 * 1024 * 1024)
    values = [terminal() for _ in range(32)]
    for value in values:
        assert receipts.retain(value.receipt)
    owner = LocalTerminalOwner(persistence, receipts, generation=7, receipt_type=JournalReceipt)
    original = FrozenLocalTerminal.__init__
    boundaries = []

    def counted(self, value, *, generation):
        boundaries.append(type(value))
        original(self, value, generation=generation)

    monkeypatch.setattr(FrozenLocalTerminal, "__init__", counted)
    try:
        result = await owner.finalize_batch(values, expires_at=deadline())
        assert len(result) == 32 and len(client.calls) == 1
        assert receipts.entries == receipts.retained_bytes == service.terminals.retained_bytes == 0
        assert boundaries == [type(values[0])] * 64
    finally:
        await transport.close()
        await service.close(timeout_seconds=2)


async def test_received_wire_cannot_share_mutable_financial_graph():
    value = terminal()
    body = wire_local_terminals([value], generation=7)
    restored = restore_wire_terminals(body, generation=7, observed_monotonic=1000000)[0]
    snapshot = FrozenLocalTerminal(restored, generation=7)
    restored.finalization.audit_envelope["later"] = "changed"
    assert "later" not in snapshot.finalization.audit_envelope


async def test_document_batch_size_is_checked_before_any_model_conversion(monkeypatch):
    receipts = LocalReceiptStore(max_entries=1, max_retained_bytes=100000)
    persistence = Persistence()
    owner = LocalTerminalOwner(persistence, receipts, generation=7)

    def forbidden(*args, **kwargs):
        raise AssertionError("oversized batch was parsed")

    monkeypatch.setattr(FrozenLocalTerminal, "__init__", forbidden)
    with pytest.raises(ValueError, match="byte limit"):
        await owner.finalize_documents([b"x" * 600000, b"y" * 600000], expires_at=deadline())
    assert persistence.calls == []
