"""Active and return-only cursors share finite entry and byte limits."""

from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest

from src.billing.accounting.permits.accounting_local_cursors import LocalCursorStore
from src.billing.accounting.permits.accounting_local_leases import (
    LocalPermitGrant,
    LocalPermitReturn,
)
from src.billing.accounting.accounting_protocol import AccountingScope, BudgetWindowRef
from src.billing.accounting.permits.preissued_permits import PermitSubject
from tests.test_accounting_local_leases import terminal
from tests.test_preissued_permit_bytes import retained_object_bytes


def value():
    receipt = terminal().receipt
    return PermitSubject.from_reservation(receipt.reservation), receipt.grant


def store(**overrides):
    return LocalCursorStore(
        **{
            "generation": 7,
            "max_entries": 1024,
            "max_retained_bytes": 16 * 1024 * 1024,
            **overrides,
        }
    )


async def test_retiring_capacity_keeps_its_entry_and_byte_charge_until_exact_ack():
    subject, grant = value()
    owner = store(max_entries=1)
    assert owner.add(subject, grant)
    charge = owner.retained_bytes
    owner.advance(subject)
    assert owner.retire(subject)
    assert not owner.retire(subject)
    assert owner.entries == owner.retiring_grants == 1
    assert owner.active_subjects == owner.available_permits == 0
    assert owner.retained_bytes == charge
    assert not owner.add(*value())
    returns = owner.return_candidates()
    assert returns[0].first_unused_ordinal == 1
    assert owner.acknowledge_return(returns[0], 3)
    assert not owner.acknowledge_return(returns[0], 3)
    assert owner.entries == owner.retained_bytes == 0


async def test_returning_suffix_keeps_capacity_on_wrong_fence_ordinal_and_count():
    subject, grant = value()
    owner = store()
    assert owner.add(subject, grant)
    owner.advance(subject)
    owner.retire(subject)
    valid = owner.return_candidates()[0]
    changed = LocalPermitReturn(
        grant=grant.model_copy(update={"fence_token": uuid4()}), first_unused_ordinal=1
    )
    for returned, count in (
        (changed, 3),
        (LocalPermitReturn(grant=grant, first_unused_ordinal=2), 2),
        (valid, 2),
        (valid, True),
    ):
        with pytest.raises(ValueError, match="does not match"):
            owner.acknowledge_return(returned, count)
    assert owner.entries == owner.retiring_grants == 1
    assert owner.return_candidates() == (valid,)


async def test_exhausted_cursor_needs_no_unused_suffix_return():
    subject, grant = value()
    owner = store()
    assert owner.add(subject, grant)
    for _ in range(4):
        owner.advance(subject)
    assert owner.entries == owner.retained_bytes == owner.available_permits == 0
    assert owner.return_candidates() == ()


@pytest.mark.parametrize("characters", [1, 256])
@pytest.mark.parametrize("windows", [0, 1, 5])
async def test_cursor_byte_charge_covers_active_and_retiring_graphs(characters, windows):
    receipt = terminal().receipt
    subject = PermitSubject.from_reservation(
        receipt.reservation.model_copy(
            update={
                "attribution": receipt.reservation.attribution.model_copy(
                    update={
                        name: chr(0x1F600 + index) * characters
                        for index, name in enumerate(
                            ("api_key", "user_id", "team_id", "organization_id", "model")
                        )
                    }
                ),
                "windows": tuple(
                    BudgetWindowRef(
                        window_id=uuid4(),
                        scope_type=scope,
                        scope_id=chr(0x1F620 + index) * characters,
                        policy_generation=2**63 - 1,
                    )
                    for index, scope in enumerate(tuple(AccountingScope)[:windows])
                ),
            }
        )
    )
    grant = receipt.grant.model_copy(
        update={"grant_id": "😁" * characters, "grantee_id": "😂" * characters}
    )
    owner = store()
    assert owner.add(subject, grant)
    assert owner.retained_bytes >= retained_object_bytes(
        (owner._active, owner._retiring, owner._grant_ids)
    )
    owner.retire(subject)
    assert owner.retained_bytes >= retained_object_bytes(
        (owner._active, owner._retiring, owner._grant_ids)
    )


