import json

import pytest

from src.batch.repository import BatchRepository
from src.batch.public_errors import BatchPublicErrorCode
from src.batch.selector_checkpoint import BatchSelectorUnavailable
from src.batch.repositories.selector_repository import BatchSelectorRepository
from src.batch.storage import LocalBatchArtifactStorage
from src.batch.worker import BatchExecutorWorker, BatchWorkerConfig
from src.router.runtime_generation import RoutingRuntimeRouterProvider
from tests import test_batch_db_integration as database_fixtures
from tests.batch import selector_fixtures
from tests.batch.selector_fixtures import answer_calls, selection_calls

pytestmark = pytest.mark.postgres
selected_batch = selector_fixtures.selected_batch


@pytest.fixture
async def batch_db(monkeypatch):
    # Extend the existing dependency fixture's seeding and teardown ownership
    # to the authenticated app principal; do not bypass its real authorization.
    monkeypatch.setattr(
        database_fixtures,
        "_BATCH_TEST_TEAM_ORGANIZATIONS",
        (*database_fixtures._BATCH_TEST_TEAM_ORGANIZATIONS, ("team-default", "org-default")),
    )
    monkeypatch.setattr(
        database_fixtures,
        "_BATCH_TEST_ORGANIZATION_IDS",
        (*database_fixtures._BATCH_TEST_ORGANIZATION_IDS, "org-default"),
    )
    fixture = database_fixtures.batch_db.__wrapped__()
    try:
        yield await anext(fixture)
    finally:
        await fixture.aclose()


@pytest.mark.parametrize("uncertain", [False, True])
async def test_public_upload_to_durable_worker_and_ordered_answer_artifact(
    selected_batch, batch_db, client, monkeypatch, tmp_path, uncertain
):
    h = selected_batch
    monkeypatch.setattr("src.batch.selector_edge.BatchSelectorRepository", BatchSelectorRepository)
    if uncertain:
        write = BatchSelectorRepository.write

        async def crash_after_receipt(self, claim, *, expected, checkpoint, expires_at):
            if expected is not None:
                raise BatchSelectorUnavailable()
            await write(
                self, claim, expected=expected, checkpoint=checkpoint, expires_at=expires_at
            )

        monkeypatch.setattr(BatchSelectorRepository, "write", crash_after_receipt)
    repository = BatchRepository(batch_db)
    storage = LocalBatchArtifactStorage(str(tmp_path / "artifacts"))
    service = database_fixtures._build_cutover_batch_service(repository=repository, storage=storage)
    for owner in (service, service.create_session_service):
        owner.callable_target_grant_service = h.app.state.callable_target_grant_service
        owner.model_group_resolver = RoutingRuntimeRouterProvider(
            h.app.state.routing_runtime_generation_store
        )
    h.app.state.batch_service, h.app.state.batch_repository = service, repository
    headers = {"Authorization": "Bearer sk-test"}
    source = [
        {
            "custom_id": custom_id,
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": h.item(index, content).request_body,
        }
        for index, (custom_id, content) in enumerate(
            [("routine", "routine extraction"), ("complex", "complex reasoning")], 1
        )
    ]
    uploaded = await client.post(
        "/v1/files",
        headers=headers,
        files={
            "file": (
                "input.jsonl",
                "\n".join(json.dumps(row) for row in source),
                "application/jsonl",
            )
        },
    )
    assert uploaded.status_code == 200, uploaded.text
    created = await client.post(
        "/v1/batches",
        headers=headers,
        json={"input_file_id": uploaded.json()["id"], "endpoint": "/v1/chat/completions"},
    )
    assert created.status_code == 200, created.text
    worker = BatchExecutorWorker(
        app=h.app,
        repository=repository,
        storage=storage,
        config=BatchWorkerConfig(worker_id="selector-e2e", worker_concurrency=2, max_attempts=1),
    )
    assert await worker.process_once()
    result = await client.get(f"/v1/batches/{created.json()['id']}", headers=headers)
    assert result.status_code == 200, result.text
    if uncertain:
        assert result.json()["request_counts"]["failed"] == 2
        assert len(selection_calls(h)) == 2 and not answer_calls(h)
        output = await client.get(
            f"/v1/files/{result.json()['error_file_id']}/content", headers=headers
        )
        assert output.status_code == 200, output.text
        errors = [json.loads(line)["error"] for line in output.text.splitlines()]
        code = BatchPublicErrorCode.SELECTOR_CHECKPOINT_UNAVAILABLE
        assert len(errors) == 2
        assert all(
            error["code"] == code.value and error["message"] == code.message for error in errors
        )
        assert "operation_id" not in output.text and "policy_identity" not in output.text
        stored = await repository.list_items(created.json()["id"])
        assert all(item.error_body["code"] == code.value for item in stored)
        return
    assert result.json()["status"] == "completed", result.text
    assert len(selection_calls(h)) == len(answer_calls(h)) == 2
    assert {call["model"] for call in answer_calls(h)} == {"classifier", "quality"}
    output = await client.get(
        f"/v1/files/{result.json()['output_file_id']}/content", headers=headers
    )
    assert output.status_code == 200, output.text
    lines = [json.loads(line) for line in output.text.splitlines()]
    assert [line["custom_id"] for line in lines] == ["routine", "complex"]
    assert all(line["response"]["body"]["usage"]["total_tokens"] == 18 for line in lines)
    assert all(line["error"] is None for line in lines)
    assert "selector_checkpoint" not in output.text and "billing_event_id" not in output.text
    items = await repository.list_items(created.json()["id"])
    assert all(item.selector_checkpoint["decision"] is not None for item in items)
    outboxes = await repository.list_completion_outbox_by_item_ids([item.item_id for item in items])
    assert {row.completion_id for row in outboxes} == {
        item.selector_checkpoint["operation_id"] for item in items
    }
