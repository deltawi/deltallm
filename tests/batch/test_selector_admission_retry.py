from dataclasses import replace

import pytest

from src.billing.operation_reservation import BillingOperationUnavailable
from tests.batch import selector_fixtures
from tests.batch.selector_fixtures import answer_calls, selection_calls

pytestmark = pytest.mark.app
selected_batch = selector_fixtures.selected_batch


async def test_failed_billing_admission_can_retry_without_a_checkpoint(selected_batch):
    h = selected_batch
    admit = h.billing.reserve.side_effect
    attempts = []

    def transient(operation, **kwargs):
        attempts.append(operation)
        if len(attempts) == 1:
            raise BillingOperationUnavailable()
        return admit(operation, **kwargs)

    h.billing.reserve.side_effect = transient
    item = h.item()
    await h.worker._process_item(h.job, item)
    assert item.selector_checkpoint is None
    assert not h.calls and not h.checkpoints.writes
    assert h.repository.failed_calls[0]["retryable"]

    await h.worker._process_item(h.job, replace(item, claim_epoch=1))
    assert len(attempts) == 2
    assert attempts[0].attribution.operation_id == attempts[1].attribution.operation_id
    assert len(selection_calls(h)) == len(answer_calls(h)) == 1
    assert len(h.checkpoints.writes) == 2
    assert len(h.repository.completed_calls) == 1
    h.billing.accept_selector.assert_awaited_once()


async def test_pending_checkpoint_is_written_after_reservation_before_dispatch(
    selected_batch, monkeypatch
):
    h = selected_batch
    write = h.checkpoints.write

    async def persist(*args, **kwargs):
        h.billing.reserve.assert_awaited_once()
        if kwargs["expected"] is None:
            h.billing.dispatch.assert_not_awaited()
            assert not h.calls
        await write(*args, **kwargs)

    monkeypatch.setattr(h.checkpoints, "write", persist)
    await h.worker._process_item(h.job, h.item())
    assert len(h.repository.completed_calls) == 1
