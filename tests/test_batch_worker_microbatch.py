from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from types import SimpleNamespace

import httpx
import pytest

from src.batch.embedding_microbatch import (
    allocate_embedding_usage,
    build_embedding_execution_signature,
    classify_embedding_microbatch_request,
    resolve_effective_upstream_max_batch_inputs,
)
from src.batch.models import BatchItemRecord, BatchJobRecord, BatchJobStatus
from src.batch.worker import BatchExecutorWorker, BatchWorkerConfig
from src.models.errors import BudgetExceededError, ModelNotFoundError, RateLimitError, ServiceUnavailableError
from src.models.requests import EmbeddingRequest


class _Repo:
    def __init__(self) -> None:
        self.completed_calls: list[dict] = []
        self.completed_bulk_calls: list[dict] = []
        self.completion_outbox_calls: list[dict] = []
        self.failed_calls: list[dict] = []
        self.renew_calls: list[dict] = []
        self.release_for_retry_calls: list[dict] = []

    async def complete_items_with_outbox_bulk(self, **kwargs) -> str:
        self.completed_bulk_calls.append(kwargs)
        for item in kwargs["items"]:
            self.completed_calls.append(
                {
                    "item_id": item["item_id"],
                    "response_body": item["response_body"],
                    "usage": item["usage"],
                    "provider_cost": item["provider_cost"],
                    "billed_cost": item["billed_cost"],
                }
            )
            self.completion_outbox_calls.append(dict(item["outbox_payload"]))
        return "completed"

    async def mark_item_failed(self, **kwargs) -> bool:
        self.failed_calls.append(kwargs)
        return True

    async def renew_item_lease(self, **kwargs) -> bool:
        self.renew_calls.append(kwargs)
        return True

    async def release_items_for_retry(self, **kwargs) -> list[str]:
        self.release_for_retry_calls.append(kwargs)
        return list(kwargs["item_ids"])

    async def refresh_job_progress(self, batch_id: str):
        del batch_id
        return None


class _Storage:
    pass


class _BudgetService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def check_budgets(self, **kwargs) -> None:
        self.calls.append(kwargs)


class _Router:
    def __init__(self, deployment, *, inject_route_policy: bool = False) -> None:
        self.deployment = deployment
        self.inject_route_policy = inject_route_policy
        self.select_calls: list[dict] = []

    def resolve_model_group(self, model: str) -> str:
        return f"group:{model}"

    async def select_deployment(self, model_group: str, request_context: dict) -> object:
        self.select_calls.append({"model_group": model_group, "request_context": dict(request_context)})
        if self.inject_route_policy:
            request_context["route_policy"] = {
                "timeout_seconds": 12.5,
                "retry_max_attempts": 3,
                "retryable_error_classes": ["timeout", "rate_limit"],
            }
        return self.deployment

    def require_deployment(self, model_group: str, deployment: object):
        assert model_group.startswith("group:")
        return deployment


class _Failover:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute_with_failover(
        self,
        *,
        primary_deployment,
        model_group,
        execute,
        return_deployment=False,
        **kwargs,
    ):
        self.calls.append(
            {
                "primary_deployment": primary_deployment,
                "model_group": model_group,
                "return_deployment": return_deployment,
                "kwargs": kwargs,
            }
        )
        data = await execute(primary_deployment)
        if return_deployment:
            return data, primary_deployment
        return data


def _build_job() -> BatchJobRecord:
    now = datetime.now(tz=UTC)
    return BatchJobRecord(
        batch_id="b1",
        endpoint="/v1/embeddings",
        status=BatchJobStatus.IN_PROGRESS,
        execution_mode="managed_internal",
        input_file_id="f1",
        output_file_id=None,
        error_file_id=None,
        model="text-embedding-3-small",
        metadata={},
        provider_batch_id=None,
        provider_status=None,
        provider_error=None,
        provider_last_sync_at=None,
        total_items=1,
        in_progress_items=1,
        completed_items=0,
        failed_items=0,
        cancelled_items=0,
        locked_by=None,
        lease_expires_at=None,
        cancel_requested_at=None,
        status_last_updated_at=now,
        created_by_api_key=None,
        created_by_user_id="user-1",
        created_by_team_id="team-1",
        created_at=now,
        started_at=now,
        completed_at=None,
        expires_at=None,
        created_by_organization_id="org-1",
    )


def _build_item(*, item_id: str, input_value, request_overrides: dict | None = None) -> BatchItemRecord:
    now = datetime.now(tz=UTC)
    request_body = {"model": "text-embedding-3-small", "input": input_value}
    if request_overrides:
        request_body.update(request_overrides)
    return BatchItemRecord(
        item_id=item_id,
        batch_id="b1",
        line_number=1,
        custom_id=f"custom-{item_id}",
        status="in_progress",
        request_body=request_body,
        response_body=None,
        error_body=None,
        usage=None,
        provider_cost=0.0,
        billed_cost=0.0,
        attempts=0,
        last_error=None,
        locked_by="w1",
        lease_expires_at=now,
        created_at=now,
        started_at=now,
        completed_at=None,
    )


def _build_worker(*, deployment_model_info: dict | None = None, inject_route_policy: bool = False):
    deployment = SimpleNamespace(
        deployment_id="dep-1",
        deltallm_params={"model": "openai/text-embedding-3-small", "api_base": "http://localhost:9090/v1"},
        input_cost_per_token=0.001,
        output_cost_per_token=0.0,
        model_info=dict(deployment_model_info or {}),
    )
    repo = _Repo()
    budget = _BudgetService()
    router = _Router(deployment, inject_route_policy=inject_route_policy)
    failover = _Failover()
    app = SimpleNamespace(
        state=SimpleNamespace(
            router=router,
            failover_manager=failover,
            budget_service=budget,
            spend_tracking_service=None,
            passive_health_tracker=None,
            router_state_backend=None,
            settings=SimpleNamespace(openai_base_url="http://localhost:9090/v1"),
            http_client=None,
        )
    )
    worker = BatchExecutorWorker(
        app=app,
        repository=repo,  # type: ignore[arg-type]
        storage=_Storage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )
    return worker, repo, budget, router, failover


