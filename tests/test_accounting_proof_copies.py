"""Proof-copy optimization must not trust nested models or mutable containers."""

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.billing.accounting_local_leases import LocalAccountingHandle, LocalDispatchPermit
from src.billing.accounting_local_receipts import RetainedLocalReceipt
from src.billing.accounting_local_receipts import LocalReceiptStore
from src.billing.accounting_snapshots import reservation_bytes
from src.billing.spend_operations import SpendPersistenceUnavailable
from src.billing.accounting_terminal_snapshots import FrozenLocalTerminal
from tests.test_accounting_local_leases import terminal
from src.telemetry.spend_operation import _reuse_accounting_allowance
from tests.test_accounting_local_handles import values


@pytest.mark.parametrize("kind", ["permit", "handle", "receipt"])
@pytest.mark.parametrize("failure", ["grant", "ordinal", "owner", "expiry", "money", "nan"])
async def test_every_raw_copy_boundary_rejects_forged_financial_graphs(kind, failure):
    proof, permit, handle = values()
    if failure == "grant":
        proof = proof.model_copy(
            update={"grant": proof.grant.model_copy(update={"operation_limit": True})}
        )
    elif failure == "ordinal":
        proof = proof.model_copy(update={"permit_ordinal": True})
    elif failure == "owner":
        proof = proof.model_copy(
            update={"reservation": proof.reservation.model_copy(update={"owner_token": uuid4()})}
        )
    elif failure == "expiry":
        proof = proof.model_copy(
            update={
                "reservation": proof.reservation.model_copy(
                    update={"expires_at": proof.grant.expires_at.replace(year=2099)}
                )
            }
        )
    elif failure == "money":
        proof = proof.model_copy(
            update={"reservation": proof.reservation.model_copy(update={"allowance": Decimal(-1)})}
        )
    else:
        proof.reservation.audit_envelope["nested"] = {"invalid": [float("nan")]}
    if kind == "receipt" and failure == "owner":
        # A receipt alone has no outer owner to compare against. It may change
        # identity before dispatch; permit/handle identity must still reject it.
        frozen = RetainedLocalReceipt.freeze(proof)
        assert frozen.restore().reservation.owner_token == proof.reservation.owner_token
        return
    with pytest.raises(ValueError):
        if kind == "receipt":
            RetainedLocalReceipt.freeze(proof)
        else:
            fields = permit if kind == "permit" else handle
            fields["proof"] = proof
            (LocalDispatchPermit if kind == "permit" else LocalAccountingHandle)(**fields)


@pytest.mark.parametrize("kind", ["permit", "handle"])
async def test_nested_model_inside_a_raw_dictionary_is_not_a_validation_escape(kind):
    proof, permit, handle = values()
    fields = permit if kind == "permit" else handle
    fields["proof"] = {
        "grant": proof.grant.model_copy(update={"operation_limit": True}),
        "permit_ordinal": proof.permit_ordinal,
        "reservation": proof.reservation,
    }
    with pytest.raises(ValueError):
        (LocalDispatchPermit if kind == "permit" else LocalAccountingHandle)(**fields)


async def test_detached_proof_owns_deep_containers():
    proof, permit, handle = values()
    proof.reservation.audit_envelope["nested"] = {"items": [{"amount": [1, 2]}]}
    issued = LocalDispatchPermit(**permit)
    operation = LocalAccountingHandle(**handle)
    frozen = RetainedLocalReceipt.freeze(proof)
    proof.reservation.audit_envelope["nested"]["items"][0]["amount"].append(3)
    issued.proof.reservation.audit_envelope["nested"]["items"][0]["amount"].append(4)
    operation.reservation.audit_envelope["nested"]["items"][0]["amount"].append(5)
    assert operation.proof.reservation.audit_envelope["nested"]["items"][0]["amount"] == [1, 2]
    assert frozen.restore().reservation.audit_envelope["nested"]["items"][0]["amount"] == [1, 2]


