"""A local issue commits all receipt proofs and cursor prefixes without an await."""

import ast
import asyncio
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from src.billing.accounting.permits.accounting_local_cursors import LocalCursorStore
from src.billing.accounting.permits.accounting_local_issue import LocalIssueCommit
from src.billing.accounting.permits.accounting_local_leases import LocalPermitReceipt
from src.billing.accounting.permits.accounting_local_receipts import LocalReceiptStore
from src.billing.accounting.accounting_protocol import ReserveDecision
from src.billing.accounting.durable_microbatch import DurableBatchFull
from tests.test_accounting_local_cursors import value
from tests.test_accounting_local_leases import terminal
from tests.test_preissued_permit_bank import fresh
from tests.test_preissued_permit_bytes import retained_object_bytes


def owners(**limits):
    cursors = LocalCursorStore(generation=7, max_entries=1024, max_retained_bytes=8 * 1024 * 1024)
    receipts = LocalReceiptStore(
        max_entries=limits.get("entries", 1024),
        max_retained_bytes=limits.get("bytes", 8 * 1024 * 1024),
    )
    return cursors, receipts, LocalIssueCommit(cursors, receipts)


def deadline():
    return asyncio.get_running_loop().time() + 1


def receipt(grant, ordinal, item=None, *, subject=None):
    if item is None:
        item = fresh(terminal().receipt.reservation)
        if subject is not None:
            item = item.model_copy(update={"windows": subject.windows})
    return LocalPermitReceipt(
        grant=grant,
        permit_ordinal=ordinal,
        reservation=item,
    )


async def test_warm_issue_retains_complete_proofs_and_returns_permits_in_input_order():
    subject, grant = value()
    cursors, retained, issue = owners()
    assert cursors.add(subject, grant)
    proposed = [receipt(grant, ordinal, subject=subject) for ordinal in range(3)]
    result = issue.commit(proposed, expires_at=deadline())
    assert [permit.operation_id for permit in result.permits] == [
        item.reservation.operation_id for item in proposed
    ]
    assert all(permit.decision is ReserveDecision.DISPATCH for permit in result.permits)
    assert [proof.restore() for proof in result.proofs] == proposed
    assert all(retained.get(item.reservation.operation_id) == item for item in proposed)
    assert cursors.get(subject).next_ordinal == 3
    assert cursors.available_permits == 1
    assert retained.entries == 3


async def test_warm_then_two_staged_grants_commit_one_contiguous_prefix_per_grant():
    subject, first = value()
    _, second = value()
    _, third = value()
    cursors, retained, issue = owners()
    assert cursors.add(subject, first)
    cursors.advance(subject)
    assert cursors.stage(subject, second)
    assert cursors.stage(subject, third)
    proposed = (
        [receipt(first, ordinal, subject=subject) for ordinal in range(1, 4)]
        + [receipt(second, ordinal, subject=subject) for ordinal in range(4)]
        + [receipt(third, 0, subject=subject)]
    )
    result = issue.commit(proposed, expires_at=deadline())
    assert len(result.permits) == retained.entries == 8
    assert cursors.entries == cursors.active_subjects == 1
    assert cursors.staged_grants == cursors.retiring_grants == 0
    assert cursors.get(subject).grant == third
    assert cursors.get(subject).next_ordinal == 1
    assert cursors.available_permits == 3


@pytest.mark.parametrize("bound", ["entries", "bytes"])
async def test_receipt_capacity_failure_does_not_consume_any_warm_or_staged_ordinal(bound):
    subject, first = value()
    _, second = value()
    cursors, retained, issue = owners(**{"entries": 1} if bound == "entries" else {"bytes": 1})
    assert cursors.add(subject, first)
    assert cursors.stage(subject, second)
    charge = cursors.retained_bytes
    with pytest.raises(DurableBatchFull):
        issue.commit(
            [receipt(first, 0, subject=subject), receipt(first, 1, subject=subject)],
            expires_at=deadline(),
        )
    assert cursors.get(subject).next_ordinal == 0
    assert cursors.staged(second.grant_id).next_ordinal == 0
    assert cursors.retained_bytes == charge and cursors.available_permits == 4
    assert retained.entries == retained.retained_bytes == 0


