from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import logging
from types import SimpleNamespace

import pytest

from fastapi import HTTPException

from src.batch.models import BatchItemRecord, BatchJobRecord, BatchJobStatus, encode_operator_failed_reason
from src.batch.service import BatchService
from src.batch.worker import BatchExecutorWorker, BatchWorkerConfig
from src.models.responses import UserAPIKeyAuth


class _SpendRecorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def log_spend(self, **kwargs):
        self.events.append({"status": "success", **kwargs})

    async def log_request_failure(self, **kwargs):
        self.events.append({"status": "error", "cost": 0.0, **kwargs})


class _PassiveHealthRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool, str | None]] = []

    async def record_request_outcome(self, deployment_id: str, success: bool, error: str | None = None) -> None:
        self.calls.append((deployment_id, success, error))


class _FakeRepository:
    def __init__(self) -> None:
        self.completed_calls: list[dict] = []

    async def mark_item_completed(self, **kwargs) -> bool:
        self.completed_calls.append(kwargs)
        return True

    async def mark_item_failed(self, **kwargs) -> bool:  # pragma: no cover
        raise AssertionError(f"unexpected failure path: {kwargs}")

    async def get_job(self, batch_id: str):  # for BatchService test
        return self.job if batch_id == self.job.batch_id else None


class _FakeStorage:
    async def write_lines(self, **kwargs):  # pragma: no cover
        raise AssertionError(f"unexpected artifact write in this test: {kwargs}")

    async def write_lines_stream(self, **kwargs):  # pragma: no cover
        raise AssertionError(f"unexpected streaming artifact write in this test: {kwargs}")

    async def delete(self, storage_key: str) -> None:  # pragma: no cover
        del storage_key
        raise AssertionError("unexpected artifact delete in this test")


