"""Local proofs keep the same identity through request retries."""

from decimal import Decimal
from uuid import uuid4

import pytest

from src.billing.accounting_local_leases import LocalAccountingHandle, LocalDispatchPermit
from src.billing.accounting_protocol import (
    AccountingAttempt,
    AccountingOperationHandle,
    DispatchPermit,
    ReserveDecision,
)
from tests.test_accounting_local_leases import terminal

pytestmark = pytest.mark.asyncio


def values():
    proof = terminal().receipt
    reservation = proof.reservation
    common = {
        "dispatch_token": reservation.owner_token,
        "accounting_partition": proof.grant.accounting_partition,
        "proof": proof,
    }
    permit = {
        **common,
        "protocol_generation": reservation.protocol_generation,
        "operation_id": reservation.operation_id,
        "decision": ReserveDecision.DISPATCH,
    }
    handle = {
        **common,
        "reservation": reservation,
        "attempts": (
            AccountingAttempt(
                deployment_id="deployment", provider="openai", model="model", pricing_snapshot={}
            ),
        ),
    }
    return proof, permit, handle


async def test_local_proofs_fit_existing_interfaces_and_survive_json_and_retry():
    proof, permit, handle = values()
    issued = LocalDispatchPermit(**permit)
    operation = LocalAccountingHandle(**handle)
    assert isinstance(issued, DispatchPermit)
    assert isinstance(operation, AccountingOperationHandle)
    assert LocalDispatchPermit.model_validate_json(issued.model_dump_json()).proof == proof
    assert LocalAccountingHandle.model_validate_json(operation.model_dump_json()).proof == proof
    reused = operation.model_copy(update={"attempts": operation.attempts * 2})
    assert reused.proof == proof and len(reused.attempts) == 2
    assert str(proof.grant.fence_token) not in repr(issued) + repr(operation)


@pytest.mark.parametrize(
    "field",
    ["protocol_generation", "operation_id", "dispatch_token", "accounting_partition", "decision"],
)
async def test_mismatched_dispatch_identity_is_rejected(field):
    _, permit, _ = values()
    permit[field] = {
        "protocol_generation": 8,
        "operation_id": uuid4(),
        "dispatch_token": uuid4(),
        "accounting_partition": 3,
        "decision": ReserveDecision.REPLAY,
    }[field]
    with pytest.raises(ValueError):
        LocalDispatchPermit(**permit)


@pytest.mark.parametrize("field", ["reservation", "dispatch_token", "accounting_partition"])
async def test_mismatched_request_handle_is_rejected(field):
    _, _, handle = values()
    handle[field] = {
        "reservation": handle["reservation"].model_copy(update={"request_fingerprint": "b" * 64}),
        "dispatch_token": uuid4(),
        "accounting_partition": 3,
    }[field]
    with pytest.raises(ValueError):
        LocalAccountingHandle(**handle)


@pytest.mark.parametrize("kind", ["permit", "handle"])
async def test_mutated_nested_nan_cannot_enter_a_local_proof(kind):
    proof, permit, handle = values()
    proof.reservation.pricing_snapshot["invalid"] = float("nan")
    constructor, kwargs = (
        (LocalDispatchPermit, permit) if kind == "permit" else (LocalAccountingHandle, handle)
    )
    with pytest.raises(ValueError):
        constructor(**kwargs)


@pytest.mark.parametrize("kind", ["permit", "handle"])
@pytest.mark.parametrize("failure", ["negative_ordinal", "fence_type", "boolean_limit"])
async def test_unvalidated_scalar_copy_cannot_enter_a_dispatch_handle(kind, failure):
    proof, permit, fields = values()
    if failure == "negative_ordinal":
        proof = proof.model_copy(update={"permit_ordinal": -1})
    else:
        changes = (
            {"fence_token": "not-a-uuid"} if failure == "fence_type" else {"operation_limit": True}
        )
        proof = proof.model_copy(update={"grant": proof.grant.model_copy(update=changes)})
    constructor, arguments = (
        (LocalDispatchPermit, permit) if kind == "permit" else (LocalAccountingHandle, fields)
    )
    arguments["proof"] = proof
    with pytest.raises(ValueError):
        constructor(**arguments)


async def test_original_request_mutation_cannot_change_a_constructed_dispatch_handle():
    proof, permit, fields = values()
    issued = LocalDispatchPermit(**permit)
    operation = LocalAccountingHandle(**fields)
    proof.reservation.pricing_snapshot["version"] = "after"
    fields["reservation"].audit_envelope["action"] = "after"
    assert issued.proof.reservation.pricing_snapshot["version"] == "price-v1"
    assert operation.proof.reservation.pricing_snapshot["version"] == "price-v1"
    assert operation.reservation.audit_envelope["action"] != "after"


@pytest.mark.parametrize("kind", ["permit", "handle"])
async def test_proof_encoding_runs_only_at_the_two_mutable_boundaries(monkeypatch, kind):
    from src.billing import accounting_local_leases

    _, permit, handle = values()
    original = accounting_local_leases.reservation_bytes
    encoded = []

    def counted(value):
        result = original(value)
        encoded.append(result)
        return result

    monkeypatch.setattr(accounting_local_leases, "reservation_bytes", counted)
    if kind == "permit":
        LocalDispatchPermit(**permit)
        assert len(encoded) == 1
    else:
        LocalAccountingHandle(**handle)
        assert len(encoded) == 2 and encoded[0] == encoded[1]


async def test_equal_money_with_a_different_canonical_document_is_still_rejected():
    proof, _, handle = values()
    amount = proof.reservation.allowance
    changed_amount = amount.quantize(Decimal("0.000000000000000001"))
    assert amount == changed_amount and str(amount) != str(changed_amount)
    handle["reservation"] = proof.reservation.model_copy(update={"allowance": changed_amount})
    with pytest.raises(ValueError, match="does not match its handle"):
        LocalAccountingHandle(**handle)