@pytest.mark.asyncio
async def test_batch_retries_no_healthy_deployments_with_backoff(monkeypatch):
    worker, repo, _, _, _ = _build_worker()
    worker.config.max_attempts = 5

    monkeypatch.setattr("src.batch.worker_execution.random.randint", lambda lower, upper: upper)

    item = _build_item(item_id="i1", input_value="hello")
    item.attempts = 1

    await worker._execution_engine._mark_item_failed(
        job=_build_job(),
        item=item,
        model_name=item.request_body["model"],
        exc=ModelNotFoundError(message="No healthy deployments available for model 'text-embedding-3-small'"),
        deployment_id=None,
        started_at_monotonic=0.0,
    )

    assert repo.failed_calls == [
        {
            "item_id": "i1",
            "worker_id": "w1",
            "error_body": {
                "message": "No healthy deployments available for model 'text-embedding-3-small'",
                "type": "ModelNotFoundError",
            },
            "last_error": "No healthy deployments available for model 'text-embedding-3-small'",
            "retryable": True,
            "retry_delay_seconds": 5,
        }
    ]


@pytest.mark.asyncio
async def test_batch_does_not_retry_regular_model_not_found_errors():
    worker, repo, _, _, _ = _build_worker()
    worker.config.max_attempts = 5

    item = _build_item(item_id="i1", input_value="hello")
    item.attempts = 1

    await worker._execution_engine._mark_item_failed(
        job=_build_job(),
        item=item,
        model_name=item.request_body["model"],
        exc=ModelNotFoundError(message="Model not found"),
        deployment_id=None,
        started_at_monotonic=0.0,
    )

    assert repo.failed_calls[0]["retryable"] is False
    assert repo.failed_calls[0]["retry_delay_seconds"] == 0


@pytest.mark.asyncio
async def test_batch_does_not_retry_budget_exceeded_errors():
    worker, repo, _, _, _ = _build_worker()
    worker.config.max_attempts = 5

    item = _build_item(item_id="i1", input_value="hello")
    item.attempts = 1

    await worker._execution_engine._mark_item_failed(
        job=_build_job(),
        item=item,
        model_name=item.request_body["model"],
        exc=BudgetExceededError(message="Budget exceeded"),
        deployment_id=None,
        started_at_monotonic=0.0,
    )

    assert repo.failed_calls[0]["retryable"] is False
    assert repo.failed_calls[0]["retry_delay_seconds"] == 0


@pytest.mark.asyncio
async def test_batch_retry_delay_caps_retry_after_header(monkeypatch):
    worker, repo, _, _, _ = _build_worker()
    worker.config.max_attempts = 5
    worker.config.retry_max_seconds = 60

    monkeypatch.setattr("src.batch.worker_execution.random.randint", lambda lower, upper: upper)

    request = httpx.Request("POST", "http://localhost:9090/v1/embeddings")
    response = httpx.Response(429, headers={"Retry-After": "1200"}, request=request)
    error = httpx.HTTPStatusError("rate limited", request=request, response=response)

    item = _build_item(item_id="i1", input_value="hello")
    item.attempts = 1

    await worker._execution_engine._mark_item_failed(
        job=_build_job(),
        item=item,
        model_name=item.request_body["model"],
        exc=error,
        deployment_id=None,
        started_at_monotonic=0.0,
    )

    assert repo.failed_calls[0]["retryable"] is True
    assert repo.failed_calls[0]["retry_delay_seconds"] == 60


@pytest.mark.asyncio
async def test_batch_retry_delay_caps_rate_limit_retry_after(monkeypatch):
    worker, repo, _, _, _ = _build_worker()
    worker.config.max_attempts = 5
    worker.config.retry_max_seconds = 45

    monkeypatch.setattr("src.batch.worker_execution.random.randint", lambda lower, upper: upper)

    item = _build_item(item_id="i1", input_value="hello")
    item.attempts = 1

    await worker._execution_engine._mark_item_failed(
        job=_build_job(),
        item=item,
        model_name=item.request_body["model"],
        exc=RateLimitError(message="rate limited", retry_after=300),
        deployment_id=None,
        started_at_monotonic=0.0,
    )

    assert repo.failed_calls[0]["retryable"] is True
    assert repo.failed_calls[0]["retry_delay_seconds"] == 45


@pytest.mark.asyncio
async def test_batch_retry_delay_respects_config_without_jitter():
    worker, repo, _, _, _ = _build_worker()
    worker.config.max_attempts = 5
    worker.config.retry_jitter = False
    worker.config.retry_initial_seconds = 5
    worker.config.retry_multiplier = 2.0
    worker.config.retry_max_seconds = 300

    item = _build_item(item_id="i1", input_value="hello")
    item.attempts = 3

    await worker._execution_engine._mark_item_failed(
        job=_build_job(),
        item=item,
        model_name=item.request_body["model"],
        exc=ServiceUnavailableError(message="upstream overloaded"),
        deployment_id=None,
        started_at_monotonic=0.0,
    )

    assert repo.failed_calls[0]["retryable"] is True
    assert repo.failed_calls[0]["retry_delay_seconds"] == 20


def test_resolve_effective_upstream_max_batch_inputs_defaults_to_one():
    assert resolve_effective_upstream_max_batch_inputs(None) == 1
    assert resolve_effective_upstream_max_batch_inputs({}) == 1
    assert resolve_effective_upstream_max_batch_inputs({"upstream_max_batch_inputs": 1}) == 1
    assert resolve_effective_upstream_max_batch_inputs({"upstream_max_batch_inputs": 8}) == 8