async def test_expiry_moves_proof_to_return_only_without_evicting_or_refunding_it():
    subject, grant = value()
    owner = store()
    assert owner.add(subject, grant)
    charge = owner.retained_bytes
    owner.prune(now=grant.dispatch_deadline + 1, minimum_validity_seconds=0.1)
    assert owner.get(subject) is None
    assert owner.entries == owner.retiring_grants == 1
    assert owner.retained_bytes == charge
    assert owner.return_candidates()[0].first_unused_ordinal == 0


async def test_close_and_return_scans_have_a_fixed_256_item_bound():
    owner = store(max_entries=1000)
    for _ in range(300):
        assert owner.add(*value())
    owner.retire_slice()
    assert owner.active_subjects == 44
    assert owner.retiring_grants == 256
    assert len(owner.return_candidates()) == 256
    owner.retire_slice()
    assert owner.retiring_grants == 300
    for method in (owner.retire_slice, owner.return_candidates):
        with pytest.raises(ValueError, match="1 to 256"):
            method(limit=257)


async def test_second_subject_cannot_overwrite_the_same_grant_id():
    subject, grant = value()
    owner = store()
    assert owner.add(subject, grant)
    other, _ = value()
    with pytest.raises(ValueError, match="cannot replace"):
        owner.add(other, grant)
    assert owner.entries == 1


async def test_retired_cursor_ordinal_cannot_be_changed_through_a_borrowed_reference():
    subject, grant = value()
    owner = store()
    assert owner.add(subject, grant)
    reference = owner.get(subject)
    owner.advance(subject)
    owner.retire(subject)
    with pytest.raises(FrozenInstanceError):
        reference.next_ordinal = 0
    assert owner.return_candidates()[0].first_unused_ordinal == 1


async def test_byte_capacity_includes_retiring_proofs_without_eviction():
    subject, grant = value()
    owner = store(max_retained_bytes=subject.retained_bytes + 2048)
    assert owner.add(subject, grant)
    owner.retire(subject)
    assert not owner.add(*value())
    assert owner.retiring_grants == 1
    assert owner.retained_bytes == subject.retained_bytes + 2048


@pytest.mark.parametrize(
    "field,changed_value",
    [
        ("protocol_generation", 8),
        ("allowance", "2"),
    ],
)
async def test_mismatched_funding_does_not_enter_retained_state(field, changed_value):
    subject, grant = value()
    owner = store()
    with pytest.raises(ValueError, match="funding identity"):
        owner.add(subject, grant.model_copy(update={field: changed_value}))
    assert owner.entries == owner.retained_bytes == owner.available_permits == 0


async def test_prune_rotates_at_most_256_live_cursors(monkeypatch):
    owner = store()
    for _ in range(300):
        assert owner.add(*value())
    observed = []
    original = LocalPermitGrant.dispatch_deadline.fget

    def counted(grant):
        observed.append(grant.grant_id)
        return original(grant)

    monkeypatch.setattr(LocalPermitGrant, "dispatch_deadline", property(counted))
    owner.prune(now=0, minimum_validity_seconds=0.1)
    assert len(observed) == 256
    assert owner.active_subjects == 300
    assert owner.retiring_grants == 0


@pytest.mark.parametrize(
    "parameter,changed_value",
    [
        ("generation", 0),
        ("max_entries", 0),
        ("max_entries", 100001),
        ("max_retained_bytes", 0),
        ("max_retained_bytes", 64 * 1024 * 1024 + 1),
    ],
)
async def test_cursor_constructor_has_fixed_caps(parameter, changed_value):
    with pytest.raises(ValueError):
        store(**{parameter: changed_value})


@pytest.mark.parametrize(
    "now,validity", [(float("nan"), 0), (float("inf"), 0), (-1, 0), (0, -1), (0, 6)]
)
async def test_invalid_scan_clock_cannot_change_retained_capacity(now, validity):
    subject, grant = value()
    owner = store()
    assert owner.add(subject, grant)
    with pytest.raises(ValueError, match="scan clock"):
        owner.prune(now=now, minimum_validity_seconds=validity)
    assert owner.active_subjects == 1
    assert owner.retiring_grants == 0
    assert owner.available_permits == 4
