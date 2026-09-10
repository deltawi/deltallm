import asyncio
from dataclasses import dataclass, field
import json

import httpx
import pytest

from src.batch.selector_checkpoint import BatchSelectorUnavailable
from src.batch.worker import BatchExecutorWorker, BatchWorkerConfig
from tests.router.selection.provider_fixtures import response_body
from tests.router.selection.test_realtime import configure
from tests.test_batch_worker import (
    _FailureRepository,
    _FakeStorage,
    _build_chat_batch_job,
    _build_chat_batch_item,
)


class Checkpoints:
    def __init__(self):
        self.rows = {}
        self.writes = []
        self.fail_finish = False

    async def write(self, claim, *, expected, checkpoint, expires_at):
        assert expires_at > asyncio.get_running_loop().time()
        if self.rows.get(claim.item_id) != expected or (self.fail_finish and expected is not None):
            raise BatchSelectorUnavailable()
        self.rows[claim.item_id] = checkpoint
        self.writes.append((claim, checkpoint))


@dataclass
class SelectedBatchHarness:
    app: object
    worker: object
    repository: object
    checkpoints: Checkpoints
    billing: object
    policy: dict
    job: object
    calls: list = field(default_factory=list)
    selector_reply: str | None = None
    selector_error: int | None = None
    answer_error: int | None = None
    selector_gate: asyncio.Event | None = None
    selector_started: asyncio.Event = field(default_factory=asyncio.Event)
    selector_closed: asyncio.Event = field(default_factory=asyncio.Event)
    active: int = 0
    peak: int = 0

    def item(self, index=1, content="hello", **overrides):
        return _build_chat_batch_item(
            f"selected-{index}",
            content,
            request_overrides={"model": self.policy["key"], "max_tokens": 8, **overrides},
        )

    async def provider(self, request):
        data = json.loads(request.content)
        self.calls.append(data)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if data.get("max_tokens") == 64:
                self.selector_started.set()
                try:
                    if self.selector_gate is not None:
                        await self.selector_gate.wait()
                    if self.selector_error is not None:
                        return httpx.Response(
                            self.selector_error, json={"error": {"message": "private"}}
                        )
                    text = self.selector_reply
                    if text is None:
                        text = (
                            '{"lane":"quality"}'
                            if "complex" in json.dumps(data)
                            else '{"lane":"economy"}'
                        )
                    return httpx.Response(200, json=response_body(text=text))
                finally:
                    self.selector_closed.set()
            if self.answer_error is not None:
                return httpx.Response(self.answer_error, json={"error": {"message": "private"}})
            return httpx.Response(200, json=response_body(text="answer"))
        finally:
            self.active -= 1


@pytest.fixture
async def selected_batch(test_app, monkeypatch):
    checkpoints = Checkpoints()
    repository = _FailureRepository()
    repository.prisma = object()
    monkeypatch.setattr("src.batch.selector_edge.BatchSelectorRepository", lambda _: checkpoints)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: harness.provider(request))
    ) as upstream:
        billing, policy = configure(test_app, upstream)
        worker = BatchExecutorWorker(
            app=test_app,
            repository=repository,
            storage=_FakeStorage(),
            config=BatchWorkerConfig(
                worker_id="w1", worker_concurrency=2, heartbeat_interval_seconds=0.01
            ),
        )
        job = _build_chat_batch_job()
        job.model = policy["key"]
        job.created_by_api_key = next(iter(test_app.state._test_repo.records))
        auth = await test_app.state.key_service.get_auth_by_token_hash(job.created_by_api_key)
        job.created_by_user_id = auth.user_id
        job.created_by_team_id = auth.team_id
        job.created_by_organization_id = auth.organization_id
        job.created_by_owner_account_id = auth.owner_account_id
        harness = SelectedBatchHarness(
            test_app, worker, repository, checkpoints, billing, policy, job
        )
        yield harness


def selection_calls(harness):
    return [call for call in harness.calls if call.get("max_tokens") == 64]


def answer_calls(harness):
    return [call for call in harness.calls if call.get("max_tokens") != 64]