async def test_issue_capacity_check_uses_frozen_identity_without_decoding(monkeypatch):
    proof, _, _ = values()
    frozen = RetainedLocalReceipt.freeze(proof)
    store = LocalReceiptStore(max_entries=1, max_retained_bytes=frozen.retained_bytes)
    assert frozen.operation_id == proof.reservation.operation_id
    assert frozen.generation == proof.reservation.protocol_generation

    def forbidden(*args, **kwargs):
        raise AssertionError("accepted proof must not be decoded for a capacity check")

    monkeypatch.setattr(RetainedLocalReceipt, "restore", forbidden)
    assert store.prepare_issue((frozen,))
    with pytest.raises(ValueError, match="repeat"):
        store.prepare_issue((frozen, frozen))


@pytest.mark.parametrize("failure", ["grant", "ordinal", "document", "oversize"])
async def test_raw_snapshot_constructor_cannot_forge_frozen_identity(failure):
    proof, _, _ = values()
    grant, ordinal, document = (
        proof.grant,
        proof.permit_ordinal,
        reservation_bytes(proof.reservation),
    )
    if failure == "grant":
        grant = grant.model_copy(update={"operation_limit": True})
    elif failure == "ordinal":
        ordinal = True
    elif failure == "document":
        document = b"{}"
    else:
        document = b"x" * 1_048_577
    with pytest.raises(ValueError):
        RetainedLocalReceipt(grant, ordinal, document)


async def test_frozen_identity_cannot_be_reinitialized():
    proof, _, _ = values()
    frozen = RetainedLocalReceipt.freeze(proof)
    with pytest.raises(ValueError, match="already set"):
        frozen._set_owned(proof, b"changed")


async def test_retry_checks_only_the_new_attempt_and_keeps_the_original_proof(monkeypatch):
    from src.billing import accounting_local_leases

    _, _, fields = values()
    operation = LocalAccountingHandle(**fields)
    attempt = fields["attempts"][0]
    attempt.pricing_snapshot["nested"] = {"values": [1, 2]}

    def forbidden(*args, **kwargs):
        raise AssertionError("a provider retry must not copy the accepted reservation")

    monkeypatch.setattr(accounting_local_leases, "reservation_snapshot", forbidden)
    kwargs = dict(
        auth=SimpleNamespace(api_key=operation.reservation.attribution.api_key),
        model=operation.reservation.attribution.model,
        call_type=operation.reservation.attribution.call_type,
        max_attempts=2,
        allowance=operation.reservation.allowance,
        attempt=attempt,
    )
    retried = _reuse_accounting_allowance(operation, **kwargs)
    assert retried.proof is operation.proof
    assert retried.reservation is operation.reservation
    attempt.pricing_snapshot["nested"]["values"].append(3)
    assert retried.attempts[-1].pricing_snapshot["nested"]["values"] == [1, 2]
    attempt.pricing_snapshot["bad"] = float("nan")
    with pytest.raises(SpendPersistenceUnavailable):
        _reuse_accounting_allowance(operation, **kwargs)
    with pytest.raises(SpendPersistenceUnavailable):
        _reuse_accounting_allowance(retried, **kwargs)
    full = operation.model_copy(update={"attempts": operation.attempts * 128})
    with pytest.raises(SpendPersistenceUnavailable):
        _reuse_accounting_allowance(full, **{**kwargs, "max_attempts": 129})


@pytest.mark.parametrize("field", ["receipt", "finalization", "grant", "reservation"])
async def test_invalid_nested_model_types_fail_with_validation_error(field):
    value = terminal()
    if field in ("receipt", "finalization"):
        value = value.model_copy(update={field: None})
    else:
        value = value.model_copy(update={"receipt": value.receipt.model_copy(update={field: None})})
    with pytest.raises(ValueError):
        FrozenLocalTerminal(value, generation=7)
