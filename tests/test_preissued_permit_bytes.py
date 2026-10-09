"""Retained cursor bytes stay bounded before a durable refill."""

import asyncio
from collections import OrderedDict
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime, timedelta
from sys import getsizeof
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import BaseModel

from src.billing.accounting.accounting_protocol import (
    AccountingScope,
    BudgetWindowRef,
    PreissuedPermitAllocation,
    ReserveDecision,
)
from src.billing.accounting.permits.preissued_permits import PermitSubject, _GrantCursor
from src.metrics.prometheus import get_prometheus_registry
from tests.test_accounting_protocol import reservation
from tests.test_preissued_permit_bank import FakePermitRepository, bank, deadline, fresh


def size(item):
    return PermitSubject.from_reservation(item).retained_bytes


@pytest.mark.parametrize("maximum", [0, -1, 64 * 1024 * 1024 + 1])
def test_byte_capacity_has_explicit_constructor_bounds(maximum):
    with pytest.raises(ValueError, match="byte capacity"):
        bank(FakePermitRepository(), max_retained_bytes=maximum)


async def test_oversized_subject_is_rejected_before_database_allocation():
    repository = FakePermitRepository()
    item = reservation()
    owner = bank(repository, max_retained_bytes=size(item) - 1)
    permits = await owner.reserve_batch([item], expires_at=deadline())
    assert permits[0].decision is ReserveDecision.CAPACITY_EXHAUSTED
    assert repository.refills == repository.claims == []
    assert owner.retained_bytes == owner.active_subjects == owner.available_permits == 0


async def test_exact_byte_limit_keeps_one_live_grant_and_rejects_another():
    repository = FakePermitRepository()
    first = reservation()
    owner = bank(repository, max_retained_bytes=size(first))
    await owner.reserve_batch([first], expires_at=deadline())
    original = repository.claims[0][0].grant
    rejected = await owner.reserve_batch([reservation()], expires_at=deadline())
    assert rejected[0].decision is ReserveDecision.CAPACITY_EXHAUSTED
    assert len(repository.refills) == 1
    assert owner.retained_bytes == size(first)
    await owner.reserve_batch([fresh(first)], expires_at=deadline())
    assert repository.claims[1][0].grant == original
    assert repository.claims[1][0].permit_ordinal == 1
    assert owner.retained_bytes == size(first)
    await owner.close()
    assert owner.retained_bytes == 0


async def test_mixed_batch_skips_an_oversized_subject_without_extra_calls():
    repository = FakePermitRepository()
    small = reservation()
    large = reservation().model_copy(
        update={"attribution": small.attribution.model_copy(update={"model": "m" * 256})}
    )
    owner = bank(repository, max_retained_bytes=size(small))
    permits = await owner.reserve_batch([large, small], expires_at=deadline())
    assert [permit.decision for permit in permits] == [
        ReserveDecision.CAPACITY_EXHAUSTED,
        ReserveDecision.DISPATCH,
    ]
    assert len(repository.refills) == len(repository.claims) == 1
    assert repository.refills[0][0].reservation == small
    assert owner.retained_bytes == size(small)
    await owner.close()


async def test_byte_capacity_bounds_many_short_subjects():
    repository = FakePermitRepository()
    items = [reservation() for _ in range(256)]
    maximum = 3 * size(items[0])
    owner = bank(repository, max_subjects=100_000, max_retained_bytes=maximum)
    permits = await owner.reserve_batch(items, expires_at=deadline())
    assert [permit.decision for permit in permits].count(ReserveDecision.DISPATCH) == 3
    assert owner.active_subjects == 3
    assert owner.retained_bytes == maximum
    assert len(repository.refills) == len(repository.claims) == 1
    assert len(repository.refills[0]) == 3
    await owner.close()


