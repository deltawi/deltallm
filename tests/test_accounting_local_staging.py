"""Uncommitted funding is not dispatchable or returnable until its owner decides."""

import pytest

from src.billing.accounting.permits.accounting_local_cursors import LocalCursorStore
from tests.test_accounting_local_cursors import value
from tests.test_preissued_permit_bytes import retained_object_bytes


def store(**overrides):
    return LocalCursorStore(
        **{
            "generation": 7,
            "max_entries": 1024,
            "max_retained_bytes": 16 * 1024 * 1024,
            **overrides,
        }
    )


async def test_staging_has_no_dispatch_or_returnable_permits_before_commit():
    subject, grant = value()
    state = store()
    assert state.stage(subject, grant)
    assert state.staged(grant.grant_id).next_ordinal == 0
    assert state.get(subject) is None
    assert state.entries == state.staged_grants == 1
    assert state.available_permits == 0
    assert state.return_candidates() == ()
    charge = state.retained_bytes
    state.activate(grant.grant_id)
    assert state.staged_grants == 0
    assert state.entries == state.active_subjects == 1
    assert state.available_permits == 4
    assert state.retained_bytes == charge


@pytest.mark.parametrize("capacity", ["entries", "bytes"])
async def test_active_staged_and_return_only_proofs_share_one_capacity(capacity):
    subject, grant = value()
    state = store(
        max_entries=2 if capacity == "entries" else 100,
        max_retained_bytes=2 * (subject.retained_bytes + 2048) if capacity == "bytes" else 1048576,
    )
    assert state.add(subject, grant)
    state.advance(subject)
    assert state.stage(*value())
    charge = state.retained_bytes
    assert not state.stage(*value())
    state.abort_staging()
    assert state.entries == 2
    assert state.retiring_grants == 1
    assert state.available_permits == 3
    assert state.retained_bytes == charge
    assert not state.add(*value())
    suffix = state.return_candidates()[0]
    assert suffix.first_unused_ordinal == 0
    assert state.acknowledge_return(suffix, 4)
    assert state.available_permits == 3
    assert state.stage(*value())


async def test_abort_keeps_the_warm_prefix_and_returns_only_new_unused_funding():
    subject, grant = value()
    state = store()
    assert state.add(subject, grant)
    state.advance(subject)
    _, second_grant = value()
    assert state.stage(subject, second_grant)
    charge = state.retained_bytes
    state.abort_staging()
    assert state.get(subject).next_ordinal == 1
    assert state.available_permits == 3
    assert state.retained_bytes == charge
    suffix = state.return_candidates()[0]
    assert suffix.grant == second_grant
    assert suffix.first_unused_ordinal == 0
    assert state.acknowledge_return(suffix, 4)
    assert state.get(subject).next_ordinal == 1


async def test_activation_cannot_replace_a_warm_cursor():
    subject, grant = value()
    state = store()
    assert state.add(subject, grant)
    _, second_grant = value()
    assert state.stage(subject, second_grant)
    with pytest.raises(ValueError, match="cannot replace"):
        state.activate(second_grant.grant_id)
    assert state.staged_grants == state.active_subjects == 1
    for _ in range(4):
        state.advance(subject)
    state.activate(second_grant.grant_id)
    assert state.get(subject).grant == second_grant
    assert state.get(subject).next_ordinal == 0


async def test_abort_never_scans_more_than_256_staged_grants():
    state = store()
    for _ in range(300):
        assert state.stage(*value())
    charge = state.retained_bytes
    state.abort_staging()
    assert state.staged_grants == 44
    assert state.retiring_grants == 256
    assert state.retained_bytes == charge
    state.abort_staging()
    assert state.staged_grants == 0 and state.retiring_grants == 300
    assert state.retained_bytes == charge
    with pytest.raises(ValueError, match="1 to 256"):
        state.abort_staging(limit=257)


@pytest.mark.parametrize("characters", [1, 256])
async def test_complete_four_index_graph_fits_the_retained_charge(characters):
    state = store()
    for _ in range(100):
        subject, grant = value()
        grant = grant.model_copy(
            update={"grant_id": grant.grant_id + "😁" * (characters - len(grant.grant_id))}
        )
        assert state.stage(subject, grant)
    state.abort_staging(limit=30)
    for grant_id in tuple(state._staged)[:30]:
        state.activate(grant_id)
    assert state.entries == 100
    assert state.retained_bytes >= retained_object_bytes(
        (state._staged, state._active, state._retiring, state._grant_ids)
    )


async def test_duplicate_grant_cannot_enter_two_state_indexes():
    subject, grant = value()
    state = store()
    assert state.stage(subject, grant)
    with pytest.raises(ValueError, match="cannot replace"):
        state.stage(subject, grant)
    with pytest.raises(ValueError, match="cannot replace"):
        state.add(subject, grant)
    assert state.entries == 1 and state.staged_grants == 1


@pytest.mark.parametrize("invalid", ["generation", "allowance", "ordinal", "clock"])
async def test_invalid_staged_funding_cannot_change_any_counter(invalid):
    subject, grant = value()
    updates = {
        "generation": {"protocol_generation": 8},
        "allowance": {"allowance": "2"},
        "ordinal": {"operation_limit": 0},
        "clock": {"observed_monotonic": float("nan")},
    }[invalid]
    state = store()
    with pytest.raises(ValueError):
        state.stage(subject, grant.model_copy(update=updates))
    assert state.entries == state.retained_bytes == state.available_permits == 0


async def test_expiry_and_shutdown_scan_do_not_take_staging_from_the_admission_owner():
    subject, grant = value()
    state = store()
    assert state.stage(subject, grant)
    state.prune(now=grant.dispatch_deadline + 1, minimum_validity_seconds=0)
    state.retire_slice()
    assert state.staged_grants == 1
    assert state.retiring_grants == state.active_subjects == 0
    assert state.return_candidates() == ()


async def test_multiple_staged_grants_for_one_subject_keep_distinct_fences():
    subject, first = value()
    _, second = value()
    state = store()
    assert state.stage(subject, first)
    assert state.stage(subject, second)
    assert state.staged(first.grant_id).grant == first
    assert state.staged(second.grant_id).grant == second
    state.abort_staging()
    assert {item.grant.grant_id for item in state.return_candidates()} == {
        first.grant_id,
        second.grant_id,
    }