@pytest.mark.asyncio
async def test_batch_worker_logs_batch_pricing_and_spend(monkeypatch):
    async def _fake_execute_embedding(request, payload, deployment):
        del request, payload, deployment
        return {"object": "list", "data": [{"index": 0, "embedding": [0.1]}], "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5}}

    monkeypatch.setattr("src.batch.worker._execute_embedding", _fake_execute_embedding)

    deployment = SimpleNamespace(
        deltallm_params={"model": "vllm/sentence-transformers/all-MiniLM-L6-v2", "api_base": "http://localhost:9090/v1"},
        input_cost_per_token=0.001,
        output_cost_per_token=0.0,
        model_info={"batch_input_cost_per_token": 0.0005, "batch_output_cost_per_token": 0.0},
    )

    class _Router:
        def resolve_model_group(self, model: str) -> str:
            return model

        async def select_deployment(self, model_group: str, request_context: dict) -> str:
            del model_group, request_context
            return "dep-1"

        def require_deployment(self, model_group: str, deployment: str):
            del model_group, deployment
            return deployment_obj

    class _Failover:
        async def execute_with_failover(
            self,
            *,
            primary_deployment,
            model_group,
            execute,
            return_deployment=False,
            **kwargs,
        ):
            del model_group, kwargs
            data = await execute(primary_deployment)
            if return_deployment:
                return data, primary_deployment
            return data

    deployment_obj = deployment
    repo = _FakeRepository()
    spend = _SpendRecorder()
    app = SimpleNamespace(state=SimpleNamespace(router=_Router(), failover_manager=_Failover(), spend_tracking_service=spend))
    worker = BatchExecutorWorker(
        app=app,
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )

    now = datetime.now(tz=UTC)
    job = BatchJobRecord(
        batch_id="b1",
        endpoint="/v1/embeddings",
        status=BatchJobStatus.IN_PROGRESS,
        execution_mode="managed_internal",
        input_file_id="f1",
        output_file_id=None,
        error_file_id=None,
        model="m-1",
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
        created_by_api_key="tok-1",
        created_by_user_id="u1",
        created_by_team_id="t1",
        created_at=now,
        started_at=now,
        completed_at=None,
        expires_at=None,
        created_by_organization_id="org-1",
    )
    item = BatchItemRecord(
        item_id="i1",
        batch_id="b1",
        line_number=1,
        custom_id="c1",
        status="in_progress",
        request_body={"model": "m-1", "input": "hello"},
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

    await worker._process_item(job, item)

    assert len(repo.completed_calls) == 1
    completed = repo.completed_calls[0]
    assert completed["provider_cost"] == 0.005
    assert completed["billed_cost"] == 0.0025
    assert len(spend.events) == 1
    logged = spend.events[0]
    assert logged["call_type"] == "embedding_batch"
    assert logged["cost"] == 0.0025
    assert logged["organization_id"] == "org-1"
    assert logged["metadata"]["deployment_model"] == "vllm/sentence-transformers/all-MiniLM-L6-v2"
    assert logged["metadata"]["pricing_tier"] == "batch"
    assert logged["metadata"]["provider_cost"] == 0.005


@pytest.mark.asyncio
async def test_batch_worker_keeps_completed_state_when_side_effects_fail(monkeypatch):
    async def _fake_execute_embedding(request, payload, deployment):
        del request, payload, deployment
        return {"object": "list", "data": [{"index": 0, "embedding": [0.1]}], "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5}}

    monkeypatch.setattr("src.batch.worker._execute_embedding", _fake_execute_embedding)

    def _boom(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("metrics backend unavailable")

    monkeypatch.setattr("src.batch.worker.increment_request", _boom)

    deployment = SimpleNamespace(
        deltallm_params={"model": "vllm/sentence-transformers/all-MiniLM-L6-v2", "api_base": "http://localhost:9090/v1"},
        input_cost_per_token=0.001,
        output_cost_per_token=0.0,
        model_info={"batch_input_cost_per_token": 0.0005, "batch_output_cost_per_token": 0.0},
    )

    class _Router:
        def resolve_model_group(self, model: str) -> str:
            return model

        async def select_deployment(self, model_group: str, request_context: dict) -> str:
            del model_group, request_context
            return "dep-1"

        def require_deployment(self, model_group: str, deployment: str):
            del model_group, deployment
            return deployment_obj

    class _Failover:
        async def execute_with_failover(
            self,
            *,
            primary_deployment,
            model_group,
            execute,
            return_deployment=False,
            **kwargs,
        ):
            del model_group, kwargs
            data = await execute(primary_deployment)
            if return_deployment:
                return data, primary_deployment
            return data

    deployment_obj = deployment
    repo = _FakeRepository()
    spend = _SpendRecorder()
    app = SimpleNamespace(state=SimpleNamespace(router=_Router(), failover_manager=_Failover(), spend_tracking_service=spend))
    worker = BatchExecutorWorker(
        app=app,
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )

    now = datetime.now(tz=UTC)
    job = BatchJobRecord(
        batch_id="b1",
        endpoint="/v1/embeddings",
        status=BatchJobStatus.IN_PROGRESS,
        execution_mode="managed_internal",
        input_file_id="f1",
        output_file_id=None,
        error_file_id=None,
        model="m-1",
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
        created_by_api_key="tok-1",
        created_by_user_id="u1",
        created_by_team_id="t1",
        created_at=now,
        started_at=now,
        completed_at=None,
        expires_at=None,
        created_by_organization_id="org-1",
    )
    item = BatchItemRecord(
        item_id="i1",
        batch_id="b1",
        line_number=1,
        custom_id="c1",
        status="in_progress",
        request_body={"model": "m-1", "input": "hello"},
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

    await worker._process_item(job, item)
    assert len(repo.completed_calls) == 1


@pytest.mark.asyncio
async def test_batch_worker_keeps_completed_state_when_passive_health_success_hook_fails(monkeypatch):
    async def _fake_execute_embedding(request, payload, deployment):
        del request, payload, deployment
        return {"object": "list", "data": [{"index": 0, "embedding": [0.1]}], "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5}}

    monkeypatch.setattr("src.batch.worker._execute_embedding", _fake_execute_embedding)

    deployment = SimpleNamespace(
        deployment_id="dep-1",
        deltallm_params={"model": "vllm/sentence-transformers/all-MiniLM-L6-v2", "api_base": "http://localhost:9090/v1"},
        input_cost_per_token=0.001,
        output_cost_per_token=0.0,
        model_info={"batch_input_cost_per_token": 0.0005, "batch_output_cost_per_token": 0.0},
    )

    class _FailingPassiveHealth:
        async def record_request_outcome(self, deployment_id: str, success: bool, error: str | None = None) -> None:
            del deployment_id, success, error
            raise RuntimeError("health sink unavailable")

    class _RouterStateBackend:
        async def increment_usage_counters(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
            return None

    class _Router:
        def resolve_model_group(self, model: str) -> str:
            return model

        async def select_deployment(self, model_group: str, request_context: dict) -> str:
            del model_group, request_context
            return "dep-1"

        def require_deployment(self, model_group: str, deployment: str):
            del model_group, deployment
            return deployment_obj

    class _Failover:
        async def execute_with_failover(self, *, primary_deployment, model_group, execute, return_deployment=False, **kwargs):  # noqa: ANN001
            del model_group, kwargs
            data = await execute(primary_deployment)
            if return_deployment:
                return data, primary_deployment
            return data

    deployment_obj = deployment
    repo = _FakeRepository()
    spend = _SpendRecorder()
    router_usage_calls: list[str] = []

    async def _record_router_usage(state_backend, deployment_id: str, *, mode: str, usage: dict):  # noqa: ANN001
        del state_backend, mode, usage
        router_usage_calls.append(deployment_id)

    monkeypatch.setattr("src.batch.worker.record_router_usage", _record_router_usage)

    app = SimpleNamespace(
        state=SimpleNamespace(
            router=_Router(),
            failover_manager=_Failover(),
            spend_tracking_service=spend,
            passive_health_tracker=_FailingPassiveHealth(),
            router_state_backend=_RouterStateBackend(),
        )
    )
    worker = BatchExecutorWorker(
        app=app,
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )

    now = datetime.now(tz=UTC)
    job = BatchJobRecord(
        batch_id="b1",
        endpoint="/v1/embeddings",
        status=BatchJobStatus.IN_PROGRESS,
        execution_mode="managed_internal",
        input_file_id="f1",
        output_file_id=None,
        error_file_id=None,
        model="m-1",
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
        created_by_api_key="tok-1",
        created_by_user_id="u1",
        created_by_team_id="t1",
        created_at=now,
        started_at=now,
        completed_at=None,
        expires_at=None,
    )
    item = BatchItemRecord(
        item_id="i1",
        batch_id="b1",
        line_number=1,
        custom_id="c1",
        status="in_progress",
        request_body={"model": "m-1", "input": "hello"},
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

    await worker._process_item(job, item)

    assert len(repo.completed_calls) == 1
    assert router_usage_calls == ["dep-1"]


@pytest.mark.asyncio
async def test_batch_worker_keeps_completed_state_when_router_usage_hook_fails(monkeypatch):
    async def _fake_execute_embedding(request, payload, deployment):
        del request, payload, deployment
        return {"object": "list", "data": [{"index": 0, "embedding": [0.1]}], "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5}}

    monkeypatch.setattr("src.batch.worker._execute_embedding", _fake_execute_embedding)
    async def _failing_record_router_usage(*args, **kwargs):  # noqa: ANN002, ANN003
        del args, kwargs
        raise RuntimeError("router usage unavailable")

    monkeypatch.setattr("src.batch.worker.record_router_usage", _failing_record_router_usage)

    deployment = SimpleNamespace(
        deployment_id="dep-1",
        deltallm_params={"model": "vllm/sentence-transformers/all-MiniLM-L6-v2", "api_base": "http://localhost:9090/v1"},
        input_cost_per_token=0.001,
        output_cost_per_token=0.0,
        model_info={"batch_input_cost_per_token": 0.0005, "batch_output_cost_per_token": 0.0},
    )

    class _RouterStateBackend:
        async def increment_usage_counters(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
            return None

    class _Router:
        def resolve_model_group(self, model: str) -> str:
            return model

        async def select_deployment(self, model_group: str, request_context: dict) -> str:
            del model_group, request_context
            return "dep-1"

        def require_deployment(self, model_group: str, deployment: str):
            del model_group, deployment
            return deployment_obj

    class _Failover:
        async def execute_with_failover(self, *, primary_deployment, model_group, execute, return_deployment=False, **kwargs):  # noqa: ANN001
            del model_group, kwargs
            data = await execute(primary_deployment)
            if return_deployment:
                return data, primary_deployment
            return data

    deployment_obj = deployment
    repo = _FakeRepository()
    spend = _SpendRecorder()
    app = SimpleNamespace(
        state=SimpleNamespace(
            router=_Router(),
            failover_manager=_Failover(),
            spend_tracking_service=spend,
            passive_health_tracker=_PassiveHealthRecorder(),
            router_state_backend=_RouterStateBackend(),
        )
    )
    worker = BatchExecutorWorker(
        app=app,
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )

    now = datetime.now(tz=UTC)
    job = BatchJobRecord(
        batch_id="b1",
        endpoint="/v1/embeddings",
        status=BatchJobStatus.IN_PROGRESS,
        execution_mode="managed_internal",
        input_file_id="f1",
        output_file_id=None,
        error_file_id=None,
        model="m-1",
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
        created_by_api_key="tok-1",
        created_by_user_id="u1",
        created_by_team_id="t1",
        created_at=now,
        started_at=now,
        completed_at=None,
        expires_at=None,
    )
    item = BatchItemRecord(
        item_id="i1",
        batch_id="b1",
        line_number=1,
        custom_id="c1",
        status="in_progress",
        request_body={"model": "m-1", "input": "hello"},
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

    await worker._process_item(job, item)

    assert len(repo.completed_calls) == 1


@pytest.mark.asyncio
async def test_batch_worker_enforces_budget_before_provider_execution(monkeypatch):
    execute_called = False

    async def _fake_execute_embedding(request, payload, deployment):
        nonlocal execute_called
        del request, payload, deployment
        execute_called = True
        return {"object": "list", "data": [{"index": 0, "embedding": [0.1]}], "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5}}

    monkeypatch.setattr("src.batch.worker._execute_embedding", _fake_execute_embedding)

    class _BudgetService:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def check_budgets(self, **kwargs):
            self.calls.append(kwargs)

    class _RouterStateBackend:
        async def increment_usage_counters(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
            return None

    deployment = SimpleNamespace(
        deployment_id="dep-1",
        deltallm_params={"model": "vllm/sentence-transformers/all-MiniLM-L6-v2", "api_base": "http://localhost:9090/v1"},
        input_cost_per_token=0.001,
        output_cost_per_token=0.0,
        model_info={"batch_input_cost_per_token": 0.0005, "batch_output_cost_per_token": 0.0},
    )

    class _Router:
        def resolve_model_group(self, model: str) -> str:
            return model

        async def select_deployment(self, model_group: str, request_context: dict) -> str:
            del model_group, request_context
            return "dep-1"

        def require_deployment(self, model_group: str, deployment: str):
            del model_group, deployment
            return deployment_obj

    class _Failover:
        async def execute_with_failover(self, *, primary_deployment, model_group, execute, return_deployment=False, **kwargs):  # noqa: ANN001
            del model_group, kwargs
            data = await execute(primary_deployment)
            if return_deployment:
                return data, primary_deployment
            return data

    deployment_obj = deployment
    repo = _FakeRepository()
    spend = _SpendRecorder()
    budget = _BudgetService()
    app = SimpleNamespace(
        state=SimpleNamespace(
            router=_Router(),
            failover_manager=_Failover(),
            spend_tracking_service=spend,
            budget_service=budget,
            passive_health_tracker=_PassiveHealthRecorder(),
            router_state_backend=_RouterStateBackend(),
        )
    )
    worker = BatchExecutorWorker(
        app=app,
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )

    now = datetime.now(tz=UTC)
    job = BatchJobRecord(
        batch_id="b1",
        endpoint="/v1/embeddings",
        status=BatchJobStatus.IN_PROGRESS,
        execution_mode="managed_internal",
        input_file_id="f1",
        output_file_id=None,
        error_file_id=None,
        model="m-1",
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
        created_by_api_key="tok-1",
        created_by_user_id="u1",
        created_by_team_id="t1",
        created_at=now,
        started_at=now,
        completed_at=None,
        expires_at=None,
        created_by_organization_id="org-1",
    )
    item = BatchItemRecord(
        item_id="i1",
        batch_id="b1",
        line_number=1,
        custom_id="c1",
        status="in_progress",
        request_body={"model": "m-1", "input": "hello"},
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

    await worker._process_item(job, item)

    assert execute_called is True
    assert budget.calls == [
        {"api_key": "tok-1", "user_id": "u1", "team_id": "t1", "organization_id": "org-1", "model": "m-1"}
    ]


@pytest.mark.asyncio
async def test_batch_worker_logs_request_failure_and_health_on_error():
    class _BudgetService:
        async def check_budgets(self, **kwargs):  # noqa: ANN003, ANN201
            return None

    class _RouterStateBackend:
        async def increment_usage_counters(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
            return None

    class _Router:
        def resolve_model_group(self, model: str) -> str:
            return model

        async def select_deployment(self, model_group: str, request_context: dict) -> str:
            del model_group, request_context
            return "dep-1"

        def require_deployment(self, model_group: str, deployment: str):
            del model_group, deployment
            return deployment_obj

    class _Failover:
        async def execute_with_failover(self, **kwargs):  # noqa: ANN003, ANN201
            raise RuntimeError("provider down")

    class _FailureRepo(_FakeRepository):
        def __init__(self) -> None:
            super().__init__()
            self.failed_calls: list[dict] = []

        async def mark_item_failed(self, **kwargs) -> bool:
            self.failed_calls.append(kwargs)
            return True

        async def refresh_job_progress(self, batch_id: str):
            assert batch_id == "b1"
            return None

    deployment_obj = SimpleNamespace(
        deployment_id="dep-1",
        deltallm_params={"model": "vllm/sentence-transformers/all-MiniLM-L6-v2", "api_base": "http://localhost:9090/v1"},
        input_cost_per_token=0.001,
        output_cost_per_token=0.0,
        model_info={},
    )
    repo = _FailureRepo()
    spend = _SpendRecorder()
    passive_health = _PassiveHealthRecorder()
    app = SimpleNamespace(
        state=SimpleNamespace(
            router=_Router(),
            failover_manager=_Failover(),
            spend_tracking_service=spend,
            budget_service=_BudgetService(),
            passive_health_tracker=passive_health,
            router_state_backend=_RouterStateBackend(),
        )
    )
    worker = BatchExecutorWorker(
        app=app,
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )

    now = datetime.now(tz=UTC)
    job = BatchJobRecord(
        batch_id="b1",
        endpoint="/v1/embeddings",
        status=BatchJobStatus.IN_PROGRESS,
        execution_mode="managed_internal",
        input_file_id="f1",
        output_file_id=None,
        error_file_id=None,
        model="m-1",
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
        created_by_api_key="tok-1",
        created_by_user_id="u1",
        created_by_team_id="t1",
        created_at=now,
        started_at=now,
        completed_at=None,
        expires_at=None,
        created_by_organization_id="org-1",
    )
    item = BatchItemRecord(
        item_id="i1",
        batch_id="b1",
        line_number=1,
        custom_id="c1",
        status="in_progress",
        request_body={"model": "m-1", "input": "hello"},
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

    await worker._process_item(job, item)

    assert len(repo.failed_calls) == 1
    assert passive_health.calls == [("dep-1", False, "provider down")]
    assert len(spend.events) == 1
    assert spend.events[0]["status"] == "error"
    assert spend.events[0]["request_id"] == "batch:b1:i1"
    assert spend.events[0]["organization_id"] == "org-1"


@pytest.mark.asyncio
async def test_batch_worker_keeps_failed_state_when_passive_health_failure_hook_raises():
    class _BudgetService:
        async def check_budgets(self, **kwargs):  # noqa: ANN003, ANN201
            return None

    class _RouterStateBackend:
        async def increment_usage_counters(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
            return None

    class _Router:
        def resolve_model_group(self, model: str) -> str:
            return model

        async def select_deployment(self, model_group: str, request_context: dict) -> str:
            del model_group, request_context
            return "dep-1"

        def require_deployment(self, model_group: str, deployment: str):
            del model_group, deployment
            return deployment_obj

    class _Failover:
        async def execute_with_failover(self, **kwargs):  # noqa: ANN003, ANN201
            raise RuntimeError("provider down")

    class _FailureRepo(_FakeRepository):
        def __init__(self) -> None:
            super().__init__()
            self.failed_calls: list[dict] = []

        async def mark_item_failed(self, **kwargs) -> bool:
            self.failed_calls.append(kwargs)
            return True

        async def refresh_job_progress(self, batch_id: str):
            assert batch_id == "b1"
            return None

    class _FailingPassiveHealth:
        def __init__(self) -> None:
            self.calls: list[tuple[str, bool, str | None]] = []

        async def record_request_outcome(self, deployment_id: str, success: bool, error: str | None = None) -> None:
            self.calls.append((deployment_id, success, error))
            raise RuntimeError("health sink unavailable")

    deployment_obj = SimpleNamespace(
        deployment_id="dep-1",
        deltallm_params={"model": "vllm/sentence-transformers/all-MiniLM-L6-v2", "api_base": "http://localhost:9090/v1"},
        input_cost_per_token=0.001,
        output_cost_per_token=0.0,
        model_info={},
    )
    repo = _FailureRepo()
    spend = _SpendRecorder()
    passive_health = _FailingPassiveHealth()
    app = SimpleNamespace(
        state=SimpleNamespace(
            router=_Router(),
            failover_manager=_Failover(),
            spend_tracking_service=spend,
            budget_service=_BudgetService(),
            passive_health_tracker=passive_health,
            router_state_backend=_RouterStateBackend(),
        )
    )
    worker = BatchExecutorWorker(
        app=app,
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )

    now = datetime.now(tz=UTC)
    job = BatchJobRecord(
        batch_id="b1",
        endpoint="/v1/embeddings",
        status=BatchJobStatus.IN_PROGRESS,
        execution_mode="managed_internal",
        input_file_id="f1",
        output_file_id=None,
        error_file_id=None,
        model="m-1",
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
        created_by_api_key="tok-1",
        created_by_user_id="u1",
        created_by_team_id="t1",
        created_at=now,
        started_at=now,
        completed_at=None,
        expires_at=None,
        created_by_organization_id="org-1",
    )
    item = BatchItemRecord(
        item_id="i1",
        batch_id="b1",
        line_number=1,
        custom_id="c1",
        status="in_progress",
        request_body={"model": "m-1", "input": "hello"},
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

    await worker._process_item(job, item)

    assert len(repo.failed_calls) == 1
    assert passive_health.calls == [("dep-1", False, "provider down")]
    assert len(spend.events) == 1
    assert spend.events[0]["status"] == "error"


@pytest.mark.asyncio
async def test_batch_worker_keeps_failed_state_when_failure_logging_hook_raises():
    class _BudgetService:
        async def check_budgets(self, **kwargs):  # noqa: ANN003, ANN201
            return None

    class _RouterStateBackend:
        async def increment_usage_counters(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
            return None

    class _Router:
        def resolve_model_group(self, model: str) -> str:
            return model

        async def select_deployment(self, model_group: str, request_context: dict) -> str:
            del model_group, request_context
            return "dep-1"

        def require_deployment(self, model_group: str, deployment: str):
            del model_group, deployment
            return deployment_obj

    class _Failover:
        async def execute_with_failover(self, **kwargs):  # noqa: ANN003, ANN201
            raise RuntimeError("provider down")

    class _FailureRepo(_FakeRepository):
        def __init__(self) -> None:
            super().__init__()
            self.failed_calls: list[dict] = []

        async def mark_item_failed(self, **kwargs) -> bool:
            self.failed_calls.append(kwargs)
            return True

        async def refresh_job_progress(self, batch_id: str):
            assert batch_id == "b1"
            return None

    class _FailingSpendRecorder(_SpendRecorder):
        async def log_request_failure(self, **kwargs):
            del kwargs
            raise RuntimeError("failure log sink unavailable")

    deployment_obj = SimpleNamespace(
        deployment_id="dep-1",
        deltallm_params={"model": "vllm/sentence-transformers/all-MiniLM-L6-v2", "api_base": "http://localhost:9090/v1"},
        input_cost_per_token=0.001,
        output_cost_per_token=0.0,
        model_info={},
    )
    repo = _FailureRepo()
    spend = _FailingSpendRecorder()
    passive_health = _PassiveHealthRecorder()
    app = SimpleNamespace(
        state=SimpleNamespace(
            router=_Router(),
            failover_manager=_Failover(),
            spend_tracking_service=spend,
            budget_service=_BudgetService(),
            passive_health_tracker=passive_health,
            router_state_backend=_RouterStateBackend(),
        )
    )
    worker = BatchExecutorWorker(
        app=app,
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )

    now = datetime.now(tz=UTC)
    job = BatchJobRecord(
        batch_id="b1",
        endpoint="/v1/embeddings",
        status=BatchJobStatus.IN_PROGRESS,
        execution_mode="managed_internal",
        input_file_id="f1",
        output_file_id=None,
        error_file_id=None,
        model="m-1",
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
        created_by_api_key="tok-1",
        created_by_user_id="u1",
        created_by_team_id="t1",
        created_at=now,
        started_at=now,
        completed_at=None,
        expires_at=None,
        created_by_organization_id="org-1",
    )
    item = BatchItemRecord(
        item_id="i1",
        batch_id="b1",
        line_number=1,
        custom_id="c1",
        status="in_progress",
        request_body={"model": "m-1", "input": "hello"},
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

    await worker._process_item(job, item)

    assert len(repo.failed_calls) == 1
    assert passive_health.calls == [("dep-1", False, "provider down")]


@pytest.mark.asyncio
async def test_batch_worker_marks_item_failed_when_budget_check_raises():
    class _BudgetService:
        async def check_budgets(self, **kwargs):  # noqa: ANN003, ANN201
            del kwargs
            raise HTTPException(status_code=429, detail="Budget exceeded")

    class _Router:
        def resolve_model_group(self, model: str) -> str:
            del model
            raise AssertionError("routing should not be reached after budget failure")

    class _FailureRepo(_FakeRepository):
        def __init__(self) -> None:
            super().__init__()
            self.failed_calls: list[dict] = []

        async def mark_item_failed(self, **kwargs) -> bool:
            self.failed_calls.append(kwargs)
            return True

        async def refresh_job_progress(self, batch_id: str):
            assert batch_id == "b1"
            return None

    repo = _FailureRepo()
    spend = _SpendRecorder()
    passive_health = _PassiveHealthRecorder()
    app = SimpleNamespace(
        state=SimpleNamespace(
            router=_Router(),
            failover_manager=SimpleNamespace(),
            spend_tracking_service=spend,
            budget_service=_BudgetService(),
            passive_health_tracker=passive_health,
            router_state_backend=None,
        )
    )
    worker = BatchExecutorWorker(
        app=app,
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )

    now = datetime.now(tz=UTC)
    job = BatchJobRecord(
        batch_id="b1",
        endpoint="/v1/embeddings",
        status=BatchJobStatus.IN_PROGRESS,
        execution_mode="managed_internal",
        input_file_id="f1",
        output_file_id=None,
        error_file_id=None,
        model="m-1",
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
        created_by_api_key="tok-1",
        created_by_user_id="u1",
        created_by_team_id="t1",
        created_at=now,
        started_at=now,
        completed_at=None,
        expires_at=None,
    )
    item = BatchItemRecord(
        item_id="i1",
        batch_id="b1",
        line_number=1,
        custom_id="c1",
        status="in_progress",
        request_body={"model": "m-1", "input": "hello"},
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

    await worker._process_item(job, item)

    assert len(repo.failed_calls) == 1
    assert repo.failed_calls[0]["retryable"] is False
    assert "Budget exceeded" in repo.failed_calls[0]["last_error"]
    assert passive_health.calls == []
    assert len(spend.events) == 1
    assert spend.events[0]["status"] == "error"
    assert spend.events[0]["request_id"] == "batch:b1:i1"


@pytest.mark.asyncio
async def test_batch_worker_marks_item_failed_when_route_selection_raises():
    class _BudgetService:
        async def check_budgets(self, **kwargs):  # noqa: ANN003, ANN201
            del kwargs
            return None

    class _Router:
        def resolve_model_group(self, model: str) -> str:
            return model

        async def select_deployment(self, model_group: str, request_context: dict) -> str:
            del model_group, request_context
            raise RuntimeError("routing unavailable")

        def require_deployment(self, model_group: str, deployment: str):
            del model_group, deployment
            raise AssertionError("require_deployment should not be reached when selection fails")

    class _FailureRepo(_FakeRepository):
        def __init__(self) -> None:
            super().__init__()
            self.failed_calls: list[dict] = []

        async def mark_item_failed(self, **kwargs) -> bool:
            self.failed_calls.append(kwargs)
            return True

        async def refresh_job_progress(self, batch_id: str):
            assert batch_id == "b1"
            return None

    repo = _FailureRepo()
    spend = _SpendRecorder()
    passive_health = _PassiveHealthRecorder()
    app = SimpleNamespace(
        state=SimpleNamespace(
            router=_Router(),
            failover_manager=SimpleNamespace(),
            spend_tracking_service=spend,
            budget_service=_BudgetService(),
            passive_health_tracker=passive_health,
            router_state_backend=None,
        )
    )
    worker = BatchExecutorWorker(
        app=app,
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )

    now = datetime.now(tz=UTC)
    job = BatchJobRecord(
        batch_id="b1",
        endpoint="/v1/embeddings",
        status=BatchJobStatus.IN_PROGRESS,
        execution_mode="managed_internal",
        input_file_id="f1",
        output_file_id=None,
        error_file_id=None,
        model="m-1",
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
        created_by_api_key="tok-1",
        created_by_user_id="u1",
        created_by_team_id="t1",
        created_at=now,
        started_at=now,
        completed_at=None,
        expires_at=None,
    )
    item = BatchItemRecord(
        item_id="i1",
        batch_id="b1",
        line_number=1,
        custom_id="c1",
        status="in_progress",
        request_body={"model": "m-1", "input": "hello"},
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

    await worker._process_item(job, item)

    assert len(repo.failed_calls) == 1
    assert repo.failed_calls[0]["retryable"] is False
    assert repo.failed_calls[0]["last_error"] == "routing unavailable"
    assert passive_health.calls == []
    assert len(spend.events) == 1
    assert spend.events[0]["status"] == "error"


def test_batch_worker_resolve_final_status_prefers_operator_failed_marker():
    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=_FakeRepository(),  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )
    now = datetime.now(tz=UTC)
    job = BatchJobRecord(
        batch_id="b1",
        endpoint="/v1/embeddings",
        status=BatchJobStatus.FINALIZING,
        execution_mode="managed_internal",
        input_file_id="f1",
        output_file_id=None,
        error_file_id=None,
        model="m-1",
        metadata={},
        provider_batch_id=None,
        provider_status=None,
        provider_error=encode_operator_failed_reason("manual stop"),
        provider_last_sync_at=None,
        total_items=2,
        in_progress_items=0,
        completed_items=1,
        failed_items=1,
        cancelled_items=0,
        locked_by="w1",
        lease_expires_at=now,
        cancel_requested_at=None,
        status_last_updated_at=now,
        created_by_api_key="tok-1",
        created_by_user_id="u1",
        created_by_team_id="t1",
        created_at=now,
        started_at=now,
        completed_at=None,
        expires_at=None,
    )

    assert worker._resolve_final_status(job) == BatchJobStatus.FAILED


@pytest.mark.asyncio
async def test_batch_worker_processes_items_with_bounded_concurrency(monkeypatch):
    active_calls = 0
    max_active_calls = 0

    async def _slow_execute_embedding(request, payload, deployment):
        nonlocal active_calls, max_active_calls
        del request, payload, deployment
        active_calls += 1
        max_active_calls = max(max_active_calls, active_calls)
        try:
            await asyncio.sleep(0.05)
            return {"object": "list", "data": [{"index": 0, "embedding": [0.1]}], "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5}}
        finally:
            active_calls -= 1

    monkeypatch.setattr("src.batch.worker._execute_embedding", _slow_execute_embedding)

    deployment = SimpleNamespace(
        deltallm_params={"model": "vllm/sentence-transformers/all-MiniLM-L6-v2", "api_base": "http://localhost:9090/v1"},
        input_cost_per_token=0.001,
        output_cost_per_token=0.0,
        model_info={"batch_input_cost_per_token": 0.0005, "batch_output_cost_per_token": 0.0},
    )

    class _Router:
        def resolve_model_group(self, model: str) -> str:
            return model

        async def select_deployment(self, model_group: str, request_context: dict) -> str:
            del model_group, request_context
            return "dep-1"

        def require_deployment(self, model_group: str, deployment: str):
            del model_group, deployment
            return deployment_obj

    class _Failover:
        async def execute_with_failover(self, *, primary_deployment, model_group, execute, return_deployment=False, **kwargs):  # noqa: ANN001
            del model_group, kwargs
            data = await execute(primary_deployment)
            if return_deployment:
                return data, primary_deployment
            return data

    class _ConcurrencyRepo(_FakeRepository):
        def __init__(self) -> None:
            super().__init__()
            self.claim_count = 0
            self.released = False
            self.now = datetime.now(tz=UTC)

        async def claim_next_job(self, *, worker_id: str, lease_seconds: int = 30):
            del worker_id, lease_seconds
            self.claim_count += 1
            if self.claim_count > 1:
                return None
            return BatchJobRecord(
                batch_id="b1",
                endpoint="/v1/embeddings",
                status=BatchJobStatus.IN_PROGRESS,
                execution_mode="managed_internal",
                input_file_id="f1",
                output_file_id=None,
                error_file_id=None,
                model="m-1",
                metadata={},
                provider_batch_id=None,
                provider_status=None,
                provider_error=None,
                provider_last_sync_at=None,
                total_items=4,
                in_progress_items=4,
                completed_items=0,
                failed_items=0,
                cancelled_items=0,
                locked_by="w1",
                lease_expires_at=self.now,
                cancel_requested_at=None,
                status_last_updated_at=self.now,
                created_by_api_key="tok-1",
                created_by_user_id="u1",
                created_by_team_id="t1",
                created_at=self.now,
                started_at=self.now,
                completed_at=None,
                expires_at=None,
            )

        async def claim_items(self, **kwargs):
            del kwargs
            return [
                BatchItemRecord(
                    item_id=f"i{index}",
                    batch_id="b1",
                    line_number=index,
                    custom_id=f"c{index}",
                    status="in_progress",
                    request_body={"model": "m-1", "input": f"hello-{index}"},
                    response_body=None,
                    error_body=None,
                    usage=None,
                    provider_cost=0.0,
                    billed_cost=0.0,
                    attempts=0,
                    last_error=None,
                    locked_by="w1",
                    lease_expires_at=self.now,
                    created_at=self.now,
                    started_at=self.now,
                    completed_at=None,
                )
                for index in range(1, 5)
            ]

        async def refresh_job_progress(self, batch_id: str):
            assert batch_id == "b1"
            return None

        async def release_job_lease(self, *, batch_id: str, worker_id: str) -> None:
            assert batch_id == "b1"
            assert worker_id == "w1"
            self.released = True

    deployment_obj = deployment
    repo = _ConcurrencyRepo()
    spend = _SpendRecorder()
    app = SimpleNamespace(state=SimpleNamespace(router=_Router(), failover_manager=_Failover(), spend_tracking_service=spend))
    worker = BatchExecutorWorker(
        app=app,
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1", worker_concurrency=2, item_buffer_multiplier=2),
    )
    worker._running = True

    did_work = await worker.process_once()
    worker._running = False

    assert did_work is True
    assert len(repo.completed_calls) == 4
    assert max_active_calls == 2
    assert repo.released is True


@pytest.mark.asyncio
async def test_batch_worker_processes_cancel_requested_job():
    now = datetime.now(tz=UTC)

    class _CancelRepo:
        def __init__(self) -> None:
            self.cancel_called = False
            self.released = False
            self.claim_count = 0
            self.refreshed = False

        async def claim_next_job(self, *, worker_id: str, lease_seconds: int = 30):
            del worker_id, lease_seconds
            self.claim_count += 1
            if self.claim_count > 1:
                return None
            return BatchJobRecord(
                batch_id="b-cancel",
                endpoint="/v1/embeddings",
                status=BatchJobStatus.IN_PROGRESS,
                execution_mode="managed_internal",
                input_file_id="f-1",
                output_file_id=None,
                error_file_id=None,
                model="m-1",
                metadata={},
                provider_batch_id=None,
                provider_status=None,
                provider_error=None,
                provider_last_sync_at=None,
                total_items=0,
                in_progress_items=0,
                completed_items=0,
                failed_items=0,
                cancelled_items=0,
                locked_by=None,
                lease_expires_at=None,
                cancel_requested_at=now,
                status_last_updated_at=now,
                created_by_api_key="tok-1",
                created_by_user_id=None,
                created_by_team_id=None,
                created_at=now,
                started_at=now,
                completed_at=None,
                expires_at=None,
            )

        async def mark_pending_items_cancelled(self, batch_id: str) -> None:
            assert batch_id == "b-cancel"
            self.cancel_called = True

        async def claim_items(self, **kwargs):
            del kwargs
            return []

        async def refresh_job_progress(self, batch_id: str):
            assert batch_id == "b-cancel"
            self.refreshed = True
            return BatchJobRecord(
                batch_id="b-cancel",
                endpoint="/v1/embeddings",
                status=BatchJobStatus.FINALIZING,
                execution_mode="managed_internal",
                input_file_id="f-1",
                output_file_id=None,
                error_file_id=None,
                model="m-1",
                metadata={},
                provider_batch_id=None,
                provider_status=None,
                provider_error=None,
                provider_last_sync_at=None,
                total_items=0,
                in_progress_items=0,
                completed_items=0,
                failed_items=0,
                cancelled_items=0,
                locked_by=None,
                lease_expires_at=None,
                cancel_requested_at=now,
                status_last_updated_at=now,
                created_by_api_key="tok-1",
                created_by_user_id=None,
                created_by_team_id=None,
                created_at=now,
                started_at=now,
                completed_at=now,
                expires_at=None,
            )

        async def release_job_lease(self, *, batch_id: str, worker_id: str) -> None:
            assert batch_id == "b-cancel"
            assert worker_id == "w1"
            self.released = True

    repo = _CancelRepo()
    app = SimpleNamespace(state=SimpleNamespace())
    worker = BatchExecutorWorker(
        app=app,
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )
    finalized: list[str] = []

    async def _finalize(job):
        finalized.append(job.batch_id)

    worker._finalize_artifacts = _finalize  # type: ignore[assignment]

    did_work = await worker.process_once()
    assert did_work is True
    assert repo.cancel_called is True
    assert repo.refreshed is True
    assert repo.released is True
    assert finalized == ["b-cancel"]


@pytest.mark.asyncio
async def test_batch_status_remains_completed_across_service_instances():
    now = datetime.now(tz=UTC)
    repo = _FakeRepository()
    repo.job = BatchJobRecord(
        batch_id="batch-completed-1",
        endpoint="/v1/embeddings",
        status=BatchJobStatus.COMPLETED,
        execution_mode="managed_internal",
        input_file_id="file-1",
        output_file_id="file-out-1",
        error_file_id=None,
        model="m-1",
        metadata={},
        provider_batch_id=None,
        provider_status=None,
        provider_error=None,
        provider_last_sync_at=None,
        total_items=3,
        in_progress_items=0,
        completed_items=3,
        failed_items=0,
        cancelled_items=0,
        locked_by=None,
        lease_expires_at=None,
        cancel_requested_at=None,
        status_last_updated_at=now,
        created_by_api_key="tok-1",
        created_by_user_id=None,
        created_by_team_id=None,
        created_at=now,
        started_at=now,
        completed_at=now,
        expires_at=None,
    )

    auth = UserAPIKeyAuth(api_key="tok-1")
    service_a = BatchService(repository=repo, storage=_FakeStorage())  # type: ignore[arg-type]
    service_b = BatchService(repository=repo, storage=_FakeStorage())  # type: ignore[arg-type]

    first = await service_a.get_batch(batch_id=repo.job.batch_id, auth=auth)
    second = await service_b.get_batch(batch_id=repo.job.batch_id, auth=auth)

    assert first["status"] == "completed"
    assert second["status"] == "completed"
    assert second["request_counts"] == {"total": 3, "completed": 3, "failed": 0, "cancelled": 0, "in_progress": 0}


@pytest.mark.asyncio
async def test_batch_worker_skips_spend_logging_when_item_completion_loses_ownership(monkeypatch):
    async def _fake_execute_embedding(request, payload, deployment):
        del request, payload, deployment
        return {"object": "list", "data": [{"index": 0, "embedding": [0.1]}], "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5}}

    monkeypatch.setattr("src.batch.worker._execute_embedding", _fake_execute_embedding)

    class _LeaseLostRepository(_FakeRepository):
        async def mark_item_completed(self, **kwargs) -> bool:
            self.completed_calls.append(kwargs)
            return False

    deployment = SimpleNamespace(
        deltallm_params={"model": "vllm/sentence-transformers/all-MiniLM-L6-v2", "api_base": "http://localhost:9090/v1"},
        input_cost_per_token=0.001,
        output_cost_per_token=0.0,
        model_info={"batch_input_cost_per_token": 0.0005, "batch_output_cost_per_token": 0.0},
    )

    class _Router:
        def resolve_model_group(self, model: str) -> str:
            return model

        async def select_deployment(self, model_group: str, request_context: dict) -> str:
            del model_group, request_context
            return "dep-1"

        def require_deployment(self, model_group: str, deployment: str):
            del model_group, deployment
            return deployment_obj

    class _Failover:
        async def execute_with_failover(self, *, primary_deployment, model_group, execute, return_deployment=False, **kwargs):  # noqa: ANN001
            del model_group, kwargs
            data = await execute(primary_deployment)
            if return_deployment:
                return data, primary_deployment
            return data

    deployment_obj = deployment
    repo = _LeaseLostRepository()
    spend = _SpendRecorder()
    app = SimpleNamespace(state=SimpleNamespace(router=_Router(), failover_manager=_Failover(), spend_tracking_service=spend))
    worker = BatchExecutorWorker(
        app=app,
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )

    now = datetime.now(tz=UTC)
    job = BatchJobRecord(
        batch_id="b1",
        endpoint="/v1/embeddings",
        status=BatchJobStatus.IN_PROGRESS,
        execution_mode="managed_internal",
        input_file_id="f1",
        output_file_id=None,
        error_file_id=None,
        model="m-1",
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
        created_by_api_key="tok-1",
        created_by_user_id="u1",
        created_by_team_id="t1",
        created_at=now,
        started_at=now,
        completed_at=None,
        expires_at=None,
    )
    item = BatchItemRecord(
        item_id="i1",
        batch_id="b1",
        line_number=1,
        custom_id="c1",
        status="in_progress",
        request_body={"model": "m-1", "input": "hello"},
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

    await worker._process_item(job, item)

    assert len(repo.completed_calls) == 1
    assert spend.events == []


@pytest.mark.asyncio
async def test_batch_worker_retries_finalizing_job_after_storage_failure(caplog: pytest.LogCaptureFixture):
    now = datetime.now(tz=UTC)

    class _FinalizingRepo:
        def __init__(self) -> None:
            self.claim_count = 0
            self.released = 0
            self.deleted_files: list[str] = []
            self.finalized = False
            self.created_files = 0
            self.created_file_calls: list[dict] = []
            self.rescheduled: list[int] = []
            self.job = BatchJobRecord(
                batch_id="b-finalize",
                endpoint="/v1/embeddings",
                status=BatchJobStatus.FINALIZING,
                execution_mode="managed_internal",
                input_file_id="f-1",
                output_file_id=None,
                error_file_id=None,
                model="m-1",
                metadata={},
                provider_batch_id=None,
                provider_status=None,
                provider_error=None,
                provider_last_sync_at=None,
                total_items=2,
                in_progress_items=0,
                completed_items=1,
                failed_items=1,
                cancelled_items=0,
                locked_by="w1",
                lease_expires_at=now,
                cancel_requested_at=None,
                status_last_updated_at=now,
                created_by_api_key="tok-1",
                created_by_user_id="u1",
                created_by_team_id="t1",
                created_at=now,
                started_at=now,
                completed_at=None,
                expires_at=None,
                created_by_organization_id="org-1",
            )

        async def claim_next_job(self, *, worker_id: str, lease_seconds: int = 30):
            del worker_id, lease_seconds
            self.claim_count += 1
            if self.claim_count > 2:
                return None
            return self.job

        async def list_items(self, batch_id: str):
            assert batch_id == "b-finalize"
            return [
                BatchItemRecord(
                    item_id="i1",
                    batch_id=batch_id,
                    line_number=1,
                    custom_id="req-1",
                    status="completed",
                    request_body={},
                    response_body={"ok": True},
                    error_body=None,
                    usage=None,
                    provider_cost=0.0,
                    billed_cost=0.0,
                    attempts=1,
                    last_error=None,
                    locked_by=None,
                    lease_expires_at=None,
                    created_at=now,
                    started_at=now,
                    completed_at=now,
                ),
                BatchItemRecord(
                    item_id="i2",
                    batch_id=batch_id,
                    line_number=2,
                    custom_id="req-2",
                    status="failed",
                    request_body={},
                    response_body=None,
                    error_body={"message": "boom"},
                    usage=None,
                    provider_cost=0.0,
                    billed_cost=0.0,
                    attempts=1,
                    last_error="boom",
                    locked_by=None,
                    lease_expires_at=None,
                    created_at=now,
                    started_at=now,
                    completed_at=now,
                ),
            ]

        async def create_file(self, **kwargs):
            self.created_files += 1
            self.created_file_calls.append(kwargs)
            return SimpleNamespace(file_id=f"file-{self.created_files}", storage_key=kwargs["storage_key"])

        async def reschedule_finalization(self, *, batch_id: str, worker_id: str, retry_delay_seconds: int) -> bool:
            assert batch_id == "b-finalize"
            assert worker_id == "w1"
            self.rescheduled.append(retry_delay_seconds)
            return True

        async def attach_artifacts_and_finalize(self, **kwargs):
            self.finalized = True
            return self.job

        async def release_job_lease(self, *, batch_id: str, worker_id: str) -> None:
            assert batch_id == "b-finalize"
            assert worker_id == "w1"
            self.released += 1

        async def delete_file(self, file_id: str) -> None:
            self.deleted_files.append(file_id)

    class _FlakyStorage:
        backend_name = "s3"

        def __init__(self) -> None:
            self.write_calls = 0
            self.deleted: list[str] = []

        async def write_lines_stream(self, *, purpose: str, filename: str, lines):  # noqa: ANN001
            del filename, lines
            self.write_calls += 1
            if self.write_calls == 2:
                raise RuntimeError("storage unavailable")
            return f"{purpose}/{self.write_calls}.jsonl", 10, "checksum"

        async def delete(self, storage_key: str) -> None:
            self.deleted.append(storage_key)

    repo = _FinalizingRepo()
    storage = _FlakyStorage()
    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=repo,  # type: ignore[arg-type]
        storage=storage,  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )

    with caplog.at_level(logging.INFO):
        did_work = await worker.process_once()

    assert did_work is True
    assert storage.deleted == ["batch_output/1.jsonl"]
    assert repo.deleted_files == ["file-1"]
    assert repo.released == 1
    assert repo.finalized is False
    assert repo.rescheduled == [60]
    assert "batch finalization retry scheduled batch_id=b-finalize worker_id=w1 delay_seconds=60" in caplog.text

    did_work = await worker.process_once()

    assert did_work is True
    assert repo.finalized is True
    assert repo.released == 2
    assert repo.created_file_calls
    assert all(call["created_by_organization_id"] == "org-1" for call in repo.created_file_calls)


@pytest.mark.asyncio
async def test_batch_worker_renews_job_and_item_leases_during_long_execution(monkeypatch):
    async def _slow_execute_embedding(request, payload, deployment):
        del request, payload, deployment
        await asyncio.sleep(0.05)
        return {"object": "list", "data": [{"index": 0, "embedding": [0.1]}], "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5}}

    monkeypatch.setattr("src.batch.worker._execute_embedding", _slow_execute_embedding)

    deployment = SimpleNamespace(
        deltallm_params={"model": "vllm/sentence-transformers/all-MiniLM-L6-v2", "api_base": "http://localhost:9090/v1"},
        input_cost_per_token=0.001,
        output_cost_per_token=0.0,
        model_info={"batch_input_cost_per_token": 0.0005, "batch_output_cost_per_token": 0.0},
    )

    class _Router:
        def resolve_model_group(self, model: str) -> str:
            return model

        async def select_deployment(self, model_group: str, request_context: dict) -> str:
            del model_group, request_context
            return "dep-1"

        def require_deployment(self, model_group: str, deployment: str):
            del model_group, deployment
            return deployment_obj

    class _Failover:
        async def execute_with_failover(self, *, primary_deployment, model_group, execute, return_deployment=False, **kwargs):  # noqa: ANN001
            del model_group, kwargs
            data = await execute(primary_deployment)
            if return_deployment:
                return data, primary_deployment
            return data

    class _LeaseRepo(_FakeRepository):
        def __init__(self) -> None:
            super().__init__()
            self.job_claims = 0
            self.renew_job_calls = 0
            self.renew_item_calls = 0
            self.released = False
            self.now = datetime.now(tz=UTC)

        async def claim_next_job(self, *, worker_id: str, lease_seconds: int = 30):
            del worker_id, lease_seconds
            self.job_claims += 1
            if self.job_claims > 1:
                return None
            return BatchJobRecord(
                batch_id="b1",
                endpoint="/v1/embeddings",
                status=BatchJobStatus.IN_PROGRESS,
                execution_mode="managed_internal",
                input_file_id="f1",
                output_file_id=None,
                error_file_id=None,
                model="m-1",
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
                locked_by="w1",
                lease_expires_at=self.now,
                cancel_requested_at=None,
                status_last_updated_at=self.now,
                created_by_api_key="tok-1",
                created_by_user_id="u1",
                created_by_team_id="t1",
                created_at=self.now,
                started_at=self.now,
                completed_at=None,
                expires_at=None,
            )

        async def claim_items(self, **kwargs):
            del kwargs
            return [
                BatchItemRecord(
                    item_id="i1",
                    batch_id="b1",
                    line_number=1,
                    custom_id="c1",
                    status="in_progress",
                    request_body={"model": "m-1", "input": "hello"},
                    response_body=None,
                    error_body=None,
                    usage=None,
                    provider_cost=0.0,
                    billed_cost=0.0,
                    attempts=0,
                    last_error=None,
                    locked_by="w1",
                    lease_expires_at=self.now,
                    created_at=self.now,
                    started_at=self.now,
                    completed_at=None,
                )
            ]

        async def renew_job_lease(self, *, batch_id: str, worker_id: str, lease_seconds: int) -> bool:
            assert batch_id == "b1"
            assert worker_id == "w1"
            assert lease_seconds == 360
            self.renew_job_calls += 1
            return True

        async def renew_item_lease(self, *, item_id: str, worker_id: str, lease_seconds: int) -> bool:
            assert item_id == "i1"
            assert worker_id == "w1"
            assert lease_seconds == 360
            self.renew_item_calls += 1
            return True

        async def refresh_job_progress(self, batch_id: str):
            assert batch_id == "b1"
            return None

        async def release_job_lease(self, *, batch_id: str, worker_id: str) -> None:
            assert batch_id == "b1"
            assert worker_id == "w1"
            self.released = True

    deployment_obj = deployment
    repo = _LeaseRepo()
    spend = _SpendRecorder()
    app = SimpleNamespace(state=SimpleNamespace(router=_Router(), failover_manager=_Failover(), spend_tracking_service=spend))
    worker = BatchExecutorWorker(
        app=app,
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1", heartbeat_interval_seconds=0.01, job_lease_seconds=360, item_lease_seconds=360),
    )
    worker._running = True

    did_work = await worker.process_once()
    worker._running = False

    assert did_work is True
    assert repo.renew_job_calls >= 1
    assert repo.renew_item_calls >= 1
    assert repo.released is True


@pytest.mark.asyncio
async def test_second_worker_reclaims_expired_in_progress_item_after_crash(monkeypatch):
    async def _fake_execute_embedding(request, payload, deployment):
        del request, payload, deployment
        return {"object": "list", "data": [{"index": 0, "embedding": [0.1]}], "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5}}

    monkeypatch.setattr("src.batch.worker._execute_embedding", _fake_execute_embedding)

    deployment = SimpleNamespace(
        deltallm_params={"model": "vllm/sentence-transformers/all-MiniLM-L6-v2", "api_base": "http://localhost:9090/v1"},
        input_cost_per_token=0.001,
        output_cost_per_token=0.0,
        model_info={"batch_input_cost_per_token": 0.0005, "batch_output_cost_per_token": 0.0},
    )

    class _Router:
        def resolve_model_group(self, model: str) -> str:
            return model

        async def select_deployment(self, model_group: str, request_context: dict) -> str:
            del model_group, request_context
            return "dep-1"

        def require_deployment(self, model_group: str, deployment: str):
            del model_group, deployment
            return deployment_obj

    class _Failover:
        async def execute_with_failover(self, *, primary_deployment, model_group, execute, return_deployment=False, **kwargs):  # noqa: ANN001
            del model_group, kwargs
            data = await execute(primary_deployment)
            if return_deployment:
                return data, primary_deployment
            return data

    class _CrashRecoveryRepo:
        def __init__(self) -> None:
            self.now = datetime.now(tz=UTC)
            self.job_status = BatchJobStatus.IN_PROGRESS
            self.job_locked_by: str | None = None
            self.job_lease_expires_at = self.now - timedelta(seconds=1)
            self.item_status = "pending"
            self.item_locked_by: str | None = None
            self.item_lease_expires_at: datetime | None = None
            self.completed_by: str | None = None

        def advance(self, seconds: int) -> None:
            self.now += timedelta(seconds=seconds)

        def _job_record(self) -> BatchJobRecord:
            return BatchJobRecord(
                batch_id="b1",
                endpoint="/v1/embeddings",
                status=self.job_status,
                execution_mode="managed_internal",
                input_file_id="f1",
                output_file_id=None,
                error_file_id=None,
                model="m-1",
                metadata={},
                provider_batch_id=None,
                provider_status=None,
                provider_error=None,
                provider_last_sync_at=None,
                total_items=1,
                in_progress_items=1 if self.item_status == "in_progress" else 0,
                completed_items=1 if self.item_status == "completed" else 0,
                failed_items=0,
                cancelled_items=0,
                locked_by=self.job_locked_by,
                lease_expires_at=self.job_lease_expires_at,
                cancel_requested_at=None,
                status_last_updated_at=self.now,
                created_by_api_key="tok-1",
                created_by_user_id="u1",
                created_by_team_id="t1",
                created_at=self.now,
                started_at=self.now,
                completed_at=None,
                expires_at=None,
            )

        def _item_record(self) -> BatchItemRecord:
            return BatchItemRecord(
                item_id="i1",
                batch_id="b1",
                line_number=1,
                custom_id="c1",
                status=self.item_status,
                request_body={"model": "m-1", "input": "hello"},
                response_body=None,
                error_body=None,
                usage=None,
                provider_cost=0.0,
                billed_cost=0.0,
                attempts=0,
                last_error=None,
                locked_by=self.item_locked_by,
                lease_expires_at=self.item_lease_expires_at,
                created_at=self.now,
                started_at=self.now,
                completed_at=None,
            )

        async def claim_next_job(self, *, worker_id: str, lease_seconds: int = 30):
            if self.job_lease_expires_at is not None and self.job_lease_expires_at >= self.now:
                return None
            self.job_locked_by = worker_id
            self.job_lease_expires_at = self.now + timedelta(seconds=lease_seconds)
            return self._job_record()

        async def claim_items(self, *, worker_id: str, lease_seconds: int = 60, **kwargs):
            del kwargs
            expired_in_progress = self.item_status == "in_progress" and self.item_lease_expires_at is not None and self.item_lease_expires_at < self.now
            if self.item_status == "pending" or expired_in_progress:
                self.item_status = "in_progress"
                self.item_locked_by = worker_id
                self.item_lease_expires_at = self.now + timedelta(seconds=lease_seconds)
                return [self._item_record()]
            return []

        async def mark_item_completed(self, *, item_id: str, worker_id: str | None, **kwargs) -> bool:
            del kwargs
            assert item_id == "i1"
            if worker_id != self.item_locked_by:
                return False
            self.item_status = "completed"
            self.item_locked_by = None
            self.item_lease_expires_at = None
            self.completed_by = worker_id
            return True

        async def mark_item_failed(self, **kwargs) -> bool:  # pragma: no cover
            raise AssertionError(f"unexpected failure path: {kwargs}")

        async def refresh_job_progress(self, batch_id: str):
            assert batch_id == "b1"
            return self._job_record()

        async def release_job_lease(self, *, batch_id: str, worker_id: str) -> None:
            assert batch_id == "b1"
            if self.job_locked_by == worker_id:
                self.job_locked_by = None
                self.job_lease_expires_at = self.now - timedelta(seconds=1)

        async def renew_job_lease(self, *, batch_id: str, worker_id: str, lease_seconds: int) -> bool:
            assert batch_id == "b1"
            if self.job_locked_by != worker_id:
                return False
            self.job_lease_expires_at = self.now + timedelta(seconds=lease_seconds)
            return True

        async def renew_item_lease(self, *, item_id: str, worker_id: str, lease_seconds: int) -> bool:
            assert item_id == "i1"
            if self.item_locked_by != worker_id:
                return False
            self.item_lease_expires_at = self.now + timedelta(seconds=lease_seconds)
            return True

    deployment_obj = deployment
    repo = _CrashRecoveryRepo()
    spend = _SpendRecorder()
    app = SimpleNamespace(state=SimpleNamespace(router=_Router(), failover_manager=_Failover(), spend_tracking_service=spend))
    worker_a = BatchExecutorWorker(
        app=app,
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1", item_lease_seconds=5),
    )
    worker_b = BatchExecutorWorker(
        app=app,
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w2", item_lease_seconds=5),
    )

    async def _crash(job, item):  # noqa: ANN001
        del job, item
        return None

    worker_a._process_item = _crash  # type: ignore[assignment]
    await worker_a.process_once()

    assert repo.item_status == "in_progress"
    assert repo.item_locked_by == "w1"

    repo.advance(10)
    did_work = await worker_b.process_once()

    assert did_work is True
    assert repo.item_status == "completed"
    assert repo.completed_by == "w2"


@pytest.mark.asyncio
async def test_heartbeat_continues_renewing_after_stop_requested():
    """stop() must not drop the heartbeat while an item is still in-flight —
    otherwise the lease expires and another worker duplicates the work."""
    renew_calls: list[float] = []

    async def _renew() -> bool:
        renew_calls.append(asyncio.get_event_loop().time())
        return True

    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=_FakeRepository(),  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1", heartbeat_interval_seconds=0.01),
    )
    worker._running = True

    heartbeat = worker._start_heartbeat(renew=_renew, label="item:test")
    await asyncio.sleep(0.03)
    worker.stop()
    await asyncio.sleep(0.05)
    await worker._stop_heartbeat(heartbeat)

    calls_before_stop = sum(1 for _ in renew_calls if _ > 0)
    assert calls_before_stop >= 2, "heartbeat should have continued renewing across stop()"


@pytest.mark.asyncio
async def test_heartbeat_exits_when_renewal_reports_lease_loss():
    calls = {"count": 0}

    async def _renew() -> bool:
        calls["count"] += 1
        return calls["count"] < 2

    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=_FakeRepository(),  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1", heartbeat_interval_seconds=0.01),
    )
    worker._running = True

    task = worker._start_heartbeat(renew=_renew, label="item:test")
    await asyncio.wait_for(task, timeout=1.0)
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_batch_worker_refresh_runtime_metrics_logs_debug_on_failure(caplog: pytest.LogCaptureFixture):
    class _Repo:
        async def summarize_runtime_statuses(self, *, now):  # noqa: ANN001
            del now
            raise RuntimeError("metrics unavailable")

    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=_Repo(),  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )

    with caplog.at_level(logging.DEBUG):
        await worker._refresh_batch_runtime_metrics()

    assert "batch worker runtime metrics refresh failed" in caplog.text


@pytest.mark.asyncio
async def test_batch_worker_refresh_runtime_metrics_logs_debug_on_publish_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    class _Repo:
        async def summarize_runtime_statuses(self, *, now):  # noqa: ANN001
            del now
            return {"queued": 1, "in_progress": 0, "finalizing": 0}

    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=_Repo(),  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )
    monkeypatch.setattr(
        "src.batch.worker.publish_batch_runtime_summary",
        lambda summary: (_ for _ in ()).throw(RuntimeError("publish unavailable")),  # noqa: ARG005
    )

    with caplog.at_level(logging.DEBUG):
        await worker._refresh_batch_runtime_metrics()

    assert "batch worker runtime metrics refresh failed" in caplog.text