async def test_expiry_releases_the_old_byte_charge_once():
    repository = FakePermitRepository()
    first = reservation()
    owner = bank(repository, max_retained_bytes=size(first))
    await owner.reserve_batch([first], expires_at=deadline())
    cursor = next(iter(owner._cursors.values()))
    cursor.grant = cursor.grant.model_copy(
        update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    second = reservation()
    permits = await owner.reserve_batch([second], expires_at=deadline())
    assert permits[0].decision is ReserveDecision.DISPATCH
    assert owner.retained_bytes == size(second)
    assert owner.active_subjects == 1
    await owner.close()
    await owner.close()
    assert owner.retained_bytes == 0


async def test_consumed_partial_grants_release_bytes_before_the_next_refill():
    repository = FakePermitRepository(grant_limit=1)
    first = reservation()
    owner = bank(repository, max_retained_bytes=size(first))
    permits = await owner.reserve_batch([first, fresh(first)], expires_at=deadline())
    assert all(permit.decision is ReserveDecision.DISPATCH for permit in permits)
    assert len(repository.refills) == 2
    assert len(repository.claims) == 1
    assert owner.retained_bytes == owner.active_subjects == owner.available_permits == 0


@pytest.mark.parametrize("phase", ["allocate_batch", "claim_batch"])
@pytest.mark.parametrize("error", [TimeoutError, asyncio.CancelledError])
async def test_uncertain_ack_releases_touched_cursor_bytes(phase, error):
    repository = FakePermitRepository()
    first = reservation()
    owner = bank(repository, max_retained_bytes=size(first))
    if phase == "claim_batch":
        await owner.reserve_batch([first], expires_at=deadline())
        first = fresh(first)
    setattr(repository, phase, AsyncMock(side_effect=error()))
    with pytest.raises(error):
        await owner.reserve_batch([first], expires_at=deadline())
    assert owner.retained_bytes == owner.active_subjects == owner.available_permits == 0


def test_byte_charge_covers_unicode_without_retaining_audit_or_pricing_payloads():
    item = reservation()
    large = item.model_copy(
        update={
            "pricing_snapshot": {"value": "p" * 32_000},
            "audit_envelope": {"value": "a" * 64_000},
        }
    )
    assert size(large) == size(item)
    unicode_item = item.model_copy(
        update={"attribution": item.attribution.model_copy(update={"model": "😀" * 256})}
    )
    assert size(unicode_item) - size(item) == 4 * (256 - len(item.attribution.model))
    subject = PermitSubject.from_reservation(unicode_item)
    assert not hasattr(subject, "audit_envelope")
    assert not hasattr(subject, "pricing_snapshot")
    assert "😀" not in repr(subject)


async def test_retained_byte_metric_uses_only_the_fixed_lane_label():
    repository = FakePermitRepository()
    owner = bank(repository, lane=62)
    item = reservation()
    await owner.reserve_batch([item], expires_at=deadline())
    metric = "deltallm_accounting_permit_bank_retained_bytes"
    assert get_prometheus_registry().get_sample_value(metric, {"lane": "62"}) == size(item)
    await owner.close()
    assert get_prometheus_registry().get_sample_value(metric, {"lane": "62"}) == 0


@pytest.mark.parametrize("windows", [0, 1, 5])
@pytest.mark.parametrize("characters", [1, 256])
async def test_byte_charge_covers_the_typed_retained_object_graph(windows, characters):
    item = reservation()
    names = ("api_key", "user_id", "team_id", "organization_id", "model")
    item = item.model_copy(
        update={
            "attribution": item.attribution.model_copy(
                update={name: chr(0x1F600 + index) * characters for index, name in enumerate(names)}
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
    allocation = PreissuedPermitAllocation(
        reservation=item, fence_token=uuid4(), target_operations=1024
    )
    grants = await FakePermitRepository().allocate_batch([allocation], expires_at=deadline())
    grant = grants[0].model_copy(update={"grant_id": "😁" * 256, "grantee_id": "😂" * 256})
    subject = PermitSubject.from_reservation(item)
    cursor = _GrantCursor(grant, subject.retained_bytes)
    retained = OrderedDict([(subject, cursor)])
    assert subject.retained_bytes >= retained_object_bytes(retained)


def retained_object_bytes(root):
    pending, visited, total = [root], set(), 0
    while pending:
        value = pending.pop()
        if id(value) in visited:
            continue
        visited.add(id(value))
        total += getsizeof(value)
        if isinstance(value, BaseModel):
            pending.extend(
                (
                    value.__dict__,
                    value.__pydantic_fields_set__,
                    value.__pydantic_extra__,
                    value.__pydantic_private__,
                )
            )
        elif is_dataclass(value):
            pending.extend(getattr(value, field.name) for field in fields(value))
        elif isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, (tuple, list, set)):
            pending.extend(value)
    return total