@pytest.mark.parametrize(
    ("input_value", "expected_eligible", "expected_reason"),
    [
        ("hello", True, None),
        ([1, 2, 3], True, None),
        (["hello", "world"], False, "multi_input_text_array"),
        ([[1, 2, 3], [4, 5, 6]], False, "multi_input_token_array"),
    ],
)
def test_classify_embedding_microbatch_request(input_value, expected_eligible: bool, expected_reason: str | None):
    payload = EmbeddingRequest.model_validate({"model": "text-embedding-3-small", "input": input_value})
    _, eligible, reason = classify_embedding_microbatch_request(payload)
    assert eligible is expected_eligible
    assert reason == expected_reason


def test_execution_signature_distinguishes_incompatible_requests():
    base_payload = EmbeddingRequest.model_validate(
        {
            "model": "text-embedding-3-small",
            "input": "hello",
            "encoding_format": "float",
            "dimensions": 256,
            "user": "user-1",
        }
    )
    base = build_embedding_execution_signature(
        payload=base_payload,
        model_group="group:text-embedding-3-small",
        primary_deployment_id="dep-1",
        input_kind="string",
    )

    assert base != build_embedding_execution_signature(
        payload=base_payload,
        model_group="group:text-embedding-3-small",
        primary_deployment_id="dep-2",
        input_kind="string",
    )
    assert base != build_embedding_execution_signature(
        payload=EmbeddingRequest.model_validate(
            {
                "model": "text-embedding-3-small",
                "input": "hello",
                "encoding_format": "float",
                "dimensions": 512,
                "user": "user-1",
            }
        ),
        model_group="group:text-embedding-3-small",
        primary_deployment_id="dep-1",
        input_kind="string",
    )
    assert base != build_embedding_execution_signature(
        payload=EmbeddingRequest.model_validate(
            {
                "model": "text-embedding-3-small",
                "input": "hello",
                "encoding_format": "base64",
                "dimensions": 256,
                "user": "user-1",
            }
        ),
        model_group="group:text-embedding-3-small",
        primary_deployment_id="dep-1",
        input_kind="string",
    )
    assert base != build_embedding_execution_signature(
        payload=EmbeddingRequest.model_validate(
            {
                "model": "text-embedding-3-small",
                "input": "hello",
                "encoding_format": "float",
                "dimensions": 256,
                "user": "user-2",
            }
        ),
        model_group="group:text-embedding-3-small",
        primary_deployment_id="dep-1",
        input_kind="string",
    )


def test_allocate_embedding_usage_evenly():
    allocations = allocate_embedding_usage(
        {"prompt_tokens": 6, "total_tokens": 6},
        item_weights=[1, 1],
    )

    assert allocations == [
        {"prompt_tokens": 3, "completion_tokens": 0, "total_tokens": 3},
        {"prompt_tokens": 3, "completion_tokens": 0, "total_tokens": 3},
    ]


def test_allocate_embedding_usage_with_remainder_is_stable():
    allocations = allocate_embedding_usage(
        {"prompt_tokens": 5, "total_tokens": 5},
        item_weights=[1, 2],
    )

    assert allocations == [
        {"prompt_tokens": 2, "completion_tokens": 0, "total_tokens": 2},
        {"prompt_tokens": 3, "completion_tokens": 0, "total_tokens": 3},
    ]


