from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.batch.completion_outbox import BatchCompletionOutboxWorker
from src.batch.models import BatchCompletionOutboxCreate
from src.batch.repositories.completion_outbox_repository import BatchCompletionOutboxRepository
from src.batch.selector_identity import batch_selector_operation_id
from tests.test_batch_completion_outbox import _build_record


@pytest.mark.parametrize("selected", [False, True])
async def test_answer_outbox_preserves_legacy_identity_or_links_selector_parent(selected):
    record = _build_record()
    payload = record.payload_json
    expected = record.completion_id
    if selected:
        expected = str(batch_selector_operation_id(record.batch_id, record.item_id))
        payload["billing_event_id"] = expected
        record.completion_id = expected
    service = SimpleNamespace(log_spend_once=AsyncMock(return_value="duplicate"))
    repository = SimpleNamespace(mark_completion_outbox_sent=AsyncMock(return_value=True))
    worker = BatchCompletionOutboxWorker(app=None, repository=repository)
    assert await worker._record_durable_success_with_dependencies(
        repository=repository, spend_tracking_service=service, record=record, payload=payload
    )
    assert service.log_spend_once.call_args.kwargs["event_id"] == expected
    assert service.log_spend_once.call_args.kwargs["usage"] == payload["usage"]
    repository.mark_completion_outbox_sent.assert_awaited_once()


@pytest.mark.parametrize("bad", ["other-id", 7, "", None])
async def test_outbox_producer_cannot_redirect_answer_spend_identity(bad):
    record = _build_record(payload_overrides={"billing_event_id": bad})
    db = SimpleNamespace(query_raw=AsyncMock())
    with pytest.raises(ValueError, match="billing identity"):
        await BatchCompletionOutboxRepository(db).enqueue_many(
            [BatchCompletionOutboxCreate(record.batch_id, record.item_id, record.payload_json)]
        )
    db.query_raw.assert_not_awaited()


async def test_outbox_producer_freezes_parent_identity_in_the_row_for_old_consumers():
    expected = str(batch_selector_operation_id("b1", "i1"))
    db = SimpleNamespace(query_raw=AsyncMock(return_value=[{"completion_id": expected}]))
    ids = await BatchCompletionOutboxRepository(db).enqueue_many(
        [BatchCompletionOutboxCreate("b1", "i1", {"billing_event_id": expected})]
    )
    assert ids == [expected]
    assert db.query_raw.call_args.args[1] == expected
