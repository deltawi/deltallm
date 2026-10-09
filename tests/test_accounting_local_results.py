"""Prepare the complete mixed admission result before consuming any permit."""

import json
from dataclasses import replace
from uuid import uuid4

import pytest

from src.billing.accounting.accounting_protocol import DispatchPermit, ReserveDecision
from src.billing.accounting.durable_microbatch import DurableBatchFull
from tests.test_accounting_local_cursors import value
from tests.test_accounting_local_issue import deadline, owners, receipt


def denial(operation_id=None, *, generation=7, decision=ReserveDecision.BUDGET_EXHAUSTED):
    return DispatchPermit(
        protocol_generation=generation, operation_id=operation_id or uuid4(), decision=decision
    )


async def test_mixed_reply_keeps_request_order_and_retains_only_issued_proofs():
    subject, grant = value()
    cursors, retained, issue = owners()
    assert cursors.add(subject, grant)
    proposed = [receipt(grant, ordinal, subject=subject) for ordinal in range(2)]
    denied = denial()
    replay = denial(decision=ReserveDecision.REPLAY)
    order = [
        denied.operation_id,
        proposed[1].reservation.operation_id,
        replay.operation_id,
        proposed[0].reservation.operation_id,
    ]
    result = issue.commit(
        proposed, non_dispatch=[denied, replay], operation_order=order, expires_at=deadline()
    )
    assert [permit.operation_id for permit in result.permits] == order
    assert [permit.decision for permit in result.permits] == [
        ReserveDecision.BUDGET_EXHAUSTED,
        ReserveDecision.DISPATCH,
        ReserveDecision.REPLAY,
        ReserveDecision.DISPATCH,
    ]
    assert len(result.proofs) == retained.entries == 2
    assert [proof.restore() for proof in result.proofs] == proposed
    assert cursors.get(subject).next_ordinal == 2


@pytest.mark.parametrize("failure", ["duplicate", "stale", "dispatch", "order", "missing"])
async def test_invalid_mixed_output_leaves_both_stores_unchanged(failure):
    subject, grant = value()
    cursors, retained, issue = owners()
    assert cursors.add(subject, grant)
    proposed = receipt(grant, 0, subject=subject)
    denied = denial()
    order = [proposed.reservation.operation_id, denied.operation_id]
    if failure == "duplicate":
        denied = denial(proposed.reservation.operation_id)
    elif failure == "stale":
        denied = denial(generation=8)
    elif failure == "dispatch":
        denied = DispatchPermit(
            protocol_generation=7,
            operation_id=denied.operation_id,
            decision=ReserveDecision.DISPATCH,
            dispatch_token=uuid4(),
            accounting_partition=0,
        )
    elif failure == "order":
        order = [denied.operation_id, denied.operation_id]
    else:
        order = [proposed.reservation.operation_id]
    with pytest.raises(ValueError):
        issue.commit(
            [proposed], non_dispatch=[denied], operation_order=order, expires_at=deadline()
        )
    assert cursors.get(subject).next_ordinal == 0 and cursors.available_permits == 4
    assert retained.entries == retained.retained_bytes == 0


async def test_denial_only_reply_creates_no_issue_or_retained_charge():
    cursors, retained, issue = owners()
    denied = denial()
    result = issue.commit([], non_dispatch=[denied], expires_at=deadline())
    assert result.permits == (denied,) and result.proofs == ()
    assert cursors.entries == retained.entries == 0


async def test_mixed_reply_has_one_shared_256_result_limit():
    subject, grant = value()
    cursors, retained, issue = owners()
    assert cursors.add(subject, grant)
    with pytest.raises(ValueError):
        issue.commit(
            [receipt(grant, 0, subject=subject)],
            non_dispatch=[denial() for _ in range(256)],
            expires_at=deadline(),
        )
    assert cursors.get(subject).next_ordinal == 0 and retained.entries == 0


async def test_output_preparation_failure_does_not_consume_a_warm_prefix(monkeypatch):
    from src.billing.accounting.permits import accounting_local_issue as module

    subject, grant = value()
    cursors, retained, issue = owners()
    assert cursors.add(subject, grant)

    def fail(*_args):
        raise RuntimeError("output preparation failed")

    monkeypatch.setattr(module, "_result", fail)
    with pytest.raises(RuntimeError, match="output preparation failed"):
        issue.commit([receipt(grant, 0, subject=subject)], expires_at=deadline())
    assert cursors.get(subject).next_ordinal == 0 and retained.entries == 0


async def test_complete_reply_bytes_are_bounded_before_either_store_changes():
    cursors, retained, issue = owners()
    proposed = []
    target = (1_048_576 - 2 - 15 - 256) // 16
    for index in range(16):
        subject, grant = value()
        subject = replace(subject, model=f"reply-size-{index}")
        assert cursors.add(subject, grant)
        item = receipt(grant, 0, subject=subject)
        reservation = item.reservation.model_copy(
            update={
                "attribution": item.reservation.attribution.model_copy(
                    update={"model": subject.model}
                ),
                "audit_envelope": {"data": ""},
            }
        )
        base = item.model_copy(update={"reservation": reservation})
        size = len(
            json.dumps(
                base.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode()
        )
        reservation = reservation.model_copy(
            update={"audit_envelope": {"data": "x" * (target - size)}}
        )
        proposed.append(base.model_copy(update={"reservation": reservation}))
    proof_bytes = len(
        json.dumps(
            [item.model_dump(mode="json") for item in proposed],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
    )
    assert proof_bytes <= 1_048_576
    with pytest.raises(DurableBatchFull):
        issue.commit(proposed, expires_at=deadline())
    assert retained.entries == retained.retained_bytes == 0
    assert cursors.available_permits == 64