@pytest.mark.parametrize("failure", ["ordinal", "fence", "subject", "duplicate", "unknown"])
async def test_invalid_later_proof_cannot_leave_an_earlier_receipt_issued(failure):
    subject, grant = value()
    cursors, retained, issue = owners()
    assert cursors.add(subject, grant)
    first, second = receipt(grant, 0, subject=subject), receipt(grant, 1, subject=subject)
    if failure == "ordinal":
        second = receipt(grant, 2, subject=subject)
    elif failure == "fence":
        second = second.model_copy(
            update={"grant": grant.model_copy(update={"fence_token": uuid4()})}
        )
    elif failure == "subject":
        second = second.model_copy(
            update={
                "reservation": second.reservation.model_copy(
                    update={
                        "attribution": second.reservation.attribution.model_copy(
                            update={"model": "other"}
                        )
                    }
                )
            }
        )
    elif failure == "duplicate":
        second = receipt(grant, 1, first.reservation)
    else:
        _, unknown = value()
        second = receipt(unknown, 0, subject=subject)
    with pytest.raises(ValueError):
        issue.commit([first, second], expires_at=deadline())
    assert cursors.get(subject).next_ordinal == 0
    assert cursors.available_permits == 4
    assert retained.entries == retained.retained_bytes == 0


async def test_staged_issue_cannot_skip_a_warm_unused_suffix():
    subject, first = value()
    _, second = value()
    cursors, retained, issue = owners()
    assert cursors.add(subject, first) and cursors.stage(subject, second)
    with pytest.raises(ValueError, match="prefix"):
        issue.commit(
            [receipt(first, 0, subject=subject), receipt(second, 0, subject=subject)],
            expires_at=deadline(),
        )
    assert cursors.get(subject).next_ordinal == 0
    assert cursors.staged_grants == 1 and retained.entries == 0


async def test_an_issued_operation_cannot_receive_a_second_dispatch():
    subject, grant = value()
    cursors, retained, issue = owners()
    assert cursors.add(subject, grant)
    first = receipt(grant, 0, subject=subject)
    issue.commit([first], expires_at=deadline())
    with pytest.raises(ValueError, match="already issued"):
        issue.commit([receipt(grant, 1, first.reservation)], expires_at=deadline())
    assert cursors.get(subject).next_ordinal == 1 and retained.entries == 1


async def test_complete_batch_size_limit_is_checked_before_issue():
    cursors, retained, issue = owners()
    proposed = []
    for _ in range(16):
        subject, grant = value()
        assert cursors.stage(subject, grant)
        item = terminal().receipt.reservation.model_copy(
            update={"windows": subject.windows, "audit_envelope": {"blob": "x" * 65500}}
        )
        proposed.append(receipt(grant, 0, fresh(item)))
    with pytest.raises(DurableBatchFull):
        issue.commit(proposed, expires_at=deadline())
    assert cursors.staged_grants == 16 and cursors.available_permits == 0
    assert retained.entries == retained.retained_bytes == 0


@pytest.mark.parametrize("invalid", [0, float("nan"), float("inf")])
async def test_invalid_caller_deadline_has_no_issue_side_effect(invalid):
    subject, grant = value()
    cursors, retained, issue = owners()
    assert cursors.add(subject, grant)
    with pytest.raises(ValueError, match="deadline"):
        issue.commit([receipt(grant, 0, subject=subject)], expires_at=invalid)
    assert cursors.get(subject).next_ordinal == 0 and retained.entries == 0


async def test_an_expired_dispatch_grant_cannot_be_issued():
    subject, grant = value()
    grant = grant.model_copy(update={"observed_monotonic": 0})
    cursors, retained, issue = owners()
    assert cursors.stage(subject, grant)
    with pytest.raises(ValueError, match="dispatch"):
        issue.commit([receipt(grant, 0, subject=subject)], expires_at=deadline())
    assert cursors.staged_grants == 1 and retained.entries == 0