def test_allocate_embedding_usage_handles_zero_usage():
    allocations = allocate_embedding_usage(
        {"prompt_tokens": 0, "total_tokens": 0},
        item_weights=[1, 3],
    )

    assert allocations == [
        {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    ]


def test_allocate_embedding_usage_allocates_cached_prompt_tokens_without_exceeding_prompt_tokens():
    allocations = allocate_embedding_usage(
        {"prompt_tokens": 9, "prompt_tokens_cached": 4, "total_tokens": 9},
        item_weights=[1, 2],
    )

    assert allocations == [
        {"prompt_tokens": 3, "prompt_tokens_cached": 1, "completion_tokens": 0, "total_tokens": 3},
        {"prompt_tokens": 6, "prompt_tokens_cached": 3, "completion_tokens": 0, "total_tokens": 6},
    ]


@pytest.mark.asyncio
async def test_prepare_item_for_execution_returns_reusable_metadata_without_execution():
    worker, repo, budget, router, failover = _build_worker(
        deployment_model_info={"upstream_max_batch_inputs": 8},
        inject_route_policy=True,
    )

    prepared = await worker._prepare_item_for_execution(_build_job(), _build_item(item_id="i1", input_value="hello"))

    assert prepared.model_name == "text-embedding-3-small"
    assert prepared.model_group == "group:text-embedding-3-small"
    assert prepared.effective_upstream_max_batch_inputs == 8
    assert prepared.microbatch_eligible is True
    assert prepared.microbatch_ineligible_reason is None
    assert prepared.execution_signature.primary_deployment_id == "dep-1"
    assert prepared.failover_kwargs == {
        "timeout_seconds": 12.5,
        "retry_max_attempts": 3,
        "retryable_error_classes": ["timeout", "rate_limit"],
    }
    assert prepared.request_context["user_id"] == "batch-worker"
    assert budget.calls == [
        {
            "api_key": None,
            "user_id": "user-1",
            "team_id": "team-1",
            "organization_id": "org-1",
            "model": "text-embedding-3-small",
        }
    ]
    assert len(router.select_calls) == 1
    assert repo.completed_calls == []
    assert repo.failed_calls == []
    assert failover.calls == []


@pytest.mark.asyncio
async def test_worker_process_items_uses_worker_prepare_override(monkeypatch):
    async def _fake_execute_embedding(request, payload, deployment):
        del request, deployment
        return {
            "object": "list",
            "data": [{"index": 0, "embedding": [0.1]}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
        }

    monkeypatch.setattr("src.batch.worker._execute_embedding", _fake_execute_embedding)

    worker, repo, _, _, failover = _build_worker(deployment_model_info={"upstream_max_batch_inputs": 1})
    worker.config.worker_concurrency = 1

    prepare_calls: list[str] = []
    original_prepare_item = worker._prepare_item_for_execution

    async def _prepare_item_override(job, item):  # noqa: ANN001
        prepare_calls.append(item.item_id)
        return await original_prepare_item(job, item)

    worker._prepare_item_for_execution = _prepare_item_override  # type: ignore[assignment]

    await worker._process_items(
        _build_job(),
        [
            _build_item(item_id="i1", input_value="hello"),
            _build_item(item_id="i2", input_value="world"),
        ],
    )

    assert prepare_calls == ["i1", "i2"]
    assert len(repo.completed_calls) == 2
    assert repo.failed_calls == []
    assert len(failover.calls) == 2


@pytest.mark.asyncio
async def test_worker_groups_eligible_items_into_upstream_microbatch_chunks(monkeypatch):
    execute_inputs: list[object] = []

    async def _fake_execute_embedding(request, payload, deployment):
        del request, deployment
        execute_inputs.append(payload.input)
        inputs = payload.input if isinstance(payload.input, list) else [payload.input]
        return {
            "object": "list",
            "data": [{"index": index, "embedding": [0.1 + index]} for index, _ in enumerate(inputs)],
            "usage": {
                "prompt_tokens": 5 * len(inputs),
                "completion_tokens": 0,
                "total_tokens": 5 * len(inputs),
            },
        }

    monkeypatch.setattr("src.batch.worker._execute_embedding", _fake_execute_embedding)

    worker, repo, budget, _, failover = _build_worker(deployment_model_info={"upstream_max_batch_inputs": 2})
    worker.config.worker_concurrency = 1

    await worker._process_items(
        _build_job(),
        [
            _build_item(item_id="i1", input_value="hello-1"),
            _build_item(item_id="i2", input_value="hello-2"),
            _build_item(item_id="i3", input_value="hello-3"),
            _build_item(item_id="i4", input_value="hello-4"),
            _build_item(item_id="i5", input_value="hello-5"),
        ],
    )

    assert execute_inputs == [
        ["hello-1", "hello-2"],
        ["hello-3", "hello-4"],
        "hello-5",
    ]
    assert len(failover.calls) == 3
    assert len(repo.completed_calls) == 5
    assert repo.failed_calls == []
    assert len(budget.calls) == 5


@pytest.mark.asyncio
async def test_worker_keeps_single_prepare_behavior_when_microbatching_is_disabled(monkeypatch):
    execute_inputs: list[object] = []

    async def _fake_execute_embedding(request, payload, deployment):
        del request, deployment
        execute_inputs.append(payload.input)
        return {
            "object": "list",
            "data": [{"index": 0, "embedding": [0.1]}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
        }

    monkeypatch.setattr("src.batch.worker._execute_embedding", _fake_execute_embedding)

    worker, repo, budget, router, failover = _build_worker(deployment_model_info={"upstream_max_batch_inputs": 1})
    worker.config.worker_concurrency = 1

    await worker._process_items(
        _build_job(),
        [
            _build_item(item_id="i1", input_value="hello"),
            _build_item(item_id="i2", input_value="world"),
        ],
    )

    assert execute_inputs == ["hello", "world"]
    assert len(failover.calls) == 2
    assert len(repo.completed_calls) == 2
    assert repo.failed_calls == []
    assert len(budget.calls) == 2
    assert len(router.select_calls) == 2


@pytest.mark.asyncio
async def test_worker_keeps_ineligible_item_shapes_on_single_item_execution(monkeypatch):
    execute_inputs: list[object] = []

    async def _fake_execute_embedding(request, payload, deployment):
        del request, deployment
        execute_inputs.append(payload.input)
        return {
            "object": "list",
            "data": [{"index": 0, "embedding": [0.1]}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
        }

    monkeypatch.setattr("src.batch.worker._execute_embedding", _fake_execute_embedding)

    worker, repo, _, _, failover = _build_worker(deployment_model_info={"upstream_max_batch_inputs": 4})
    worker.config.worker_concurrency = 1

    await worker._process_items(
        _build_job(),
        [
            _build_item(item_id="i1", input_value=["hello", "world"]),
            _build_item(item_id="i2", input_value=[[1, 2, 3], [4, 5, 6]]),
        ],
    )

    assert execute_inputs == [
        ["hello", "world"],
        [[1, 2, 3], [4, 5, 6]],
    ]
    assert len(failover.calls) == 2
    assert len(repo.completed_calls) == 2
    assert repo.failed_calls == []


@pytest.mark.asyncio
async def test_worker_fans_out_grouped_embedding_responses_into_single_item_rows(monkeypatch):
    async def _fake_execute_embedding(request, payload, deployment):
        del request, deployment
        assert payload.input == ["aaaa", "bbbb"]
        return {
            "object": "list",
            "data": [
                {"index": 1, "embedding": [0.3, 0.4]},
                {"index": 0, "embedding": [0.1, 0.2]},
            ],
            "usage": {"prompt_tokens": 8, "total_tokens": 8},
        }

    monkeypatch.setattr("src.batch.worker._execute_embedding", _fake_execute_embedding)

    worker, repo, _, _, _ = _build_worker(deployment_model_info={"upstream_max_batch_inputs": 4})
    worker.config.worker_concurrency = 1

    await worker._process_items(
        _build_job(),
        [
            _build_item(item_id="i1", input_value="aaaa"),
            _build_item(item_id="i2", input_value="bbbb"),
        ],
    )

    assert len(repo.completed_calls) == 2
    first, second = repo.completed_calls
    assert first["response_body"] == {
        "object": "list",
        "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
        "model": "openai/text-embedding-3-small",
        "usage": {"prompt_tokens": 4, "completion_tokens": 0, "total_tokens": 4},
        "_provider": "openai",
    }
    assert second["response_body"] == {
        "object": "list",
        "data": [{"object": "embedding", "index": 0, "embedding": [0.3, 0.4]}],
        "model": "openai/text-embedding-3-small",
        "usage": {"prompt_tokens": 4, "completion_tokens": 0, "total_tokens": 4},
        "_provider": "openai",
    }
    assert first["usage"] == {"prompt_tokens": 4, "completion_tokens": 0, "total_tokens": 4}
    assert second["usage"] == {"prompt_tokens": 4, "completion_tokens": 0, "total_tokens": 4}

    artifact_now = datetime.now(tz=UTC)

    class _ArtifactRepo:
        async def list_items(self, batch_id: str):
            assert batch_id == "b1"
            return [
                BatchItemRecord(
                    item_id="i1",
                    batch_id=batch_id,
                    line_number=1,
                    custom_id="custom-i1",
                    status="completed",
                    request_body={},
                    response_body=first["response_body"],
                    error_body=None,
                    usage=None,
                    provider_cost=0.0,
                    billed_cost=0.0,
                    attempts=1,
                    last_error=None,
                    locked_by=None,
                    lease_expires_at=None,
                    created_at=artifact_now,
                    started_at=artifact_now,
                    completed_at=artifact_now,
                ),
                BatchItemRecord(
                    item_id="i2",
                    batch_id=batch_id,
                    line_number=2,
                    custom_id="custom-i2",
                    status="completed",
                    request_body={},
                    response_body=second["response_body"],
                    error_body=None,
                    usage=None,
                    provider_cost=0.0,
                    billed_cost=0.0,
                    attempts=1,
                    last_error=None,
                    locked_by=None,
                    lease_expires_at=None,
                    created_at=artifact_now,
                    started_at=artifact_now,
                    completed_at=artifact_now,
                ),
            ]

    artifact_worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=_ArtifactRepo(),  # type: ignore[arg-type]
        storage=_Storage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )
    artifact_rows = [json.loads(line) async for line in artifact_worker._iter_output_lines("b1")]

    assert artifact_rows == [
        {
            "id": "batch_req_i1",
            "custom_id": "custom-i1",
            "response": {
                "status_code": 200,
                "request_id": "req_batch_i1",
                "body": {
                    "object": "list",
                    "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                    "model": "openai/text-embedding-3-small",
                    "usage": {"prompt_tokens": 4, "completion_tokens": 0, "total_tokens": 4},
                },
            },
            "error": None,
        },
        {
            "id": "batch_req_i2",
            "custom_id": "custom-i2",
            "response": {
                "status_code": 200,
                "request_id": "req_batch_i2",
                "body": {
                    "object": "list",
                    "data": [{"object": "embedding", "index": 0, "embedding": [0.3, 0.4]}],
                    "model": "openai/text-embedding-3-small",
                    "usage": {"prompt_tokens": 4, "completion_tokens": 0, "total_tokens": 4},
                },
            },
            "error": None,
        },
    ]


@pytest.mark.asyncio
async def test_worker_isolates_duplicate_response_indexes_back_to_single_item_execution(monkeypatch):
    execute_inputs: list[object] = []

    async def _fake_execute_embedding(request, payload, deployment):
        del request, deployment
        execute_inputs.append(payload.input)
        if payload.input == ["hello", "world"]:
            return {
                "object": "list",
                "data": [
                    {"index": 0, "embedding": [0.1]},
                    {"index": 0, "embedding": [0.2]},
                ],
                "usage": {"prompt_tokens": 10, "total_tokens": 10},
            }
        return {
            "object": "list",
            "data": [{"index": 0, "embedding": [0.1]}],
            "usage": {"prompt_tokens": 5, "total_tokens": 5},
        }

    monkeypatch.setattr("src.batch.worker._execute_embedding", _fake_execute_embedding)

    worker, repo, _, _, failover = _build_worker(deployment_model_info={"upstream_max_batch_inputs": 4})
    worker.config.worker_concurrency = 1

    await worker._process_items(
        _build_job(),
        [
            _build_item(item_id="i1", input_value="hello"),
            _build_item(item_id="i2", input_value="world"),
        ],
    )

    assert execute_inputs == [["hello", "world"], "hello", "world"]
    assert len(failover.calls) == 3
    assert len(repo.completed_calls) == 2
    assert repo.failed_calls == []


@pytest.mark.asyncio
async def test_worker_isolates_response_length_mismatch_back_to_single_item_execution(monkeypatch):
    execute_inputs: list[object] = []

    async def _fake_execute_embedding(request, payload, deployment):
        del request, deployment
        execute_inputs.append(payload.input)
        if payload.input == ["hello", "world"]:
            return {
                "object": "list",
                "data": [{"index": 0, "embedding": [0.1]}],
                "usage": {"prompt_tokens": 10, "total_tokens": 10},
            }
        return {
            "object": "list",
            "data": [{"index": 0, "embedding": [0.1]}],
            "usage": {"prompt_tokens": 5, "total_tokens": 5},
        }

    monkeypatch.setattr("src.batch.worker._execute_embedding", _fake_execute_embedding)

    worker, repo, _, _, failover = _build_worker(deployment_model_info={"upstream_max_batch_inputs": 4})
    worker.config.worker_concurrency = 1

    await worker._process_items(
        _build_job(),
        [
            _build_item(item_id="i1", input_value="hello"),
            _build_item(item_id="i2", input_value="world"),
        ],
    )

    assert execute_inputs == [["hello", "world"], "hello", "world"]
    assert len(failover.calls) == 3
    assert len(repo.completed_calls) == 2
    assert repo.failed_calls == []


@pytest.mark.asyncio
async def test_worker_isolates_upstream_exception_back_to_single_item_execution(monkeypatch):
    execute_inputs: list[object] = []

    async def _fake_execute_embedding(request, payload, deployment):
        del request, deployment
        execute_inputs.append(payload.input)
        if payload.input == ["hello", "world"]:
            raise RuntimeError("upstream exploded")
        return {
            "object": "list",
            "data": [{"index": 0, "embedding": [0.1]}],
            "usage": {"prompt_tokens": 5, "total_tokens": 5},
        }

    monkeypatch.setattr("src.batch.worker._execute_embedding", _fake_execute_embedding)

    worker, repo, _, _, failover = _build_worker(deployment_model_info={"upstream_max_batch_inputs": 4})
    worker.config.worker_concurrency = 1

    await worker._process_items(
        _build_job(),
        [
            _build_item(item_id="i1", input_value="hello"),
            _build_item(item_id="i2", input_value="world"),
        ],
    )

    assert execute_inputs == [["hello", "world"], "hello", "world"]
    assert len(failover.calls) == 3
    assert len(repo.completed_calls) == 2
    assert repo.failed_calls == []


@pytest.mark.asyncio
async def test_worker_grouped_fallback_uses_worker_process_item_override(monkeypatch):
    async def _fake_execute_embedding(request, payload, deployment):
        del request, deployment
        if payload.input == ["hello", "world"]:
            raise RuntimeError("upstream exploded")
        return {
            "object": "list",
            "data": [{"index": 0, "embedding": [0.1]}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
        }

    worker, repo, _, _, failover = _build_worker(deployment_model_info={"upstream_max_batch_inputs": 4})
    worker.config.worker_concurrency = 1

    overridden_item_ids: list[str] = []

    async def _process_item_override(job, item):  # noqa: ANN001
        del job
        overridden_item_ids.append(item.item_id)

    worker._process_item = _process_item_override  # type: ignore[assignment]

    monkeypatch.setattr("src.batch.worker._execute_embedding", _fake_execute_embedding)

    await worker._process_items(
        _build_job(),
        [
            _build_item(item_id="i1", input_value="hello"),
            _build_item(item_id="i2", input_value="world"),
        ],
    )

    assert overridden_item_ids == ["i1", "i2"]
    assert len(failover.calls) == 1
    assert repo.completed_calls == []
    assert repo.failed_calls == []


@pytest.mark.asyncio
async def test_worker_grouped_fallback_reprepares_items_before_isolated_retries(monkeypatch):
    async def _fake_execute_embedding(request, payload, deployment):
        del request, deployment
        if payload.input == ["hello", "world"]:
            raise RuntimeError("upstream exploded")
        return {
            "object": "list",
            "data": [{"index": 0, "embedding": [0.1]}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
        }

    monkeypatch.setattr("src.batch.worker._execute_embedding", _fake_execute_embedding)

    worker, repo, _, router, failover = _build_worker(deployment_model_info={"upstream_max_batch_inputs": 4})
    worker.config.worker_concurrency = 1

    prepare_calls: list[str] = []
    original_prepare_item = worker._prepare_item_for_execution

    async def _prepare_item_override(job, item):  # noqa: ANN001
        prepare_calls.append(item.item_id)
        return await original_prepare_item(job, item)

    worker._prepare_item_for_execution = _prepare_item_override  # type: ignore[assignment]

    await worker._process_items(
        _build_job(),
        [
            _build_item(item_id="i1", input_value="hello"),
            _build_item(item_id="i2", input_value="world"),
        ],
    )

    assert prepare_calls == ["i1", "i2", "i1", "i2"]
    assert len(router.select_calls) == 4
    assert len(failover.calls) == 3
    assert len(repo.completed_calls) == 2
    assert repo.failed_calls == []


@pytest.mark.asyncio
async def test_worker_records_one_upstream_failure_before_isolation_fallback(monkeypatch):
    class _PassiveHealthRecorder:
        def __init__(self) -> None:
            self.calls: list[tuple[str, bool, str | None]] = []

        async def record_request_outcome(
            self,
            deployment_id: str,
            success: bool,
            error: str | None = None,
            *,
            exc: Exception | None = None,
        ) -> None:
            del exc
            self.calls.append((deployment_id, success, error))

    async def _fake_execute_embedding(request, payload, deployment):
        del request, deployment
        if payload.input == ["hello", "world"]:
            raise RuntimeError("upstream exploded")
        return {
            "object": "list",
            "data": [{"index": 0, "embedding": [0.1]}],
            "usage": {"prompt_tokens": 5, "total_tokens": 5},
        }

    monkeypatch.setattr("src.batch.worker._execute_embedding", _fake_execute_embedding)

    worker, repo, _, _, failover = _build_worker(deployment_model_info={"upstream_max_batch_inputs": 4})
    passive_health = _PassiveHealthRecorder()
    worker.app.state.passive_health_tracker = passive_health
    worker.config.worker_concurrency = 1

    await worker._process_items(
        _build_job(),
        [
            _build_item(item_id="i1", input_value="hello"),
            _build_item(item_id="i2", input_value="world"),
        ],
    )

    assert len(failover.calls) == 3
    assert len(repo.completed_calls) == 2
    assert passive_health.calls == [
        ("dep-1", False, "upstream exploded"),
        ("dep-1", True, None),
        ("dep-1", True, None),
    ]


@pytest.mark.asyncio
async def test_worker_attributes_grouped_response_validation_failures_to_served_deployment(monkeypatch):
    class _PassiveHealthRecorder:
        def __init__(self) -> None:
            self.calls: list[tuple[str, bool, str | None]] = []

        async def record_request_outcome(
            self,
            deployment_id: str,
            success: bool,
            error: str | None = None,
            *,
            exc: Exception | None = None,
        ) -> None:
            del exc
            self.calls.append((deployment_id, success, error))

    async def _fake_execute_embedding(request, payload, deployment):
        del request
        if isinstance(payload.input, list):
            assert deployment.deployment_id == "dep-2"
            return {
                "object": "list",
                "data": [
                    {"index": 0, "embedding": [0.1]},
                    {"index": 0, "embedding": [0.2]},
                ],
                "usage": {"prompt_tokens": 10, "total_tokens": 10},
            }
        return {
            "object": "list",
            "data": [{"index": 0, "embedding": [0.1]}],
            "usage": {"prompt_tokens": 5, "total_tokens": 5},
        }

    monkeypatch.setattr("src.batch.worker._execute_embedding", _fake_execute_embedding)

    worker, repo, _, router, _ = _build_worker(deployment_model_info={"upstream_max_batch_inputs": 4})
    passive_health = _PassiveHealthRecorder()
    served_deployment = SimpleNamespace(
        deployment_id="dep-2",
        deltallm_params={"model": "openai/text-embedding-3-small", "api_base": "http://localhost:9090/v1"},
        input_cost_per_token=0.001,
        output_cost_per_token=0.0,
        model_info={"upstream_max_batch_inputs": 4},
    )

    class _FailoverToDep2:
        async def execute_with_failover(
            self,
            *,
            primary_deployment,
            model_group,
            execute,
            return_deployment=False,
            **kwargs,
        ):
            del primary_deployment, model_group, kwargs
            data = await execute(served_deployment)
            if return_deployment:
                return data, served_deployment
            return data

    worker.app.state.failover_manager = _FailoverToDep2()
    worker.app.state.passive_health_tracker = passive_health
    worker.config.worker_concurrency = 1

    await worker._process_items(
        _build_job(),
        [
            _build_item(item_id="i1", input_value="hello"),
            _build_item(item_id="i2", input_value="world"),
        ],
    )

    assert len(repo.completed_calls) == 2
    assert len(router.select_calls) == 4
    assert passive_health.calls[0] == ("dep-2", False, "microbatch response contains duplicate index=0")


@pytest.mark.asyncio
async def test_worker_salvages_grouped_chunk_when_bulk_completion_fails_without_reexecuting_upstream(monkeypatch):
    class _RouterStateBackend:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, int]]] = []

        async def increment_usage_counters(self, deployment_id: str, counters: dict[str, int]) -> None:
            self.calls.append((deployment_id, dict(counters)))

    class _PassiveHealthRecorder:
        def __init__(self) -> None:
            self.calls: list[tuple[str, bool, str | None]] = []

        async def record_request_outcome(
            self,
            deployment_id: str,
            success: bool,
            error: str | None = None,
            *,
            exc: Exception | None = None,
        ) -> None:
            del exc
            self.calls.append((deployment_id, success, error))

    async def _fake_execute_embedding(request, payload, deployment):
        del request, deployment
        inputs = payload.input if isinstance(payload.input, list) else [payload.input]
        return {
            "object": "list",
            "data": [{"index": index, "embedding": [0.1 + index]} for index, _ in enumerate(inputs)],
            "usage": {
                "prompt_tokens": 5 * len(inputs),
                "completion_tokens": 0,
                "total_tokens": 5 * len(inputs),
            },
        }

    monkeypatch.setattr("src.batch.worker._execute_embedding", _fake_execute_embedding)

    class _BulkFailRepo(_Repo):
        async def complete_items_with_outbox_bulk(self, **kwargs) -> str:
            self.completed_bulk_calls.append(kwargs)
            raise RuntimeError("database unavailable")

    worker, _, _, _, failover = _build_worker(deployment_model_info={"upstream_max_batch_inputs": 4})
    repo = _BulkFailRepo()
    router_state_backend = _RouterStateBackend()
    passive_health = _PassiveHealthRecorder()
    worker.repository = repo  # type: ignore[assignment]
    worker.app.state.router_state_backend = router_state_backend
    worker.app.state.passive_health_tracker = passive_health
    worker.config.worker_concurrency = 1

    await worker._process_items(
        _build_job(),
        [
            _build_item(item_id="i1", input_value="hello"),
            _build_item(item_id="i2", input_value="world"),
        ],
    )

    assert len(failover.calls) == 1
    assert len(repo.completed_bulk_calls) == 2
    assert repo.completed_calls == []
    assert repo.release_for_retry_calls == [{"item_ids": ["i1", "i2"], "worker_id": "w1"}]
    assert repo.failed_calls == []
    assert passive_health.calls == [("dep-1", True, None)]
    assert router_state_backend.calls == [("dep-1", {"rpm": 1, "tpm": 10})]


@pytest.mark.asyncio
async def test_worker_treats_ambiguous_single_item_completion_exception_as_already_persisted(monkeypatch):
    execute_inputs: list[object] = []

    async def _fake_execute_embedding(request, payload, deployment):
        del request, deployment
        execute_inputs.append(payload.input)
        return {
            "object": "list",
            "data": [{"index": 0, "embedding": [0.1]}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
        }

    monkeypatch.setattr("src.batch.worker._execute_embedding", _fake_execute_embedding)

    class _AmbiguousCommitRepo(_Repo):
        def __init__(self) -> None:
            super().__init__()
            self.persist_attempts = 0

        async def complete_items_with_outbox_bulk(self, **kwargs) -> str:
            self.completed_bulk_calls.append(kwargs)
            self.persist_attempts += 1
            if self.persist_attempts == 1:
                raise RuntimeError("connection dropped after commit")
            return "already_completed"

    worker, _, _, _, failover = _build_worker()
    repo = _AmbiguousCommitRepo()
    worker.repository = repo  # type: ignore[assignment]
    worker.config.worker_concurrency = 1

    await worker._process_items(
        _build_job(),
        [_build_item(item_id="i1", input_value="hello")],
    )

    assert execute_inputs == ["hello"]
    assert len(failover.calls) == 1
    assert len(repo.completed_bulk_calls) == 2
    assert repo.release_for_retry_calls == []
    assert repo.failed_calls == []


@pytest.mark.asyncio
async def test_worker_requeues_unpersisted_items_after_grouped_completion_salvage(monkeypatch):
    execute_inputs: list[object] = []

    async def _fake_execute_embedding(request, payload, deployment):
        del request, deployment
        execute_inputs.append(payload.input)
        inputs = payload.input if isinstance(payload.input, list) else [payload.input]
        return {
            "object": "list",
            "data": [{"index": index, "embedding": [0.1 + index]} for index, _ in enumerate(inputs)],
            "usage": {"prompt_tokens": 5 * len(inputs), "total_tokens": 5 * len(inputs)},
        }

    monkeypatch.setattr("src.batch.worker._execute_embedding", _fake_execute_embedding)

    class _LeaseLossRepo(_Repo):
        async def complete_items_with_outbox_bulk(self, **kwargs) -> str:
            self.completed_bulk_calls.append(kwargs)
            return "not_owned"

    worker, _, _, _, failover = _build_worker(deployment_model_info={"upstream_max_batch_inputs": 4})
    repo = _LeaseLossRepo()
    worker.repository = repo  # type: ignore[assignment]
    worker.config.worker_concurrency = 1

    await worker._process_items(
        _build_job(),
        [
            _build_item(item_id="i1", input_value="hello"),
            _build_item(item_id="i2", input_value="world"),
        ],
    )

    assert execute_inputs == [["hello", "world"]]
    assert len(failover.calls) == 1
    assert repo.completed_calls == []
    assert repo.release_for_retry_calls == []
    assert repo.failed_calls == []


@pytest.mark.asyncio
async def test_worker_replans_later_chunks_after_earlier_chunk_usage_is_recorded(monkeypatch):
    execute_calls: list[tuple[str, object]] = []
    first_chunk_started = asyncio.Event()
    allow_first_chunk_finish = asyncio.Event()

    async def _fake_execute_embedding(request, payload, deployment):
        del request
        execute_calls.append((deployment.deployment_id, payload.input))
        if payload.input == ["hello-1", "hello-2"]:
            first_chunk_started.set()
            await allow_first_chunk_finish.wait()
        inputs = payload.input if isinstance(payload.input, list) else [payload.input]
        return {
            "object": "list",
            "data": [{"index": index, "embedding": [0.1 + index]} for index, _ in enumerate(inputs)],
            "usage": {
                "prompt_tokens": 5 * len(inputs),
                "completion_tokens": 0,
                "total_tokens": 5 * len(inputs),
            },
        }

    monkeypatch.setattr("src.batch.worker._execute_embedding", _fake_execute_embedding)

    class _RouterStateBackend:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, int]]] = []

        async def increment_usage_counters(self, deployment_id: str, counters: dict[str, int]) -> None:
            self.calls.append((deployment_id, dict(counters)))

    deployment_one = SimpleNamespace(
        deployment_id="dep-1",
        deltallm_params={"model": "openai/text-embedding-3-small", "api_base": "http://localhost:9090/v1"},
        input_cost_per_token=0.001,
        output_cost_per_token=0.0,
        model_info={"upstream_max_batch_inputs": 2},
    )
    deployment_two = SimpleNamespace(
        deployment_id="dep-2",
        deltallm_params={"model": "openai/text-embedding-3-small", "api_base": "http://localhost:9090/v1"},
        input_cost_per_token=0.001,
        output_cost_per_token=0.0,
        model_info={"upstream_max_batch_inputs": 2},
    )
    repo = _Repo()
    budget = _BudgetService()
    router_state_backend = _RouterStateBackend()

    class _Router:
        def __init__(self) -> None:
            self.select_calls: list[str] = []

        def resolve_model_group(self, model: str) -> str:
            return f"group:{model}"

        async def select_deployment(self, model_group: str, request_context: dict) -> object:
            del model_group, request_context
            selected = deployment_one if not router_state_backend.calls else deployment_two
            self.select_calls.append(selected.deployment_id)
            return selected

        def require_deployment(self, model_group: str, deployment: object):
            assert model_group.startswith("group:")
            return deployment

    failover = _Failover()
    router = _Router()
    app = SimpleNamespace(
        state=SimpleNamespace(
            router=router,
            failover_manager=failover,
            budget_service=budget,
            spend_tracking_service=None,
            passive_health_tracker=None,
            router_state_backend=router_state_backend,
            settings=SimpleNamespace(openai_base_url="http://localhost:9090/v1"),
            http_client=None,
        )
    )
    worker = BatchExecutorWorker(
        app=app,
        repository=repo,  # type: ignore[arg-type]
        storage=_Storage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1", worker_concurrency=2),
    )

    process_task = asyncio.create_task(
        worker._process_items(
            _build_job(),
            [
                _build_item(item_id="i1", input_value="hello-1"),
                _build_item(item_id="i2", input_value="hello-2"),
                _build_item(item_id="i3", input_value="hello-3"),
                _build_item(item_id="i4", input_value="hello-4"),
            ],
        )
    )
    await first_chunk_started.wait()
    await asyncio.sleep(0.05)

    assert router.select_calls == ["dep-1", "dep-1"]

    allow_first_chunk_finish.set()
    await process_task

    assert execute_calls == [
        ("dep-1", ["hello-1", "hello-2"]),
        ("dep-2", ["hello-3", "hello-4"]),
    ]
    assert router.select_calls == ["dep-1", "dep-1", "dep-2", "dep-2"]
    assert router_state_backend.calls == [
        ("dep-1", {"rpm": 1, "tpm": 10}),
        ("dep-2", {"rpm": 1, "tpm": 10}),
    ]


