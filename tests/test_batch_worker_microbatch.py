from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.batch.embedding_microbatch import (
    build_embedding_execution_signature,
    classify_embedding_microbatch_request,
    resolve_effective_upstream_max_batch_inputs,
)
from src.batch.models import BatchItemRecord, BatchJobRecord, BatchJobStatus
from src.batch.worker import BatchExecutorWorker, BatchWorkerConfig
from src.models.requests import EmbeddingRequest


class _Repo:
    def __init__(self) -> None:
        self.completed_calls: list[dict] = []
        self.failed_calls: list[dict] = []
        self.renew_calls: list[dict] = []

    async def mark_item_completed(self, **kwargs) -> bool:
        self.completed_calls.append(kwargs)
        return True

    async def mark_item_failed(self, **kwargs) -> bool:
        self.failed_calls.append(kwargs)
        return True

    async def renew_item_lease(self, **kwargs) -> bool:
        self.renew_calls.append(kwargs)
        return True

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
@pytest.mark.parametrize("model_info", [{}, {"upstream_max_batch_inputs": 1}, {"upstream_max_batch_inputs": 4}])
async def test_worker_keeps_one_upstream_call_per_item_before_grouped_execution(monkeypatch, model_info: dict):
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

    worker, repo, budget, _, failover = _build_worker(deployment_model_info=model_info)

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