async def test_commit_freezes_nested_facts_before_changing_any_cursor():
    subject, grant = value()
    cursors, retained, issue = owners()
    assert cursors.add(subject, grant)
    proposed = receipt(grant, 0, subject=subject)
    result = issue.commit([proposed], expires_at=deadline())
    original = result.proofs[0].reservation_json
    proposed.reservation.audit_envelope["changed"] = True
    proposed.reservation.pricing_snapshot["changed"] = True
    assert (
        retained.get(proposed.reservation.operation_id).reservation.audit_envelope.get("changed")
        is None
    )
    assert result.proofs[0].reservation_json == original


@pytest.mark.parametrize("shift", [-3600, 0, 3600])
async def test_issue_uses_the_database_clock_anchor_not_host_wall_time(shift):
    subject, grant = value()
    item = receipt(grant, 0, subject=subject).reservation
    grant = grant.model_copy(
        update={
            name: getattr(grant, name) + timedelta(seconds=shift)
            for name in ("observed_at", "dispatch_expires_at", "expires_at")
        }
    )
    item = item.model_copy(update={"expires_at": item.expires_at + timedelta(seconds=shift)})
    cursors, retained, issue = owners()
    assert cursors.stage(subject, grant)
    assert len(issue.commit([receipt(grant, 0, item)], expires_at=deadline()).permits) == 1
    assert retained.entries == 1 and cursors.available_permits == 3


@pytest.mark.parametrize("late", ["caller", "dispatch"])
async def test_deadlines_are_checked_again_after_all_proofs_are_ready(monkeypatch, late):
    subject, grant = value()
    cursors, retained, issue = owners()
    assert cursors.add(subject, grant)
    proposed = receipt(grant, 0, subject=subject)
    loop = asyncio.get_running_loop()
    now = loop.time()
    elapsed = 2 if late == "caller" else 31
    clock = iter((now, now + elapsed, now + elapsed))
    with monkeypatch.context() as scoped:
        scoped.setattr(loop, "time", lambda: next(clock))
        with pytest.raises(ValueError, match="deadline" if late == "caller" else "dispatch"):
            issue.commit([proposed], expires_at=now + (1 if late == "caller" else 60))
    assert cursors.get(subject).next_ordinal == 0
    assert retained.entries == retained.retained_bytes == 0


async def test_256_subjects_commit_in_input_order_with_a_bounded_complete_graph():
    cursors, retained, issue = owners()
    proposed = []
    for _ in range(256):
        subject, grant = value()
        assert cursors.stage(subject, grant)
        proposed.append(receipt(grant, 0, subject=subject))
    result = issue.commit(proposed, expires_at=deadline())
    assert len(result.permits) == cursors.active_subjects == retained.entries == 256
    assert [permit.operation_id for permit in result.permits] == [
        item.reservation.operation_id for item in proposed
    ]
    assert cursors.staged_grants == 0 and cursors.available_permits == 768
    assert cursors.retained_bytes + retained.retained_bytes >= retained_object_bytes(
        (
            cursors._active,
            cursors._staged,
            cursors._retiring,
            cursors._grant_ids,
            retained._values,
            result,
        )
    )


async def test_empty_issue_has_no_side_effect_and_oversized_batch_fails():
    cursors, retained, issue = owners()
    assert issue.commit([], expires_at=deadline()).permits == ()
    _, grant = value()
    with pytest.raises(ValueError, match="256"):
        issue.commit([receipt(grant, 0) for _ in range(257)], expires_at=deadline())
    assert cursors.entries == retained.entries == 0


def test_issue_commit_and_both_store_mutations_have_no_await():
    root = Path(__file__).resolve().parents[1] / "src/billing/accounting/permits"
    for filename, method in (
        ("accounting_local_issue.py", "commit"),
        ("accounting_local_cursors.py", "_commit_issue"),
        ("accounting_local_receipts.py", "_commit_issue"),
    ):
        tree = ast.parse((root / filename).read_text())
        nodes = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == method
        ]
        assert len(nodes) == 1
        assert not any(
            isinstance(node, (ast.Await, ast.Yield, ast.YieldFrom)) for node in ast.walk(nodes[0])
        )