@pytest.mark.asyncio
async def test_worker_keeps_later_item_leases_alive_during_slow_isolation_fallback(monkeypatch):
    first_single_item_started = asyncio.Event()
    allow_first_single_item_finish = asyncio.Event()

    async def _fake_execute_embedding(request, payload, deployment):
        del request, deployment
        if payload.input == ["hello", "world"]:
            raise RuntimeError("upstream exploded")
        if payload.input == "hello":
            first_single_item_started.set()
            await allow_first_single_item_finish.wait()
        return {
            "object": "list",
            "data": [{"index": 0, "embedding": [0.1]}],
            "usage": {"prompt_tokens": 5, "total_tokens": 5},
        }

    monkeypatch.setattr("src.batch.worker._execute_embedding", _fake_execute_embedding)

    worker, repo, _, _, _ = _build_worker(deployment_model_info={"upstream_max_batch_inputs": 4})
    worker.config.worker_concurrency = 1
    worker.config.heartbeat_interval_seconds = 0.01

    process_task = asyncio.create_task(
        worker._process_items(
            _build_job(),
            [
                _build_item(item_id="i1", input_value="hello"),
                _build_item(item_id="i2", input_value="world"),
            ],
        )
    )

    await first_single_item_started.wait()
    await asyncio.sleep(0.05)

    assert any(call["item_id"] == "i2" for call in repo.renew_calls)

    allow_first_single_item_finish.set()
    await process_task

    assert len(repo.completed_calls) == 2
    assert repo.failed_calls == []
