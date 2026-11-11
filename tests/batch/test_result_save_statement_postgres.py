"""A batch result save is one statement: items and outbox rows, together or not at all."""

from __future__ import annotations

import pytest

from src.batch.models import BatchCompletionOutboxCreate, BatchItemCreate
from src.batch.repository import BatchRepository
from tests import test_batch_db_integration as batch_fixtures

pytestmark = pytest.mark.postgres
batch_db = batch_fixtures.batch_db


class _CountingClient:
    """Pass statements through to the real client and count them."""

    def __init__(self, client) -> None:  # noqa: ANN001
        self._client = client
        self.statements = 0
        self.transactions = 0

    async def query_raw(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        self.statements += 1
        return await self._client.query_raw(*args, **kwargs)

    async def execute_raw(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        self.statements += 1
        return await self._client.execute_raw(*args, **kwargs)

    def tx(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        self.transactions += 1
        return self._client.tx(*args, **kwargs)


@pytest.fixture
async def claimed(batch_db):
    repository = BatchRepository(batch_db)
    file_id = await batch_fixtures._seed_batch_file(repository)
    job = await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id=file_id,
        model="m",
        metadata=None,
        created_by_api_key="key-a",
        created_by_user_id=None,
        created_by_team_id=None,
        status="in_progress",
        total_items=3,
    )
    await repository.create_items(
        job.batch_id,
        [
            BatchItemCreate(line_number=line, custom_id=f"c{line}", request_body={"input": "x"})
            for line in range(1, 4)
        ],
    )
    items = await repository.claim_items(batch_id=job.batch_id, worker_id="worker-a", limit=3)
    return job.batch_id, sorted(items, key=lambda item: item.item_id)


def _rows(batch_id: str, items) -> list[dict]:  # noqa: ANN001
    return [
        {
            "item_id": item.item_id,
            "claim_epoch": item.claim_epoch,
            "response_body": {"data": [item.line_number]},
            "usage": {"prompt_tokens": 3},
            "provider_cost": 0.001,
            "billed_cost": 0.002,
            "outbox_payload": {"batch_id": batch_id, "item_id": item.item_id},
        }
        for item in items
    ]


async def _states(batch_db, items) -> dict[str, tuple[str, int]]:  # noqa: ANN001
    rows = await batch_db.query_raw(
        """
        SELECT i.item_id, i.status, COUNT(o.completion_id)::integer AS outbox
        FROM deltallm_batch_item i
        LEFT JOIN deltallm_batch_completion_outbox o ON o.item_id = i.item_id
        WHERE i.item_id = ANY($1::text[])
        GROUP BY i.item_id, i.status
        """,
        [item.item_id for item in items],
    )
    return {row["item_id"]: (row["status"], row["outbox"]) for row in rows}


async def test_save_completes_items_and_outbox_rows_in_one_statement(batch_db, claimed):
    batch_id, items = claimed
    client = _CountingClient(batch_db)
    repository = BatchRepository(client)

    result = await repository.complete_items_with_outbox_bulk(
        items=_rows(batch_id, items), worker_id="worker-a"
    )

    assert result == "completed"
    assert client.statements == 1
    assert client.transactions == 0
    assert await _states(batch_db, items) == {item.item_id: ("completed", 1) for item in items}
    (row,) = await batch_db.query_raw(
        """
        SELECT o.status, o.attempt_count, o.max_attempts, o.payload_json, i.response_body,
               i.locked_by, i.lease_expires_at
        FROM deltallm_batch_completion_outbox o
        JOIN deltallm_batch_item i ON i.item_id = o.item_id
        WHERE o.item_id = $1
        """,
        items[0].item_id,
    )
    assert row["status"] == "queued"
    assert row["attempt_count"] == 0
    assert row["max_attempts"] == 5
    assert row["payload_json"] == {"batch_id": batch_id, "item_id": items[0].item_id}
    assert row["response_body"] == {"data": [items[0].line_number]}
    assert row["locked_by"] is None and row["lease_expires_at"] is None


async def test_retry_after_an_uncertain_commit_finds_its_own_save(batch_db, claimed):
    batch_id, items = claimed
    repository = BatchRepository(batch_db)
    rows = _rows(batch_id, items)
    assert await repository.complete_items_with_outbox_bulk(items=rows, worker_id="worker-a") == (
        "completed"
    )
    assert await repository.complete_items_with_outbox_bulk(items=rows, worker_id="worker-a") == (
        "already_completed"
    )
    assert await _states(batch_db, items) == {item.item_id: ("completed", 1) for item in items}


async def test_one_item_not_owned_saves_none_of_them(batch_db, claimed):
    batch_id, items = claimed
    repository = BatchRepository(batch_db)
    await batch_db.execute_raw(
        "UPDATE deltallm_batch_item SET claim_epoch = claim_epoch + 1 WHERE item_id = $1",
        items[1].item_id,
    )

    result = await repository.complete_items_with_outbox_bulk(
        items=_rows(batch_id, items), worker_id="worker-a"
    )

    assert result == "not_owned"
    assert await _states(batch_db, items) == {item.item_id: ("in_progress", 0) for item in items}


async def test_an_existing_outbox_row_undoes_the_item_update(batch_db, claimed):
    batch_id, items = claimed
    repository = BatchRepository(batch_db)
    # An outbox row that should not exist yet: the save must fail as a whole.
    await repository.enqueue_completion_outbox_many(
        [BatchCompletionOutboxCreate(batch_id=batch_id, item_id=items[2].item_id, payload_json={})]
    )

    with pytest.raises(Exception):
        await repository.complete_items_with_outbox_bulk(
            items=_rows(batch_id, items), worker_id="worker-a"
        )

    assert await _states(batch_db, items) == {
        items[0].item_id: ("in_progress", 0),
        items[1].item_id: ("in_progress", 0),
        items[2].item_id: ("in_progress", 1),
    }
