from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
import json
import logging
from types import SimpleNamespace

import pytest

from fastapi import HTTPException

from src.callbacks import CallbackManager, CustomLogger
from src.batch.models import (
    BatchItemRecord,
    BatchJobRecord,
    BatchJobStatus,
    BatchWorkClaim,
    BatchWorkRecommendation,
    encode_operator_failed_reason,
)
from src.batch.service import BatchService
from src.batch.worker import BatchArtifactValidationError, BatchExecutorWorker, BatchWorkerConfig
from src.batch.worker_types import BatchItemLeaseLostError
from src.db.repositories import KeyRecord
from src.guardrails.exceptions import GuardrailViolationError
from src.services.key_service import KeyService
from src.services.limit_counter import LimitCounter
from src.models.errors import ServiceUnavailableError
from src.models.responses import UserAPIKeyAuth


class _AllowAllCallableTargetGrantService:
    def resolve_policy_allowlist(self, auth):  # noqa: ANN001
        del auth
        return SimpleNamespace(allowlist=None, authoritative=True, fallback_reason=None)


@pytest.fixture(autouse=True)
def _default_batch_policy_grants(monkeypatch):
    original_init = BatchExecutorWorker.__init__

    def _init_with_default_policy(self, *args, **kwargs):  # noqa: ANN001
        app = kwargs.get("app")
        state = getattr(app, "state", None)
        if state is not None and not hasattr(state, "callable_target_grant_service"):
            state.callable_target_grant_service = _AllowAllCallableTargetGrantService()
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(BatchExecutorWorker, "__init__", _init_with_default_policy)


class _SpendRecorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def log_spend(self, **kwargs):
        self.events.append({"status": "success", **kwargs})

    async def log_spend_once(self, **kwargs):
        self.events.append({"status": "success_once", **kwargs})
        return "inserted"

    async def log_request_failure(self, **kwargs):
        self.events.append({"status": "error", "cost": 0.0, **kwargs})


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


class _FakeRepository:
    def __init__(self) -> None:
        self.completed_calls: list[dict] = []
        self.completion_outbox_calls: list[dict] = []
        self.release_for_retry_calls: list[dict] = []
        self.refresh_job_progress_calls: list[str] = []

    async def mark_item_completed(self, **kwargs) -> bool:
        self.completed_calls.append(kwargs)
        return True

    async def complete_items_with_outbox_bulk(self, **kwargs) -> str:
        worker_id = kwargs.get("worker_id")
        for item in kwargs["items"]:
            updated = await self.mark_item_completed(
                item_id=item["item_id"],
                worker_id=worker_id,
                response_body=item["response_body"],
                usage=item["usage"],
                provider_cost=item["provider_cost"],
                billed_cost=item["billed_cost"],
            )
            if not updated:
                return "not_owned"
            self.completion_outbox_calls.append(dict(item["outbox_payload"]))
        return "completed"

    async def mark_item_failed(self, **kwargs) -> bool:  # pragma: no cover
        raise AssertionError(f"unexpected failure path: {kwargs}")

    async def renew_item_lease(self, **kwargs) -> bool:
        del kwargs
        return True

    async def release_items_for_retry(self, **kwargs) -> list[str]:
        self.release_for_retry_calls.append(kwargs)
        return list(kwargs["item_ids"])

    async def refresh_job_progress(self, batch_id: str):
        self.refresh_job_progress_calls.append(batch_id)
        return None

    async def get_job(self, batch_id: str):  # for BatchService test
        return self.job if batch_id == self.job.batch_id else None


class _FailureRepository(_FakeRepository):
    def __init__(self) -> None:
        super().__init__()
        self.failed_calls: list[dict] = []

    async def mark_item_failed(self, **kwargs) -> bool:
        self.failed_calls.append(kwargs)
        return True

    async def refresh_job_progress(self, batch_id: str):
        assert batch_id == "b-chat"
        self.refresh_job_progress_calls.append(batch_id)
        return None


class _FakeStorage:
    async def write_lines(self, **kwargs):  # pragma: no cover
        raise AssertionError(f"unexpected artifact write in this test: {kwargs}")

    async def write_lines_stream(self, **kwargs):  # pragma: no cover
        raise AssertionError(f"unexpected streaming artifact write in this test: {kwargs}")

    async def delete(self, storage_key: str) -> None:  # pragma: no cover
        del storage_key
        raise AssertionError("unexpected artifact delete in this test")


def _valid_embedding_artifact_response_body() -> dict[str, object]:
    return {
        "object": "list",
        "data": [{"index": 0, "embedding": [0.1, 0.2]}],
        "model": "provider-embedding-model",
        "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
        "_provider": "openai",
    }


def test_batch_worker_idle_poll_delay_jitters_and_backs_off(monkeypatch):
    calls: list[tuple[float, float]] = []

    def _uniform(lower: float, upper: float) -> float:
        calls.append((lower, upper))
        return upper

    monkeypatch.setattr("src.batch.worker.random.uniform", _uniform)
    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=_FakeRepository(),  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1", poll_interval_seconds=1.0),
    )

    delays = [worker._next_idle_poll_delay_seconds() for _ in range(5)]

    assert delays == pytest.approx([1.2, 2.4, 4.8, 8.0, 8.0])
    assert calls[0] == pytest.approx((0.8, 1.2))
    assert calls[1] == pytest.approx((1.6, 2.4))
    assert calls[2] == pytest.approx((3.2, 4.8))
    assert calls[3] == pytest.approx((6.4, 8.0))
    assert worker._idle_backoff_attempts == 5

    worker._reset_idle_backoff()

    assert worker._idle_backoff_attempts == 0


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
    assert len(repo.completion_outbox_calls) == 1
    outbox_payload = repo.completion_outbox_calls[0]
    assert outbox_payload["call_type"] == "embedding_batch"
    assert outbox_payload["billed_cost"] == 0.0025
    assert outbox_payload["provider_cost"] == 0.005
    assert outbox_payload["organization_id"] == "org-1"
    assert outbox_payload["deployment_model"] == "vllm/sentence-transformers/all-MiniLM-L6-v2"
    assert spend.events == []


@pytest.mark.asyncio
async def test_batch_worker_processes_chat_item_with_chat_batch_accounting(monkeypatch):
    execute_calls: list[dict] = []
    router_usage_calls: list[tuple[str, str, dict[str, int]]] = []
    router_contexts: list[dict] = []

    async def _fake_execute_chat(request, payload, deployment, *, record_usage: bool = True):
        del request, deployment
        execute_calls.append({"model": payload.model, "record_usage": record_usage})
        return (
            {
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 1,
                "model": payload.model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "hello"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
            },
            12.0,
        )

    async def _patched_record_router_usage(state_backend, deployment_id: str, *, mode: str, usage: dict):
        del state_backend
        router_usage_calls.append((deployment_id, mode, dict(usage)))

    monkeypatch.setattr("src.batch.worker.execute_chat", _fake_execute_chat)
    monkeypatch.setattr("src.batch.worker.record_router_usage", _patched_record_router_usage)

    deployment = SimpleNamespace(
        deployment_id="dep-chat",
        deltallm_params={
            "provider": "groq",
            "model": "openai/gpt-oss-120b",
            "api_base": "https://api.groq.com/openai/v1",
        },
        input_cost_per_token=0.001,
        output_cost_per_token=0.002,
        model_info={"batch_input_cost_per_token": 0.0005, "batch_output_cost_per_token": 0.001},
    )

    class _Router:
        def resolve_model_group(self, model: str) -> str:
            return model

        async def select_deployment(self, model_group: str, request_context: dict) -> str:
            del model_group
            router_contexts.append(request_context)
            return "dep-chat"

        def require_deployment(self, model_group: str, deployment: str):
            del model_group, deployment
            return deployment_obj

    class _Failover:
        async def execute_with_failover(self, *, primary_deployment, model_group, execute, return_deployment=False, **kwargs):
            del model_group, kwargs
            data = await execute(primary_deployment)
            if return_deployment:
                return data, primary_deployment
            return data

    deployment_obj = deployment
    repo = _FakeRepository()
    passive_health = _PassiveHealthRecorder()
    app = SimpleNamespace(
        state=SimpleNamespace(
            router=_Router(),
            failover_manager=_Failover(),
            spend_tracking_service=None,
            passive_health_tracker=passive_health,
            router_state_backend=SimpleNamespace(),
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
        batch_id="b-chat",
        endpoint="/v1/chat/completions",
        status=BatchJobStatus.IN_PROGRESS,
        execution_mode="managed_internal",
        input_file_id="f1",
        output_file_id=None,
        error_file_id=None,
        model="gpt-oss",
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
        item_id="i-chat",
        batch_id="b-chat",
        line_number=1,
        custom_id="chat-1",
        status="in_progress",
        request_body={
            "model": "gpt-oss",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
            "metadata": {"tags": ["batch-blue"]},
        },
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

    assert router_contexts == [{"metadata": {"tags": ["batch-blue"]}, "user_id": "u1"}]
    assert execute_calls == [{"model": "gpt-oss", "record_usage": False}]
    assert passive_health.calls == [("dep-chat", True, None)]
    assert router_usage_calls == [("dep-chat", "chat", {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10})]
    assert len(repo.completed_calls) == 1
    completed = repo.completed_calls[0]
    assert completed["response_body"]["object"] == "chat.completion"
    assert completed["usage"] == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
    assert completed["billed_cost"] == pytest.approx(0.0065)
    assert completed["provider_cost"] == pytest.approx(0.013)
    assert len(repo.completion_outbox_calls) == 1
    outbox_payload = repo.completion_outbox_calls[0]
    assert outbox_payload["call_type"] == "chat_batch"
    assert outbox_payload["api_provider"] == "groq"
    assert outbox_payload["api_base"] == "https://api.groq.com/openai/v1"
    assert outbox_payload["deployment_model"] == "openai/gpt-oss-120b"


@pytest.mark.asyncio
async def test_batch_worker_logs_chat_request_failure_with_batch_metadata(monkeypatch):
    async def _fake_execute_chat(request, payload, deployment, *, record_usage: bool = True):
        del request, payload, deployment, record_usage
        raise RuntimeError("chat provider down")

    monkeypatch.setattr("src.batch.worker.execute_chat", _fake_execute_chat)

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
            return "dep-chat"

        def require_deployment(self, model_group: str, deployment: str):
            del model_group, deployment
            return deployment_obj

    class _Failover:
        async def execute_with_failover(self, *, primary_deployment, model_group, execute, return_deployment=False, **kwargs):
            del model_group, kwargs
            data = await execute(primary_deployment)
            if return_deployment:
                return data, primary_deployment
            return data

    class _FailureRepo(_FakeRepository):
        def __init__(self) -> None:
            super().__init__()
            self.failed_calls: list[dict] = []

        async def mark_item_failed(self, **kwargs) -> bool:
            self.failed_calls.append(kwargs)
            return True

        async def refresh_job_progress(self, batch_id: str):
            assert batch_id == "b-chat"
            return None

    deployment_obj = SimpleNamespace(
        deployment_id="dep-chat",
        deltallm_params={
            "provider": "groq",
            "model": "openai/gpt-oss-120b",
            "api_base": "https://api.groq.com/openai/v1",
        },
        input_cost_per_token=0.001,
        output_cost_per_token=0.002,
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
        batch_id="b-chat",
        endpoint="/v1/chat/completions",
        status=BatchJobStatus.IN_PROGRESS,
        execution_mode="managed_internal",
        input_file_id="f1",
        output_file_id=None,
        error_file_id=None,
        model="gpt-oss",
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
        item_id="i-chat",
        batch_id="b-chat",
        line_number=1,
        custom_id="chat-1",
        status="in_progress",
        request_body={
            "model": "gpt-oss",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        },
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
    assert repo.failed_calls[0]["item_id"] == "i-chat"
    assert passive_health.calls == [("dep-chat", False, "chat provider down")]
    assert len(spend.events) == 1
    assert spend.events[0]["status"] == "error"
    assert spend.events[0]["request_id"] == "batch:b-chat:i-chat"
    assert spend.events[0]["call_type"] == "chat_batch"
    assert spend.events[0]["metadata"] == {
        "batch_id": "b-chat",
        "batch_item_id": "i-chat",
        "custom_id": "chat-1",
        "endpoint": "/v1/chat/completions",
    }


def _build_chat_batch_job() -> BatchJobRecord:
    now = datetime.now(tz=UTC)
    return BatchJobRecord(
        batch_id="b-chat",
        endpoint="/v1/chat/completions",
        status=BatchJobStatus.IN_PROGRESS,
        execution_mode="managed_internal",
        input_file_id="f1",
        output_file_id=None,
        error_file_id=None,
        model="gpt-oss",
        metadata={},
        provider_batch_id=None,
        provider_status=None,
        provider_error=None,
        provider_last_sync_at=None,
        total_items=2,
        in_progress_items=2,
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


def _build_chat_batch_item(item_id: str, content: str, *, request_overrides: dict | None = None) -> BatchItemRecord:
    now = datetime.now(tz=UTC)
    request_body = {
        "model": "gpt-oss",
        "messages": [{"role": "user", "content": content}],
        "stream": False,
    }
    if request_overrides:
        request_body.update(request_overrides)
    return BatchItemRecord(
        item_id=item_id,
        batch_id="b-chat",
        line_number=int(item_id.rsplit("-", 1)[-1]) if "-" in item_id else 1,
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


def _build_chat_batch_worker(
    *,
    deployment_params: dict,
    repository: _FakeRepository | None = None,
    state_overrides: dict[str, object] | None = None,
):
    deployment = SimpleNamespace(
        deployment_id="dep-chat",
        deltallm_params=deployment_params,
        input_cost_per_token=0.001,
        output_cost_per_token=0.002,
        model_info={"batch_input_cost_per_token": 0.0005, "batch_output_cost_per_token": 0.001},
    )

    class _Router:
        def resolve_model_group(self, model: str) -> str:
            return model

        async def select_deployment(self, model_group: str, request_context: dict) -> object:
            del model_group, request_context
            return deployment

        def require_deployment(self, model_group: str, deployment: object):
            del model_group
            return deployment

    class _Failover:
        async def execute_with_failover(self, *, primary_deployment, model_group, execute, return_deployment=False, **kwargs):
            del model_group, kwargs
            data = await execute(primary_deployment)
            if return_deployment:
                return data, primary_deployment
            return data

    repo = repository or _FakeRepository()
    app_state = SimpleNamespace(
        router=_Router(),
        failover_manager=_Failover(),
        spend_tracking_service=None,
        passive_health_tracker=None,
        router_state_backend=None,
        settings=SimpleNamespace(openai_base_url="https://api.openai.com/v1"),
    )
    for key, value in (state_overrides or {}).items():
        setattr(app_state, key, value)
    app = SimpleNamespace(state=app_state)
    worker = BatchExecutorWorker(
        app=app,
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )
    return worker, repo


def _patch_fake_chat_execute(monkeypatch, execute_calls: list[str]) -> None:
    async def _fake_execute_chat(request, payload, deployment, *, record_usage: bool = True):
        del request, deployment, record_usage
        execute_calls.append(payload.messages[0].content)
        return (
            {
                "id": f"chatcmpl-{len(execute_calls)}",
                "object": "chat.completion",
                "created": 1,
                "model": payload.model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
            },
            5.0,
        )

    monkeypatch.setattr("src.batch.worker.execute_chat", _fake_execute_chat)


@pytest.mark.asyncio
async def test_batch_chat_pre_call_callback_transforms_provider_payload(monkeypatch):
    class _RewriteCallback(CustomLogger):
        async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):  # noqa: ANN001
            del user_api_key_dict, cache
            assert call_type == "completion"
            updated = dict(data)
            updated["messages"] = [{"role": "user", "content": "rewritten"}]
            return updated

    manager = CallbackManager()
    manager.register_callback(_RewriteCallback(), callback_type="success")
    execute_calls: list[str] = []
    _patch_fake_chat_execute(monkeypatch, execute_calls)

    worker, repo = _build_chat_batch_worker(
        deployment_params={"provider": "vllm", "model": "gpt-oss", "api_base": "http://localhost:9090/v1"},
        state_overrides={"callback_manager": manager},
    )

    await worker._process_item(_build_chat_batch_job(), _build_chat_batch_item("chat-1", "original"))

    assert execute_calls == ["rewritten"]
    assert len(repo.completed_calls) == 1


@pytest.mark.asyncio
async def test_batch_chat_guardrail_rejection_is_terminal_item_failure(monkeypatch):
    class _BlockingGuardrailMiddleware:
        async def run_pre_call(self, *, request_data, user_api_key_dict, call_type, override_guardrails=None):  # noqa: ANN001
            del request_data, user_api_key_dict, call_type, override_guardrails
            raise GuardrailViolationError(
                guardrail_name="blocker",
                message="blocked by policy",
                violation_type="blocked",
            )

    execute_calls: list[str] = []
    _patch_fake_chat_execute(monkeypatch, execute_calls)
    repo = _FailureRepository()
    worker, _ = _build_chat_batch_worker(
        deployment_params={"provider": "vllm", "model": "gpt-oss", "api_base": "http://localhost:9090/v1"},
        repository=repo,
        state_overrides={"guardrail_middleware": _BlockingGuardrailMiddleware()},
    )

    await worker._process_item(_build_chat_batch_job(), _build_chat_batch_item("chat-1", "blocked"))

    assert execute_calls == []
    assert len(repo.failed_calls) == 1
    error_body = repo.failed_calls[0]["error_body"]
    assert error_body["retryable"] is False
    assert error_body["retry_category"] == "invalid_request"
    assert error_body["terminal_reason"] == "not_retryable"


@pytest.mark.asyncio
async def test_batch_chat_releases_max_parallel_slot_after_provider_failure(monkeypatch):
    token_hash = "a" * 64

    class _KeyRepository:
        async def get_by_token(self, token_hash_arg: str) -> KeyRecord | None:
            assert token_hash_arg == token_hash
            return KeyRecord(token=token_hash, max_parallel_requests=1)

    async def _failing_execute_chat(request, payload, deployment, *, record_usage: bool = True):  # noqa: ANN001
        del request, payload, deployment, record_usage
        raise RuntimeError("provider failed")

    monkeypatch.setattr("src.batch.worker.execute_chat", _failing_execute_chat)

    limit_counter = LimitCounter()
    repo = _FailureRepository()
    worker, _ = _build_chat_batch_worker(
        deployment_params={"provider": "vllm", "model": "gpt-oss", "api_base": "http://localhost:9090/v1"},
        repository=repo,
        state_overrides={
            "key_service": KeyService(repository=_KeyRepository(), redis_client=None),
            "limit_counter": limit_counter,
        },
    )
    job = _build_chat_batch_job()
    job.created_by_api_key = token_hash

    await worker._process_item(job, _build_chat_batch_item("chat-1", "hello"))

    assert len(repo.failed_calls) == 1
    assert limit_counter._fallback_parallel == {}


@pytest.mark.asyncio
async def test_batch_chat_max_parallel_policy_failure_does_not_mark_passive_health(monkeypatch):
    token_hash = "b" * 64

    class _KeyRepository:
        async def get_by_token(self, token_hash_arg: str) -> KeyRecord | None:
            assert token_hash_arg == token_hash
            return KeyRecord(token=token_hash, max_parallel_requests=1)

    execute_calls: list[str] = []
    _patch_fake_chat_execute(monkeypatch, execute_calls)
    metric_calls: list[dict] = []
    monkeypatch.setattr(
        "src.batch.policy.increment_batch_policy_retryable_failure",
        lambda **kwargs: metric_calls.append(kwargs),
    )

    limit_counter = LimitCounter()
    await limit_counter.acquire_parallel("key", token_hash, 1)
    passive_health = _PassiveHealthRecorder()
    repo = _FailureRepository()
    worker, _ = _build_chat_batch_worker(
        deployment_params={"provider": "vllm", "model": "gpt-oss", "api_base": "http://localhost:9090/v1"},
        repository=repo,
        state_overrides={
            "key_service": KeyService(repository=_KeyRepository(), redis_client=None),
            "limit_counter": limit_counter,
            "passive_health_tracker": passive_health,
        },
    )
    job = _build_chat_batch_job()
    job.created_by_api_key = token_hash

    await worker._process_item(job, _build_chat_batch_item("chat-1", "hello"))

    assert execute_calls == []
    assert passive_health.calls == []
    assert len(repo.failed_calls) == 1
    error_body = repo.failed_calls[0]["error_body"]
    assert error_body["retryable"] is True
    assert error_body["retry_category"] == "rate_limit"
    assert metric_calls == [{"endpoint": "chat_batch", "reason": "rate_limit"}]
    assert limit_counter._fallback_parallel == {f"key:{token_hash}": 1}


@pytest.mark.asyncio
async def test_batch_chat_model_access_fails_closed_when_grant_service_missing(monkeypatch):
    execute_calls: list[str] = []
    _patch_fake_chat_execute(monkeypatch, execute_calls)
    repo = _FailureRepository()
    worker, _ = _build_chat_batch_worker(
        deployment_params={"provider": "vllm", "model": "gpt-oss", "api_base": "http://localhost:9090/v1"},
        repository=repo,
        state_overrides={"callable_target_grant_service": None},
    )

    await worker._process_item(_build_chat_batch_job(), _build_chat_batch_item("chat-1", "hello"))

    assert execute_calls == []
    assert len(repo.failed_calls) == 1
    error_body = repo.failed_calls[0]["error_body"]
    assert error_body["retryable"] is False
    assert error_body["retry_category"] == "permission"


@pytest.mark.asyncio
async def test_batch_worker_sync_microbatches_compatible_chat_items():
    class _ChatMicrobatchExecutor:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        async def execute_chat_microbatch(self, *, requests, deployment, request_context):  # noqa: ANN001
            del deployment
            self.calls.append([request.messages[0].content for request in requests])
            assert [request.metadata for request in requests] == [
                {"tenant": "shared"},
                {"tenant": "shared"},
            ]
            assert request_context["items"] == [
                {"item_id": "chat-1", "custom_id": "custom-chat-1", "line_number": 1},
                {"item_id": "chat-2", "custom_id": "custom-chat-2", "line_number": 2},
            ]
            return [
                {
                    "index": index,
                    "response_body": {
                        "id": f"chatcmpl-{index}",
                        "object": "chat.completion",
                        "created": 1,
                        "model": "gpt-oss",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": f"answer-{index}"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
                    },
                    "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
                }
                for index, _ in enumerate(requests)
            ]

    worker, repo = _build_chat_batch_worker(
        deployment_params={
            "provider": "vllm",
            "model": "openai/gpt-oss-120b",
            "api_base": "http://vllm.local/v1",
            "chat_batching": {"mode": "sync_microbatch", "upstream_max_batch_size": 4},
        }
    )
    worker.config.worker_concurrency = 1
    executor = _ChatMicrobatchExecutor()
    worker.app.state.chat_microbatch_executor = executor

    await worker._process_items(
        _build_chat_batch_job(),
        [
            _build_chat_batch_item("chat-1", "hello", request_overrides={"metadata": {"tenant": "shared"}}),
            _build_chat_batch_item("chat-2", "world", request_overrides={"metadata": {"tenant": "shared"}}),
        ],
    )

    assert executor.calls == [["hello", "world"]]
    assert len(repo.completed_calls) == 2
    assert [call["response_body"]["choices"][0]["message"]["content"] for call in repo.completed_calls] == [
        "answer-0",
        "answer-1",
    ]
    assert len(repo.completion_outbox_calls) == 2
    assert repo.completion_outbox_calls[0]["batch_execution_mode"] == "sync_microbatch"
    assert repo.completion_outbox_calls[0]["microbatch_size"] == 2
    assert repo.completion_outbox_calls[0]["api_provider"] == "vllm"


@pytest.mark.asyncio
async def test_batch_worker_sync_microbatch_falls_back_when_metadata_differs(monkeypatch):
    execute_calls: list[str] = []

    class _UnexpectedMicrobatchExecutor:
        def __init__(self) -> None:
            self.calls = 0

        async def execute_chat_microbatch(self, **kwargs):  # noqa: ANN003, ANN201
            self.calls += 1
            raise AssertionError(f"unexpected chat microbatch call: {kwargs}")

    _patch_fake_chat_execute(monkeypatch, execute_calls)
    worker, repo = _build_chat_batch_worker(
        deployment_params={
            "provider": "vllm",
            "model": "openai/gpt-oss-120b",
            "api_base": "http://vllm.local/v1",
            "chat_batching": {"mode": "sync_microbatch", "upstream_max_batch_size": 4},
        }
    )
    worker.config.worker_concurrency = 1
    executor = _UnexpectedMicrobatchExecutor()
    worker.app.state.chat_microbatch_executor = executor

    await worker._process_items(
        _build_chat_batch_job(),
        [
            _build_chat_batch_item("chat-1", "hello", request_overrides={"metadata": {"tenant": "a"}}),
            _build_chat_batch_item("chat-2", "world", request_overrides={"metadata": {"tenant": "b"}}),
        ],
    )

    assert executor.calls == 0
    assert execute_calls == ["hello", "world"]
    assert len(repo.completed_calls) == 2
    assert [payload["batch_execution_mode"] for payload in repo.completion_outbox_calls] == [
        "sync_microbatch_fallback",
        "sync_microbatch_fallback",
    ]


@pytest.mark.asyncio
async def test_batch_worker_sync_microbatch_uses_failover_served_deployment():
    primary = SimpleNamespace(
        deployment_id="dep-chat",
        deltallm_params={
            "provider": "vllm",
            "model": "openai/gpt-oss-120b",
            "api_base": "http://vllm.primary/v1",
            "chat_batching": {"mode": "sync_microbatch", "upstream_max_batch_size": 4},
        },
        input_cost_per_token=0.001,
        output_cost_per_token=0.002,
        model_info={"batch_input_cost_per_token": 0.0005, "batch_output_cost_per_token": 0.001},
    )
    fallback = SimpleNamespace(
        deployment_id="dep-chat-fallback",
        deltallm_params={
            "provider": "groq",
            "model": "openai/gpt-oss-120b",
            "api_base": "https://api.groq.com/openai/v1",
            "chat_batching": {"mode": "sync_microbatch", "upstream_max_batch_size": 4},
        },
        input_cost_per_token=0.003,
        output_cost_per_token=0.004,
        model_info={"batch_input_cost_per_token": 0.0015, "batch_output_cost_per_token": 0.002},
    )

    class _Router:
        def resolve_model_group(self, model: str) -> str:
            return model

        async def select_deployment(self, model_group: str, request_context: dict) -> object:
            del model_group, request_context
            return primary

        def require_deployment(self, model_group: str, deployment: object):
            del model_group
            return deployment

    class _Failover:
        async def execute_with_failover(self, *, primary_deployment, model_group, execute, return_deployment=False, **kwargs):
            del model_group, kwargs
            try:
                data = await execute(primary_deployment)
                served_deployment = primary_deployment
            except ServiceUnavailableError:
                data = await execute(fallback)
                served_deployment = fallback
            if return_deployment:
                return data, served_deployment
            return data

    class _ChatMicrobatchExecutor:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def execute_chat_microbatch(self, *, requests, deployment, request_context):  # noqa: ANN001
            del request_context
            self.calls.append(deployment.deployment_id)
            if deployment.deployment_id == "dep-chat":
                raise ServiceUnavailableError(message="primary unavailable", affects_deployment_health=True)
            return [
                {
                    "index": index,
                    "response_body": {
                        "id": f"chatcmpl-{index}",
                        "object": "chat.completion",
                        "created": 1,
                        "model": "gpt-oss",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": f"fallback-{index}"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
                    },
                    "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
                }
                for index, _ in enumerate(requests)
            ]

    repo = _FakeRepository()
    executor = _ChatMicrobatchExecutor()
    app = SimpleNamespace(
        state=SimpleNamespace(
            router=_Router(),
            failover_manager=_Failover(),
            spend_tracking_service=None,
            passive_health_tracker=None,
            router_state_backend=None,
            chat_microbatch_executor=executor,
        )
    )
    worker = BatchExecutorWorker(
        app=app,
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )
    worker.config.worker_concurrency = 1

    await worker._process_items(
        _build_chat_batch_job(),
        [
            _build_chat_batch_item("chat-1", "hello"),
            _build_chat_batch_item("chat-2", "world"),
        ],
    )

    assert executor.calls == ["dep-chat", "dep-chat-fallback"]
    assert len(repo.completed_calls) == 2
    assert len(repo.completion_outbox_calls) == 2
    assert repo.completion_outbox_calls[0]["api_provider"] == "groq"
    assert repo.completion_outbox_calls[0]["api_base"] == "https://api.groq.com/openai/v1"
    assert repo.completion_outbox_calls[0]["provider_cost"] == pytest.approx(0.020)
    assert repo.completion_outbox_calls[0]["billed_cost"] == pytest.approx(0.010)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fallback_chat_batching", "contents"),
    [
        ({"mode": "concurrent"}, ["hello", "world"]),
        ({"mode": "sync_microbatch", "upstream_max_batch_size": 2}, ["hello", "world", "again"]),
    ],
)
async def test_batch_worker_sync_microbatch_primary_failure_with_unsupported_fallback_requeues_chunk(
    monkeypatch,
    fallback_chat_batching: dict,
    contents: list[str],
):
    execute_calls: list[str] = []
    _patch_fake_chat_execute(monkeypatch, execute_calls)

    primary = SimpleNamespace(
        deployment_id="dep-chat",
        deltallm_params={
            "provider": "vllm",
            "model": "openai/gpt-oss-120b",
            "api_base": "http://vllm.primary/v1",
            "chat_batching": {"mode": "sync_microbatch", "upstream_max_batch_size": 4},
        },
        input_cost_per_token=0.001,
        output_cost_per_token=0.002,
        model_info={"batch_input_cost_per_token": 0.0005, "batch_output_cost_per_token": 0.001},
    )
    fallback = SimpleNamespace(
        deployment_id="dep-chat-fallback",
        deltallm_params={
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_base": "https://api.openai.com/v1",
            "chat_batching": fallback_chat_batching,
        },
        input_cost_per_token=0.003,
        output_cost_per_token=0.004,
        model_info={"batch_input_cost_per_token": 0.0015, "batch_output_cost_per_token": 0.002},
    )

    class _Router:
        def resolve_model_group(self, model: str) -> str:
            return model

        async def select_deployment(self, model_group: str, request_context: dict) -> object:
            del model_group, request_context
            return primary

        def require_deployment(self, model_group: str, deployment: object):
            del model_group
            return deployment

    class _Failover:
        def __init__(self) -> None:
            self.failures: list[tuple[str, bool | None, str | None]] = []

        async def execute_with_failover(self, *, primary_deployment, model_group, execute, return_deployment=False, **kwargs):
            del model_group, kwargs
            try:
                data = await execute(primary_deployment)
                served_deployment = primary_deployment
            except ServiceUnavailableError as exc:
                self.failures.append(
                    (primary_deployment.deployment_id, exc.affects_deployment_health, exc.code)
                )
                try:
                    data = await execute(fallback)
                    served_deployment = fallback
                except ServiceUnavailableError as fallback_exc:
                    self.failures.append(
                        (fallback.deployment_id, fallback_exc.affects_deployment_health, fallback_exc.code)
                    )
                    raise
            if return_deployment:
                return data, served_deployment
            return data

    class _GlobalMicrobatchExecutor:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def execute_chat_microbatch(self, *, requests, deployment, request_context):  # noqa: ANN001
            del requests, request_context
            self.calls.append(deployment.deployment_id)
            if deployment.deployment_id != "dep-chat":
                raise AssertionError(f"unexpected fallback microbatch call for {deployment.deployment_id}")
            raise ServiceUnavailableError(message="primary unavailable", affects_deployment_health=True)

    repo = _FailureRepository()
    failover = _Failover()
    executor = _GlobalMicrobatchExecutor()
    passive_health = _PassiveHealthRecorder()
    app = SimpleNamespace(
        state=SimpleNamespace(
            router=_Router(),
            failover_manager=failover,
            spend_tracking_service=None,
            passive_health_tracker=passive_health,
            router_state_backend=None,
            settings=SimpleNamespace(openai_base_url="https://api.openai.com/v1"),
        )
    )
    worker = BatchExecutorWorker(
        app=app,
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )
    worker.config.worker_concurrency = 2
    worker.config.retry_jitter = False
    worker.app.state.chat_microbatch_executor = executor

    await worker._process_items(
        _build_chat_batch_job(),
        [_build_chat_batch_item(f"chat-{index}", content) for index, content in enumerate(contents, start=1)],
    )

    assert executor.calls == ["dep-chat"]
    assert failover.failures == [
        ("dep-chat", True, None),
        ("dep-chat-fallback", False, "chat_microbatch_unsupported"),
    ]
    assert execute_calls == []
    assert repo.completed_calls == []
    assert repo.completion_outbox_calls == []
    assert repo.failed_calls == []
    assert len(repo.release_for_retry_calls) == 1
    release_call = repo.release_for_retry_calls[0]
    assert release_call["item_ids"] == [f"chat-{index}" for index in range(1, len(contents) + 1)]
    assert release_call["retry_delay_seconds"] == 5
    assert release_call["last_error"] == "primary unavailable"
    assert release_call["error_body"]["retryable"] is True
    assert release_call["error_body"]["retry_category"] == "service_unavailable"
    assert release_call["error_body"]["microbatch"] == {
        "retry_count": 1,
        "original_size": len(contents),
        "failed_size": len(contents),
    }
    assert repo.refresh_job_progress_calls == ["b-chat"]
    assert passive_health.calls == [("dep-chat", False, "primary unavailable")]


@pytest.mark.asyncio
async def test_batch_worker_sync_microbatch_unsupported_fallback_respects_max_in_flight(monkeypatch):
    active = 0
    max_active = 0
    fallback_reasons: list[tuple[str, int]] = []

    async def _fake_execute_chat(request, payload, deployment, *, record_usage: bool = True):
        del request, deployment, record_usage
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return (
            {
                "id": f"chatcmpl-{payload.messages[0].content}",
                "object": "chat.completion",
                "created": 1,
                "model": payload.model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
            },
            5.0,
        )

    monkeypatch.setattr("src.batch.worker.execute_chat", _fake_execute_chat)
    monkeypatch.setattr(
        "src.batch.chat_worker_execution.increment_batch_chat_microbatch_fallback",
        lambda *, reason, count=1: fallback_reasons.append((reason, count)),
    )

    primary = SimpleNamespace(
        deployment_id="dep-chat",
        deltallm_params={
            "provider": "vllm",
            "model": "openai/gpt-oss-120b",
            "api_base": "http://vllm.primary/v1",
            "chat_batching": {"mode": "sync_microbatch", "upstream_max_batch_size": 4, "max_in_flight": 1},
        },
        input_cost_per_token=0.001,
        output_cost_per_token=0.002,
        model_info={"batch_input_cost_per_token": 0.0005, "batch_output_cost_per_token": 0.001},
    )
    fallback = SimpleNamespace(
        deployment_id="dep-chat-fallback",
        deltallm_params={
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_base": "https://api.openai.com/v1",
            "chat_batching": {"mode": "concurrent"},
        },
        input_cost_per_token=0.003,
        output_cost_per_token=0.004,
        model_info={"batch_input_cost_per_token": 0.0015, "batch_output_cost_per_token": 0.002},
    )

    class _Router:
        def resolve_model_group(self, model: str) -> str:
            return model

        async def select_deployment(self, model_group: str, request_context: dict) -> object:
            del model_group, request_context
            return primary

        def require_deployment(self, model_group: str, deployment: object):
            del model_group
            return deployment

    class _Failover:
        async def execute_with_failover(self, *, primary_deployment, model_group, execute, return_deployment=False, **kwargs):
            del model_group, kwargs
            try:
                data = await execute(primary_deployment)
                served_deployment = primary_deployment
            except ServiceUnavailableError:
                data = await execute(fallback)
                served_deployment = fallback
            if return_deployment:
                return data, served_deployment
            return data

    class _GlobalMicrobatchExecutor:
        async def execute_chat_microbatch(self, *, requests, deployment, request_context):  # noqa: ANN001
            del requests, request_context
            if deployment.deployment_id != "dep-chat":
                raise AssertionError(f"unexpected fallback microbatch call for {deployment.deployment_id}")
            raise ServiceUnavailableError(
                message="sync microbatch unsupported",
                code="chat_microbatch_unsupported",
                affects_deployment_health=False,
            )

    repo = _FakeRepository()
    app = SimpleNamespace(
        state=SimpleNamespace(
            router=_Router(),
            failover_manager=_Failover(),
            spend_tracking_service=None,
            passive_health_tracker=None,
            router_state_backend=None,
            settings=SimpleNamespace(openai_base_url="https://api.openai.com/v1"),
            chat_microbatch_executor=_GlobalMicrobatchExecutor(),
        )
    )
    worker = BatchExecutorWorker(
        app=app,
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )
    worker.config.worker_concurrency = 3

    await worker._process_items(
        _build_chat_batch_job(),
        [
            _build_chat_batch_item("chat-1", "hello"),
            _build_chat_batch_item("chat-2", "world"),
            _build_chat_batch_item("chat-3", "again"),
        ],
    )

    assert len(repo.completed_calls) == 3
    assert repo.release_for_retry_calls == []
    assert fallback_reasons == [("mode=concurrent", 3)]
    assert max_active == 1


@pytest.mark.asyncio
async def test_batch_worker_sync_microbatch_response_shape_failure_uses_served_deployment_for_health():
    primary = SimpleNamespace(
        deployment_id="dep-chat",
        deltallm_params={
            "provider": "vllm",
            "model": "openai/gpt-oss-120b",
            "api_base": "http://vllm.primary/v1",
            "chat_batching": {"mode": "sync_microbatch", "upstream_max_batch_size": 4},
        },
        input_cost_per_token=0.001,
        output_cost_per_token=0.002,
        model_info={"batch_input_cost_per_token": 0.0005, "batch_output_cost_per_token": 0.001},
    )
    fallback = SimpleNamespace(
        deployment_id="dep-chat-fallback",
        deltallm_params={
            "provider": "groq",
            "model": "openai/gpt-oss-120b",
            "api_base": "https://api.groq.com/openai/v1",
            "chat_batching": {"mode": "sync_microbatch", "upstream_max_batch_size": 4},
        },
        input_cost_per_token=0.003,
        output_cost_per_token=0.004,
        model_info={"batch_input_cost_per_token": 0.0015, "batch_output_cost_per_token": 0.002},
    )

    class _Router:
        def resolve_model_group(self, model: str) -> str:
            return model

        async def select_deployment(self, model_group: str, request_context: dict) -> object:
            del model_group, request_context
            return primary

        def require_deployment(self, model_group: str, deployment: object):
            del model_group
            return deployment

    class _Failover:
        async def execute_with_failover(self, *, primary_deployment, model_group, execute, return_deployment=False, **kwargs):
            del model_group, kwargs
            try:
                data = await execute(primary_deployment)
                served_deployment = primary_deployment
            except ServiceUnavailableError:
                data = await execute(fallback)
                served_deployment = fallback
            if return_deployment:
                return data, served_deployment
            return data

    class _ChatMicrobatchExecutor:
        async def execute_chat_microbatch(self, *, requests, deployment, request_context):  # noqa: ANN001
            del requests, request_context
            if deployment.deployment_id == "dep-chat":
                raise ServiceUnavailableError(message="primary unavailable", affects_deployment_health=True)
            return [
                {
                    "index": 0,
                    "response_body": {
                        "id": "chatcmpl-0",
                        "object": "chat.completion",
                        "created": 1,
                        "model": "gpt-oss",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "ok"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
                    },
                    "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
                }
            ]

    repo = _FailureRepository()
    passive_health = _PassiveHealthRecorder()
    app = SimpleNamespace(
        state=SimpleNamespace(
            router=_Router(),
            failover_manager=_Failover(),
            spend_tracking_service=None,
            passive_health_tracker=passive_health,
            router_state_backend=None,
            chat_microbatch_executor=_ChatMicrobatchExecutor(),
        )
    )
    worker = BatchExecutorWorker(
        app=app,
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )
    worker.config.worker_concurrency = 1

    await worker._process_items(
        _build_chat_batch_job(),
        [
            _build_chat_batch_item("chat-1", "hello"),
            _build_chat_batch_item("chat-2", "world"),
        ],
    )

    assert len(repo.failed_calls) == 2
    assert passive_health.calls == [
        ("dep-chat-fallback", False, "chat microbatch response length mismatch expected=2 actual=1"),
    ]


@pytest.mark.asyncio
async def test_batch_worker_sync_microbatch_retryable_failure_requeues_chunk_and_records_health_once():
    class _ChatMicrobatchExecutor:
        async def execute_chat_microbatch(self, *, requests, deployment, request_context):  # noqa: ANN001
            del requests, deployment, request_context
            raise ServiceUnavailableError(message="provider down", affects_deployment_health=True)

    repo = _FailureRepository()
    worker, _ = _build_chat_batch_worker(
        deployment_params={
            "provider": "vllm",
            "model": "openai/gpt-oss-120b",
            "api_base": "http://vllm.local/v1",
            "chat_batching": {"mode": "sync_microbatch", "upstream_max_batch_size": 4},
        },
        repository=repo,
    )
    worker.config.worker_concurrency = 1
    worker.config.retry_jitter = False
    passive_health = _PassiveHealthRecorder()
    worker.app.state.passive_health_tracker = passive_health
    worker.app.state.chat_microbatch_executor = _ChatMicrobatchExecutor()

    await worker._process_items(
        _build_chat_batch_job(),
        [
            _build_chat_batch_item("chat-1", "hello"),
            _build_chat_batch_item("chat-2", "world"),
        ],
    )

    assert repo.failed_calls == []
    assert len(repo.release_for_retry_calls) == 1
    release_call = repo.release_for_retry_calls[0]
    assert release_call["item_ids"] == ["chat-1", "chat-2"]
    assert release_call["retry_delay_seconds"] == 5
    assert release_call["last_error"] == "provider down"
    assert release_call["error_body"]["retryable"] is True
    assert release_call["error_body"]["retry_category"] == "service_unavailable"
    assert release_call["error_body"]["microbatch"] == {
        "retry_count": 1,
        "original_size": 2,
        "failed_size": 2,
    }
    assert repo.refresh_job_progress_calls == ["b-chat"]
    assert passive_health.calls == [("dep-chat", False, "provider down")]


@pytest.mark.asyncio
async def test_batch_worker_missing_chat_batching_config_keeps_concurrent_item_execution(monkeypatch):
    execute_calls: list[str] = []

    class _UnusedMicrobatchExecutor:
        async def execute_chat_microbatch(self, **kwargs):  # noqa: ANN003, ANN201
            raise AssertionError(f"unexpected chat microbatch call: {kwargs}")

    _patch_fake_chat_execute(monkeypatch, execute_calls)
    worker, repo = _build_chat_batch_worker(
        deployment_params={
            "provider": "vllm",
            "model": "openai/gpt-oss-120b",
            "api_base": "http://vllm.local/v1",
        }
    )
    worker.config.worker_concurrency = 1
    worker.app.state.chat_microbatch_executor = _UnusedMicrobatchExecutor()

    await worker._process_items(
        _build_chat_batch_job(),
        [
            _build_chat_batch_item("chat-1", "hello"),
            _build_chat_batch_item("chat-2", "world"),
        ],
    )

    assert execute_calls == ["hello", "world"]
    assert len(repo.completed_calls) == 2
    assert [payload["batch_execution_mode"] for payload in repo.completion_outbox_calls] == ["concurrent", "concurrent"]


@pytest.mark.asyncio
async def test_batch_worker_explicit_concurrent_mode_does_not_call_microbatch_executor(monkeypatch):
    execute_calls: list[str] = []

    class _UnusedMicrobatchExecutor:
        async def execute_chat_microbatch(self, **kwargs):  # noqa: ANN003, ANN201
            raise AssertionError(f"unexpected chat microbatch call: {kwargs}")

    _patch_fake_chat_execute(monkeypatch, execute_calls)
    worker, repo = _build_chat_batch_worker(
        deployment_params={
            "provider": "vllm",
            "model": "openai/gpt-oss-120b",
            "api_base": "http://vllm.local/v1",
            "chat_batching": {"mode": "concurrent", "max_in_flight": 1},
        }
    )
    worker.config.worker_concurrency = 1
    worker.app.state.chat_microbatch_executor = _UnusedMicrobatchExecutor()

    await worker._process_items(
        _build_chat_batch_job(),
        [
            _build_chat_batch_item("chat-1", "hello"),
            _build_chat_batch_item("chat-2", "world"),
        ],
    )

    assert execute_calls == ["hello", "world"]
    assert len(repo.completed_calls) == 2
    assert [payload["batch_execution_mode"] for payload in repo.completion_outbox_calls] == ["concurrent", "concurrent"]


@pytest.mark.asyncio
async def test_batch_worker_sync_microbatch_falls_back_when_request_params_differ(monkeypatch):
    execute_calls: list[str] = []

    class _UnusedMicrobatchExecutor:
        async def execute_chat_microbatch(self, **kwargs):  # noqa: ANN003, ANN201
            raise AssertionError(f"unexpected chat microbatch call: {kwargs}")

    _patch_fake_chat_execute(monkeypatch, execute_calls)
    worker, repo = _build_chat_batch_worker(
        deployment_params={
            "provider": "vllm",
            "model": "openai/gpt-oss-120b",
            "api_base": "http://vllm.local/v1",
            "chat_batching": {"mode": "sync_microbatch", "upstream_max_batch_size": 4},
        }
    )
    worker.config.worker_concurrency = 1
    worker.app.state.chat_microbatch_executor = _UnusedMicrobatchExecutor()

    await worker._process_items(
        _build_chat_batch_job(),
        [
            _build_chat_batch_item("chat-1", "hello", request_overrides={"temperature": 0.1}),
            _build_chat_batch_item("chat-2", "world", request_overrides={"temperature": 0.2}),
        ],
    )

    assert execute_calls == ["hello", "world"]
    assert len(repo.completed_calls) == 2
    assert [payload["batch_execution_mode"] for payload in repo.completion_outbox_calls] == [
        "sync_microbatch_fallback",
        "sync_microbatch_fallback",
    ]


@pytest.mark.asyncio
async def test_batch_worker_sync_microbatch_falls_back_when_token_cap_exceeded(monkeypatch):
    execute_calls: list[str] = []

    class _UnusedMicrobatchExecutor:
        async def execute_chat_microbatch(self, **kwargs):  # noqa: ANN003, ANN201
            raise AssertionError(f"unexpected chat microbatch call: {kwargs}")

    _patch_fake_chat_execute(monkeypatch, execute_calls)
    worker, repo = _build_chat_batch_worker(
        deployment_params={
            "provider": "vllm",
            "model": "openai/gpt-oss-120b",
            "api_base": "http://vllm.local/v1",
            "chat_batching": {
                "mode": "sync_microbatch",
                "upstream_max_batch_size": 4,
                "max_total_input_tokens": 1,
            },
        }
    )
    worker.config.worker_concurrency = 1
    worker.app.state.chat_microbatch_executor = _UnusedMicrobatchExecutor()

    await worker._process_items(
        _build_chat_batch_job(),
        [
            _build_chat_batch_item("chat-1", "hello"),
            _build_chat_batch_item("chat-2", "world"),
        ],
    )

    assert execute_calls == ["hello", "world"]
    assert len(repo.completed_calls) == 2
    assert [payload["batch_execution_mode"] for payload in repo.completion_outbox_calls] == [
        "sync_microbatch_fallback",
        "sync_microbatch_fallback",
    ]


@pytest.mark.asyncio
async def test_batch_worker_sync_microbatch_requires_per_item_usage():
    class _FailureRepo(_FakeRepository):
        def __init__(self) -> None:
            super().__init__()
            self.failed_calls: list[dict] = []

        async def mark_item_failed(self, **kwargs) -> bool:
            self.failed_calls.append(kwargs)
            return True

        async def refresh_job_progress(self, batch_id: str):
            assert batch_id == "b-chat"
            return None

    class _ChatMicrobatchExecutor:
        async def execute_chat_microbatch(self, *, requests, deployment, request_context):  # noqa: ANN001
            del requests, deployment, request_context
            return [
                {
                    "index": 0,
                    "response_body": {
                        "id": "chatcmpl-0",
                        "object": "chat.completion",
                        "created": 1,
                        "model": "gpt-oss",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "ok"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
                    },
                    "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
                },
                {
                    "index": 1,
                    "response_body": {
                        "id": "chatcmpl-1",
                        "object": "chat.completion",
                        "created": 1,
                        "model": "gpt-oss",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "missing-usage"},
                                "finish_reason": "stop",
                            }
                        ],
                    },
                },
            ]

    repo = _FailureRepo()
    worker, _ = _build_chat_batch_worker(
        deployment_params={
            "provider": "vllm",
            "model": "openai/gpt-oss-120b",
            "api_base": "http://vllm.local/v1",
            "chat_batching": {"mode": "sync_microbatch", "upstream_max_batch_size": 4},
        },
        repository=repo,
    )
    worker.config.worker_concurrency = 1
    worker.app.state.chat_microbatch_executor = _ChatMicrobatchExecutor()

    await worker._process_items(
        _build_chat_batch_job(),
        [
            _build_chat_batch_item("chat-1", "hello"),
            _build_chat_batch_item("chat-2", "world"),
        ],
    )

    assert len(repo.completed_calls) == 1
    assert repo.completed_calls[0]["item_id"] == "chat-1"
    assert len(repo.failed_calls) == 1
    assert repo.failed_calls[0]["item_id"] == "chat-2"
    assert repo.failed_calls[0]["error_body"]["type"] == "BatchResponseShapeError"
    assert repo.failed_calls[0]["retryable"] is False


@pytest.mark.asyncio
async def test_batch_worker_sync_microbatch_persists_mixed_success_and_failure_results():
    class _FailureRepo(_FakeRepository):
        def __init__(self) -> None:
            super().__init__()
            self.failed_calls: list[dict] = []

        async def mark_item_failed(self, **kwargs) -> bool:
            self.failed_calls.append(kwargs)
            return True

        async def refresh_job_progress(self, batch_id: str):
            assert batch_id == "b-chat"
            return None

    class _ChatMicrobatchExecutor:
        async def execute_chat_microbatch(self, *, requests, deployment, request_context):  # noqa: ANN001
            del requests, deployment, request_context
            return [
                {
                    "index": 0,
                    "response_body": {
                        "id": "chatcmpl-0",
                        "object": "chat.completion",
                        "created": 1,
                        "model": "gpt-oss",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "ok"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
                    },
                    "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
                },
                {
                    "index": 1,
                    "error": {"message": "provider rejected this item"},
                },
            ]

    repo = _FailureRepo()
    worker, _ = _build_chat_batch_worker(
        deployment_params={
            "provider": "vllm",
            "model": "openai/gpt-oss-120b",
            "api_base": "http://vllm.local/v1",
            "chat_batching": {"mode": "sync_microbatch", "upstream_max_batch_size": 4},
        },
        repository=repo,
    )
    worker.config.worker_concurrency = 1
    worker.app.state.chat_microbatch_executor = _ChatMicrobatchExecutor()

    await worker._process_items(
        _build_chat_batch_job(),
        [
            _build_chat_batch_item("chat-1", "hello"),
            _build_chat_batch_item("chat-2", "world"),
        ],
    )

    assert len(repo.completed_calls) == 1
    assert repo.completed_calls[0]["item_id"] == "chat-1"
    assert len(repo.failed_calls) == 1
    assert repo.failed_calls[0]["item_id"] == "chat-2"
    assert repo.failed_calls[0]["error_body"]["type"] == "InvalidRequestError"
    assert repo.failed_calls[0]["retryable"] is False


@pytest.mark.asyncio
async def test_batch_worker_uses_post_construction_hook_patches(monkeypatch):
    execute_inputs: list[object] = []
    router_usage_calls: list[tuple[str, str, dict[str, int]]] = []
    metric_statuses: list[str] = []

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
    app = SimpleNamespace(
        state=SimpleNamespace(
            router=_Router(),
            failover_manager=_Failover(),
            spend_tracking_service=None,
            passive_health_tracker=None,
            router_state_backend=_RouterStateBackend(),
        )
    )
    worker = BatchExecutorWorker(
        app=app,
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )

    async def _patched_execute_embedding(request, payload, deployment):
        del request, deployment
        execute_inputs.append(payload.input)
        return {
            "object": "list",
            "data": [{"index": 0, "embedding": [0.1]}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
        }

    async def _patched_record_router_usage(state_backend, deployment_id: str, *, mode: str, usage: dict):
        del state_backend
        router_usage_calls.append((deployment_id, mode, dict(usage)))

    def _patched_observe_batch_item_execution_latency(*, status: str, latency_seconds: float) -> None:
        assert latency_seconds >= 0
        metric_statuses.append(status)

    monkeypatch.setattr("src.batch.worker._execute_embedding", _patched_execute_embedding)
    monkeypatch.setattr("src.batch.worker.record_router_usage", _patched_record_router_usage)
    monkeypatch.setattr(
        "src.batch.worker.observe_batch_item_execution_latency",
        _patched_observe_batch_item_execution_latency,
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

    assert execute_inputs == ["hello"]
    assert router_usage_calls == [
        ("dep-1", "embedding", {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5})
    ]
    assert metric_statuses == ["success"]
    assert len(repo.completed_calls) == 1


@pytest.mark.asyncio
async def test_batch_worker_process_item_uses_worker_prepare_override(monkeypatch):
    async def _fake_execute_embedding(request, payload, deployment):
        del request, deployment
        return {
            "object": "list",
            "data": [{"index": 0, "embedding": [0.1]}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
        }

    monkeypatch.setattr("src.batch.worker._execute_embedding", _fake_execute_embedding)

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
    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace(router=_Router(), failover_manager=_Failover(), spend_tracking_service=None)),
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )

    prepare_calls: list[str] = []
    original_prepare_item = worker._prepare_item_for_execution

    async def _prepare_item_override(job, item):  # noqa: ANN001
        prepare_calls.append(item.item_id)
        return await original_prepare_item(job, item)

    worker._prepare_item_for_execution = _prepare_item_override  # type: ignore[assignment]

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

    assert prepare_calls == ["i1"]
    assert len(repo.completed_calls) == 1


@pytest.mark.asyncio
async def test_batch_worker_normalizes_single_item_embedding_usage(monkeypatch):
    async def _fake_execute_embedding(request, payload, deployment):
        del request, payload, deployment
        return {"object": "list", "data": [{"index": 0, "embedding": [0.1]}], "usage": {"prompt_tokens": 5, "total_tokens": 5}}

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
        async def execute_with_failover(self, *, primary_deployment, model_group, execute, return_deployment=False, **kwargs):
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
        metadata=None,
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
        lease_expires_at=now,
        cancel_requested_at=None,
        status_last_updated_at=now,
        created_by_api_key="key-1",
        created_by_user_id="user-1",
        created_by_team_id="team-1",
        created_by_organization_id="org-1",
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

    assert repo.completed_calls[0]["response_body"] == {
        "object": "list",
        "data": [{"object": "embedding", "index": 0, "embedding": [0.1]}],
        "model": "vllm/sentence-transformers/all-MiniLM-L6-v2",
        "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
        "_provider": "vllm",
    }
    assert repo.completed_calls[0]["usage"] == {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5}

    artifact_now = datetime.now(tz=UTC)

    class _ArtifactRepo:
        async def list_items(self, batch_id: str):
            assert batch_id == "b1"
            return [
                BatchItemRecord(
                    item_id="i1",
                    batch_id=batch_id,
                    line_number=1,
                    custom_id="c1",
                    status="completed",
                    request_body=item.request_body,
                    response_body=repo.completed_calls[0]["response_body"],
                    error_body=None,
                    usage=repo.completed_calls[0]["usage"],
                    provider_cost=0.0,
                    billed_cost=0.0,
                    attempts=1,
                    last_error=None,
                    locked_by=None,
                    lease_expires_at=None,
                    created_at=artifact_now,
                    started_at=artifact_now,
                    completed_at=artifact_now,
                )
            ]

    artifact_worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=_ArtifactRepo(),  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )

    rows = [json.loads(line) async for line in artifact_worker._iter_output_lines("b1")]

    assert rows == [
        {
            "id": "batch_req_i1",
            "custom_id": "c1",
            "response": {
                "status_code": 200,
                "request_id": "req_batch_i1",
                "body": {
                    "object": "list",
                    "data": [{"object": "embedding", "index": 0, "embedding": [0.1]}],
                    "model": "vllm/sentence-transformers/all-MiniLM-L6-v2",
                    "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
                },
            },
            "error": None,
        }
    ]


@pytest.mark.asyncio
async def test_batch_worker_iter_output_lines_emit_openai_batch_success_rows():
    now = datetime.now(tz=UTC)

    class _ArtifactRepo:
        async def list_items(self, batch_id: str):
            assert batch_id == "b1"
            return [
                BatchItemRecord(
                    item_id="i1",
                    batch_id=batch_id,
                    line_number=1,
                    custom_id="req-1",
                    status="completed",
                    request_body={},
                    response_body=_valid_embedding_artifact_response_body(),
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
                )
            ]

    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=_ArtifactRepo(),  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )

    rows = [json.loads(line) async for line in worker._iter_output_lines("b1")]

    assert rows == [
        {
            "id": "batch_req_i1",
            "custom_id": "req-1",
            "response": {
                "status_code": 200,
                "request_id": "req_batch_i1",
                "body": {
                    "object": "list",
                    "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                    "model": "provider-embedding-model",
                    "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
                },
            },
            "error": None,
        }
    ]


@pytest.mark.asyncio
async def test_batch_worker_iter_output_lines_emit_chat_batch_success_rows():
    now = datetime.now(tz=UTC)

    class _ArtifactRepo:
        async def list_items(self, batch_id: str):
            assert batch_id == "b1"
            return [
                BatchItemRecord(
                    item_id="i1",
                    batch_id=batch_id,
                    line_number=1,
                    custom_id="chat-1",
                    status="completed",
                    request_body={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
                    response_body={
                        "id": "chatcmpl-1",
                        "object": "chat.completion",
                        "created": 1,
                        "model": "gpt-4o-mini",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "hello"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
                        "_provider": "openai",
                    },
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
                )
            ]

    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=_ArtifactRepo(),  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )

    rows = [json.loads(line) async for line in worker._iter_output_lines("b1", endpoint="/v1/chat/completions")]

    assert rows == [
        {
            "id": "batch_req_i1",
            "custom_id": "chat-1",
            "response": {
                "status_code": 200,
                "request_id": "req_batch_i1",
                "body": {
                    "id": "chatcmpl-1",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "hello"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
                },
            },
            "error": None,
        }
    ]


@pytest.mark.asyncio
async def test_batch_worker_iter_output_lines_reject_missing_response_body_for_completed_items():
    now = datetime.now(tz=UTC)

    class _ArtifactRepo:
        async def list_items(self, batch_id: str):
            assert batch_id == "b1"
            return [
                BatchItemRecord(
                    item_id="i1",
                    batch_id=batch_id,
                    line_number=1,
                    custom_id="req-1",
                    status="completed",
                    request_body={},
                    response_body=None,
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
                )
            ]

    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=_ArtifactRepo(),  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )

    with pytest.raises(BatchArtifactValidationError, match="missing an embedding response body"):
        _ = [json.loads(line) async for line in worker._iter_output_lines("b1")]


@pytest.mark.asyncio
async def test_batch_worker_iter_output_lines_reject_malformed_embedding_payload_for_completed_items():
    now = datetime.now(tz=UTC)

    class _ArtifactRepo:
        async def list_items(self, batch_id: str):
            assert batch_id == "b1"
            return [
                BatchItemRecord(
                    item_id="i1",
                    batch_id=batch_id,
                    line_number=1,
                    custom_id="req-1",
                    status="completed",
                    request_body={},
                    response_body={
                        "object": "list",
                        "data": [{"index": 0}],
                        "model": "provider-embedding-model",
                        "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
                    },
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
                )
            ]

    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=_ArtifactRepo(),  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )

    with pytest.raises(BatchArtifactValidationError, match="missing embedding"):
        _ = [json.loads(line) async for line in worker._iter_output_lines("b1")]


@pytest.mark.asyncio
async def test_batch_worker_iter_output_lines_backfill_legacy_model_and_usage_from_item_fields():
    now = datetime.now(tz=UTC)

    class _ArtifactRepo:
        async def list_items(self, batch_id: str):
            assert batch_id == "b1"
            return [
                BatchItemRecord(
                    item_id="i1",
                    batch_id=batch_id,
                    line_number=1,
                    custom_id="req-1",
                    status="completed",
                    request_body={"model": "request-model", "input": "hello"},
                    response_body={
                        "object": "list",
                        "data": [{"index": 0, "embedding": [0.1, 0.2]}],
                        "usage": {"prompt_tokens": 5, "total_tokens": 5},
                        "_provider": "openai",
                    },
                    error_body=None,
                    usage={"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
                    provider_cost=0.0,
                    billed_cost=0.0,
                    attempts=1,
                    last_error=None,
                    locked_by=None,
                    lease_expires_at=None,
                    created_at=now,
                    started_at=now,
                    completed_at=now,
                )
            ]

    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=_ArtifactRepo(),  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )

    rows = [json.loads(line) async for line in worker._iter_output_lines("b1")]

    assert rows == [
        {
            "id": "batch_req_i1",
            "custom_id": "req-1",
            "response": {
                "status_code": 200,
                "request_id": "req_batch_i1",
                "body": {
                    "object": "list",
                    "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                    "model": "request-model",
                    "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
                },
            },
            "error": None,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        pytest.param(lambda body: body.pop("model"), "missing a valid model", id="missing-model"),
        pytest.param(lambda body: body.__setitem__("model", " "), "missing a valid model", id="blank-model"),
        pytest.param(lambda body: body.pop("usage"), "usage is not an object", id="missing-usage"),
        pytest.param(lambda body: body.__setitem__("usage", "invalid"), "usage is not an object", id="non-object-usage"),
        pytest.param(
            lambda body: body.__setitem__("usage", {"completion_tokens": 0, "total_tokens": 5}),
            "invalid prompt_tokens",
            id="missing-prompt-tokens",
        ),
        pytest.param(
            lambda body: body.__setitem__("usage", {"prompt_tokens": 5, "total_tokens": 5}),
            "invalid completion_tokens",
            id="missing-completion-tokens",
        ),
        pytest.param(
            lambda body: body.__setitem__("usage", {"prompt_tokens": 5, "completion_tokens": 0}),
            "invalid total_tokens",
            id="missing-total-tokens",
        ),
        pytest.param(
            lambda body: body.__setitem__("usage", {"prompt_tokens": "five", "completion_tokens": 0, "total_tokens": 5}),
            "invalid prompt_tokens",
            id="non-int-prompt-tokens",
        ),
        pytest.param(
            lambda body: body.__setitem__("usage", {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": -1}),
            "negative total_tokens",
            id="negative-total-tokens",
        ),
    ],
)
async def test_batch_worker_iter_output_lines_reject_invalid_success_body_fields(mutate, match):
    now = datetime.now(tz=UTC)
    response_body = _valid_embedding_artifact_response_body()
    mutate(response_body)

    class _ArtifactRepo:
        async def list_items(self, batch_id: str):
            assert batch_id == "b1"
            return [
                BatchItemRecord(
                    item_id="i1",
                    batch_id=batch_id,
                    line_number=1,
                    custom_id="req-1",
                    status="completed",
                    request_body={},
                    response_body=response_body,
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
                )
            ]

    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=_ArtifactRepo(),  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )

    with pytest.raises(BatchArtifactValidationError, match=match):
        _ = [json.loads(line) async for line in worker._iter_output_lines("b1")]


@pytest.mark.asyncio
async def test_batch_worker_iter_error_lines_emit_openai_batch_error_rows():
    now = datetime.now(tz=UTC)

    class _ArtifactRepo:
        async def list_items(self, batch_id: str):
            assert batch_id == "b1"
            return [
                BatchItemRecord(
                    item_id="i1",
                    batch_id=batch_id,
                    line_number=1,
                    custom_id="req-1",
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
                BatchItemRecord(
                    item_id="i2",
                    batch_id=batch_id,
                    line_number=2,
                    custom_id="req-2",
                    status="cancelled",
                    request_body={},
                    response_body=None,
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
            ]

    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=_ArtifactRepo(),  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )

    rows = [json.loads(line) async for line in worker._iter_error_lines("b1")]

    assert rows == [
        {
            "id": "batch_req_i1",
            "custom_id": "req-1",
            "response": None,
            "error": {"message": "boom", "type": "BatchItemError"},
        },
        {
            "id": "batch_req_i2",
            "custom_id": "req-2",
            "response": None,
            "error": {"message": "Batch request cancelled", "type": "BatchItemCancelled"},
        },
    ]


@pytest.mark.asyncio
async def test_batch_worker_keeps_completed_state_when_side_effects_fail(monkeypatch):
    async def _fake_execute_embedding(request, payload, deployment):
        del request, payload, deployment
        return {"object": "list", "data": [{"index": 0, "embedding": [0.1]}], "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5}}

    monkeypatch.setattr("src.batch.worker._execute_embedding", _fake_execute_embedding)

    def _boom(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("metrics backend unavailable")

    monkeypatch.setattr("src.batch.worker.observe_batch_item_execution_latency", _boom)

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
        async def record_request_outcome(
            self,
            deployment_id: str,
            success: bool,
            error: str | None = None,
            *,
            exc: Exception | None = None,
        ) -> None:
            del deployment_id, success, error, exc
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
                batch_id="b-work",
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
            assert batch_id == "b-work"
            self.cancel_called = True

        async def claim_items(self, **kwargs):
            del kwargs
            return []

        async def refresh_job_progress(self, batch_id: str):
            assert batch_id == "b-work"
            self.refreshed = True
            return BatchJobRecord(
                batch_id="b-work",
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
            assert batch_id == "b-work"
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
    assert finalized == ["b-work"]


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
                    response_body=_valid_embedding_artifact_response_body(),
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
async def test_batch_worker_marks_invalid_completed_artifacts_failed_without_retry(
    caplog: pytest.LogCaptureFixture,
):
    now = datetime.now(tz=UTC)

    class _FinalizingRepo:
        def __init__(self) -> None:
            self.claim_count = 0
            self.attach_calls: list[dict] = []
            self.provider_error_calls: list[dict] = []
            self.rescheduled: list[int] = []
            self.released = 0
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
                total_items=1,
                in_progress_items=0,
                completed_items=1,
                failed_items=0,
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
            if self.claim_count > 1:
                return None
            return self.job

        async def list_items(self, batch_id: str):
            assert batch_id == "b-finalize"
            invalid_body = _valid_embedding_artifact_response_body()
            invalid_body.pop("model")
            return [
                BatchItemRecord(
                    item_id="i1",
                    batch_id=batch_id,
                    line_number=1,
                    custom_id="req-1",
                    status="completed",
                    request_body={},
                    response_body=invalid_body,
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
                )
            ]

        async def attach_artifacts_and_finalize(self, **kwargs):
            self.attach_calls.append(kwargs)
            return self.job

        async def set_provider_error(self, **kwargs):
            self.provider_error_calls.append(kwargs)
            return self.job

        async def reschedule_finalization(self, *, batch_id: str, worker_id: str, retry_delay_seconds: int) -> bool:
            assert batch_id == "b-finalize"
            assert worker_id == "w1"
            self.rescheduled.append(retry_delay_seconds)
            return True

        async def release_job_lease(self, *, batch_id: str, worker_id: str) -> None:
            assert batch_id == "b-finalize"
            assert worker_id == "w1"
            self.released += 1

    class _StreamingStorage:
        backend_name = "local"

        async def write_lines_stream(self, *, purpose: str, filename: str, lines):  # noqa: ANN001
            assert purpose == "batch_output"
            assert filename == "b-finalize-output.jsonl"
            _ = [line async for line in lines]
            raise AssertionError("expected artifact validation failure before write completion")

    repo = _FinalizingRepo()
    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=repo,  # type: ignore[arg-type]
        storage=_StreamingStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )

    with caplog.at_level(logging.WARNING):
        did_work = await worker.process_once()

    assert did_work is True
    assert repo.attach_calls == [
        {
            "batch_id": "b-finalize",
            "output_file_id": None,
            "error_file_id": None,
            "final_status": BatchJobStatus.FAILED,
            "worker_id": "w1",
        }
    ]
    assert repo.provider_error_calls == [
        {
            "batch_id": "b-finalize",
            "provider_error": "artifact_validation_failed: completed batch item embedding response is missing a valid model",
        }
    ]
    assert repo.rescheduled == []
    assert repo.released == 1
    assert "batch finalization permanently failed batch_id=b-finalize" in caplog.text


@pytest.mark.asyncio
async def test_batch_worker_finalize_artifacts_writes_openai_compatible_rows():
    now = datetime.now(tz=UTC)

    class _FinalizingRepo:
        def __init__(self) -> None:
            self.attach_calls: list[dict] = []
            self.created_file_calls: list[dict] = []

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
                    response_body=_valid_embedding_artifact_response_body(),
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
            self.created_file_calls.append(kwargs)
            purpose = kwargs["purpose"]
            return SimpleNamespace(file_id=f"{purpose}-file", storage_key=kwargs["storage_key"])

        async def attach_artifacts_and_finalize(self, **kwargs):
            self.attach_calls.append(kwargs)
            return SimpleNamespace(batch_id=kwargs["batch_id"])

    class _CapturingStorage:
        backend_name = "local"

        def __init__(self) -> None:
            self.writes: dict[str, dict[str, object]] = {}

        async def write_lines_stream(self, *, purpose: str, filename: str, lines):  # noqa: ANN001
            captured_lines = [line async for line in lines]
            self.writes[purpose] = {"filename": filename, "lines": captured_lines}
            size = sum(len(line) + 1 for line in captured_lines)
            return f"{purpose}/{filename}", size, "checksum"

    repo = _FinalizingRepo()
    storage = _CapturingStorage()
    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=repo,  # type: ignore[arg-type]
        storage=storage,  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )
    job = BatchJobRecord(
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
        created_by_organization_id="org-1",
        created_at=now,
        started_at=now,
        completed_at=None,
        expires_at=None,
    )

    await worker._finalize_artifacts(job)

    output_rows = [json.loads(line) for line in storage.writes["batch_output"]["lines"]]
    error_rows = [json.loads(line) for line in storage.writes["batch_error"]["lines"]]

    assert output_rows == [
        {
            "id": "batch_req_i1",
            "custom_id": "req-1",
            "response": {
                "status_code": 200,
                "request_id": "req_batch_i1",
                "body": {
                    "object": "list",
                    "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                    "model": "provider-embedding-model",
                    "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
                },
            },
            "error": None,
        }
    ]
    assert error_rows == [
        {
            "id": "batch_req_i2",
            "custom_id": "req-2",
            "response": None,
            "error": {"message": "boom", "type": "BatchItemError"},
        }
    ]
    assert repo.attach_calls == [
        {
            "batch_id": "b-finalize",
            "output_file_id": "batch_output-file",
            "error_file_id": "batch_error-file",
            "final_status": BatchJobStatus.COMPLETED,
            "worker_id": "w1",
        }
    ]


@pytest.mark.asyncio
async def test_batch_worker_finalize_artifacts_deletes_unrecorded_artifact_when_file_record_creation_fails():
    now = datetime.now(tz=UTC)

    class _FinalizingRepo:
        async def list_items(self, batch_id: str):
            return [
                BatchItemRecord(
                    item_id="i1",
                    batch_id=batch_id,
                    line_number=1,
                    custom_id="req-1",
                    status="completed",
                    request_body={},
                    response_body=_valid_embedding_artifact_response_body(),
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
                )
            ]

        async def create_file(self, **kwargs):  # noqa: ANN003
            del kwargs
            return None

    class _Storage:
        backend_name = "local"

        def __init__(self) -> None:
            self.deleted: list[str] = []

        async def write_lines_stream(self, *, purpose: str, filename: str, lines):  # noqa: ANN001
            async for _line in lines:
                pass
            return f"{purpose}/{filename}", 10, "checksum"

        async def delete(self, storage_key: str) -> None:
            self.deleted.append(storage_key)

    storage = _Storage()
    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=_FinalizingRepo(),  # type: ignore[arg-type]
        storage=storage,  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )
    job = BatchJobRecord(
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
        total_items=1,
        in_progress_items=0,
        completed_items=1,
        failed_items=0,
        cancelled_items=0,
        locked_by="w1",
        lease_expires_at=now,
        cancel_requested_at=None,
        status_last_updated_at=now,
        created_by_api_key="tok-1",
        created_by_user_id="u1",
        created_by_team_id="t1",
        created_by_organization_id="org-1",
        created_at=now,
        started_at=now,
        completed_at=None,
        expires_at=None,
    )

    with pytest.raises(RuntimeError, match="Failed to create output artifact record"):
        await worker._finalize_artifacts(job)

    assert storage.deleted == ["batch_output/b-finalize-output.jsonl"]


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

        async def renew_item_lease(
            self,
            *,
            item_id: str,
            worker_id: str,
            lease_seconds: int,
            claim_epoch: int | None = None,
        ) -> bool:
            del claim_epoch
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

        async def complete_items_with_outbox_bulk(self, *, items: list[dict], worker_id: str | None) -> str:
            assert len(items) == 1
            updated = await self.mark_item_completed(
                item_id=items[0]["item_id"],
                worker_id=worker_id,
                response_body=items[0]["response_body"],
                usage=items[0]["usage"],
                provider_cost=items[0]["provider_cost"],
                billed_cost=items[0]["billed_cost"],
            )
            return "completed" if updated else "not_owned"

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

        async def renew_item_lease(
            self,
            *,
            item_id: str,
            worker_id: str,
            lease_seconds: int,
            claim_epoch: int | None = None,
        ) -> bool:
            del claim_epoch
            assert item_id == "i1"
            if self.item_locked_by != worker_id:
                return False
            self.item_lease_expires_at = self.now + timedelta(seconds=lease_seconds)
            return True

        async def release_items_for_retry(self, *, item_ids: list[str], worker_id: str, **kwargs) -> list[str]:
            del kwargs
            del item_ids, worker_id
            return []

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
    lease_lost = asyncio.Event()

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

    task = worker._start_heartbeat(renew=_renew, label="item:test", lease_lost_event=lease_lost)
    await asyncio.wait_for(task, timeout=1.0)
    assert calls["count"] == 2
    assert lease_lost.is_set()


@pytest.mark.asyncio
async def test_provider_call_wait_is_cancelled_when_item_lease_is_lost():
    started = asyncio.Event()
    cancelled = asyncio.Event()
    lease_lost = asyncio.Event()

    async def _provider_call() -> None:
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=_FakeRepository(),  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1"),
    )

    wait_task = asyncio.create_task(
        worker._execution_engine._await_with_lease_loss_cancellation(
            _provider_call(),
            lease_lost_event=lease_lost,
            label="item:test",
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1.0)

    lease_lost.set()

    with pytest.raises(BatchItemLeaseLostError):
        await asyncio.wait_for(wait_task, timeout=1.0)
    assert cancelled.is_set()


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


def _work_slice_job(
    *,
    status: BatchJobStatus = BatchJobStatus.IN_PROGRESS,
    batch_id: str = "b-work",
) -> BatchJobRecord:
    now = datetime.now(tz=UTC)
    return BatchJobRecord(
        batch_id=batch_id,
        endpoint="/v1/embeddings",
        status=status,
        execution_mode="managed_internal",
        input_file_id="file-1",
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
        locked_by="w1" if status == BatchJobStatus.FINALIZING else None,
        lease_expires_at=now + timedelta(seconds=60) if status == BatchJobStatus.FINALIZING else None,
        cancel_requested_at=None,
        status_last_updated_at=now,
        created_by_api_key="tok-1",
        created_by_user_id=None,
        created_by_team_id=None,
        created_at=now,
        started_at=now,
        completed_at=None,
        expires_at=None,
        scheduling_model_group="m-1",
        service_tier="standard",
        remaining_work_units=1,
    )


def _work_slice_item() -> BatchItemRecord:
    now = datetime.now(tz=UTC)
    return BatchItemRecord(
        item_id="item-1",
        batch_id="b-work",
        line_number=1,
        custom_id="custom-1",
        status="in_progress",
        request_body={"model": "m-1", "input": "hello"},
        response_body=None,
        error_body=None,
        usage=None,
        provider_cost=0.0,
        billed_cost=0.0,
        attempts=1,
        last_error=None,
        locked_by="w1",
        lease_expires_at=now + timedelta(seconds=60),
        created_at=now,
        started_at=now,
        completed_at=None,
        scheduling_model="m-1",
        scheduling_model_group="m-1",
        estimated_work_units=1,
    )


@pytest.mark.asyncio
async def test_batch_worker_work_slice_mode_processes_claim_without_job_lease():
    now = datetime.now(tz=UTC)

    class _WorkSliceRepo:
        def __init__(self) -> None:
            self.claim_next_job_called = False
            self.claim_next_work_kwargs: dict | None = None
            self.release_claim_items_calls: list[dict] = []
            self.release_job_lease_called = False
            self.refreshed: list[list[str]] = []

        async def claim_next_job(self, **kwargs):  # pragma: no cover
            del kwargs
            self.claim_next_job_called = True
            raise AssertionError("job_fifo claim should not run in work_slice mode")

        async def claim_next_finalization(self, **kwargs):
            del kwargs
            return None

        async def claim_next_work(self, **kwargs):
            self.claim_next_work_kwargs = kwargs
            return BatchWorkClaim(
                claim_id="claim-1",
                worker_id="w1",
                batch_id="b-work",
                endpoint="/v1/embeddings",
                model_group="m-1",
                tenant_scope_type="api_key",
                tenant_scope_id="tok-1",
                service_tier="standard",
                item_ids=["item-1"],
                claimed_work_units=1,
                lease_expires_at=now + timedelta(seconds=60),
            )

        async def get_job(self, batch_id: str):
            assert batch_id == "b-work"
            return _work_slice_job()

        async def load_claim_items(self, item_ids: list[str]):
            assert item_ids == ["item-1"]
            return [_work_slice_item()]

        async def release_claim_items(self, **kwargs) -> int:
            self.release_claim_items_calls.append(kwargs)
            return 0

        async def refresh_jobs_after_claim(self, batch_ids: list[str]):
            self.refreshed.append(batch_ids)
            return [_work_slice_job()]

        async def summarize_runtime_statuses(self, *, now):  # noqa: ANN001
            del now
            return {"queued": 0, "in_progress": 1, "finalizing": 0}

        async def release_job_lease(self, **kwargs):  # pragma: no cover
            del kwargs
            self.release_job_lease_called = True
            raise AssertionError("work-slice item execution must not hold a job lease")

    repo = _WorkSliceRepo()
    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(
            worker_id="w1",
            scheduler_claim_mode="work_slice",
            work_claim_max_items=3,
            work_claim_max_work_units=7,
        ),
    )
    processed: list[tuple[str, list[str]]] = []

    async def _process_items(job, items):  # noqa: ANN001
        processed.append((job.batch_id, [item.item_id for item in items]))

    worker._process_items = _process_items  # type: ignore[assignment]

    did_work = await worker.process_once()

    assert did_work is True
    assert repo.claim_next_job_called is False
    assert repo.claim_next_work_kwargs == {
        "worker_id": "w1",
        "max_items": 3,
        "max_work_units": 7,
        "lease_seconds": 360,
        "scheduler_mode": "slice_v1",
    }
    assert processed == [("b-work", ["item-1"])]
    assert repo.release_claim_items_calls == [{"item_ids": ["item-1"], "worker_id": "w1"}]
    assert repo.refreshed == [["b-work"]]
    assert repo.release_job_lease_called is False


@pytest.mark.asyncio
async def test_batch_worker_work_slice_empty_claim_records_diagnostic_reason(monkeypatch):
    class _EmptyClaimRepo:
        async def claim_next_finalization(self, **kwargs):
            del kwargs
            return None

        async def claim_next_work(self, **kwargs):
            del kwargs
            return None

        async def diagnose_empty_work_claim(self) -> str:
            return "not_before_future"

    recorded_reasons: list[str] = []
    monkeypatch.setattr(
        "src.batch.worker.increment_batch_claim_empty_job",
        lambda *, reason: recorded_reasons.append(reason),
    )

    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=_EmptyClaimRepo(),  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1", scheduler_claim_mode="work_slice"),
    )

    did_work = await worker.process_once()

    assert did_work is False
    assert recorded_reasons == ["not_before_future"]


@pytest.mark.asyncio
async def test_batch_worker_work_slice_empty_claim_diagnostic_failure_falls_back(monkeypatch):
    class _FailingDiagnosticRepo:
        async def claim_next_finalization(self, **kwargs):
            del kwargs
            return None

        async def claim_next_work(self, **kwargs):
            del kwargs
            return None

        async def diagnose_empty_work_claim(self) -> str:
            raise RuntimeError("diagnostic unavailable")

    recorded_reasons: list[str] = []
    monkeypatch.setattr(
        "src.batch.worker.increment_batch_claim_empty_job",
        lambda *, reason: recorded_reasons.append(reason),
    )

    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=_FailingDiagnosticRepo(),  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1", scheduler_claim_mode="work_slice"),
    )

    did_work = await worker.process_once()

    assert did_work is False
    assert recorded_reasons == ["no_available_work"]


@pytest.mark.asyncio
async def test_batch_worker_fifo_active_records_slice_shadow_recommendation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    comparisons: list[dict[str, str]] = []
    better_choices: list[str] = []
    monkeypatch.setattr(
        "src.batch.worker.increment_batch_scheduler_shadow_comparison",
        lambda **kwargs: comparisons.append(kwargs),
    )
    monkeypatch.setattr(
        "src.batch.worker.increment_batch_scheduler_shadow_better_choice",
        lambda *, reason: better_choices.append(reason),
    )

    class _FifoShadowRepo:
        def __init__(self) -> None:
            self.recommend_calls: list[dict] = []
            self.claim_next_work_called = False
            self.released = False

        async def recommend_next_work(self, **kwargs):
            self.recommend_calls.append(kwargs)
            return BatchWorkRecommendation(
                batch_id="b-shadow",
                endpoint="/v1/embeddings",
                model_group="m-1",
                tenant_scope_type="team",
                tenant_scope_id="team-1",
                service_tier="standard",
                size_class="xs",
                item_count=1,
                work_units=1,
                reason="slice_v1",
            )

        async def claim_next_job(self, **kwargs):
            assert kwargs == {"worker_id": "w1", "lease_seconds": 120}
            return _work_slice_job(batch_id="b-active")

        async def claim_next_work(self, **kwargs):  # pragma: no cover
            del kwargs
            self.claim_next_work_called = True
            raise AssertionError("active fifo mode must not claim work slices")

        async def renew_job_lease(self, **kwargs) -> bool:
            del kwargs
            return True

        async def claim_items(self, **kwargs):
            assert kwargs == {
                "batch_id": "b-active",
                "worker_id": "w1",
                "limit": 20,
                "lease_seconds": 360,
            }
            return []

        async def refresh_job_progress(self, batch_id: str):
            assert batch_id == "b-active"
            return None

        async def summarize_runtime_statuses(self, *, now):  # noqa: ANN001
            del now
            return {"queued": 0, "in_progress": 1, "finalizing": 0}

        async def release_job_lease(self, *, batch_id: str, worker_id: str) -> None:
            assert batch_id == "b-active"
            assert worker_id == "w1"
            self.released = True

    repo = _FifoShadowRepo()
    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(
            worker_id="w1",
            scheduler_shadow_mode="slice_v1",
            job_lease_seconds=120,
        ),
    )

    async def _process_items(job, items):  # noqa: ANN001
        assert job.batch_id == "b-active"
        assert items == []

    worker._process_items = _process_items  # type: ignore[assignment]

    did_work = await worker.process_once()
    await worker.drain_shadow_tasks()

    assert did_work is True
    assert repo.recommend_calls == [
        {
            "max_items": 20,
            "max_work_units": 80,
            "claim_order": "round_robin",
            "reason": "slice_v1",
        }
    ]
    assert repo.claim_next_work_called is False
    assert repo.released is True
    assert comparisons == [
        {
            "active_mode": "fifo_v1",
            "shadow_mode": "slice_v1",
            "result": "job_mismatch",
        }
    ]
    assert better_choices == ["slice_v1"]


@pytest.mark.asyncio
async def test_batch_worker_fifo_shadow_recommendation_does_not_block_active_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shadow_started = asyncio.Event()
    release_shadow = asyncio.Event()
    observed_latencies: list[tuple[str, float]] = []
    monkeypatch.setattr(
        "src.batch.worker.observe_batch_scheduler_decision_latency",
        lambda *, mode, latency_seconds: observed_latencies.append((mode, latency_seconds)),
    )

    class _BlockingShadowRepo:
        def __init__(self) -> None:
            self.released = False

        async def recommend_next_work(self, **kwargs):
            del kwargs
            shadow_started.set()
            await release_shadow.wait()
            return BatchWorkRecommendation(
                batch_id="b-shadow",
                endpoint="/v1/embeddings",
                model_group="m-1",
                tenant_scope_type="team",
                tenant_scope_id="team-1",
                service_tier="standard",
                size_class="xs",
                item_count=1,
                work_units=1,
                reason="slice_v1",
            )

        async def claim_next_job(self, **kwargs):
            assert kwargs == {"worker_id": "w1", "lease_seconds": 120}
            return _work_slice_job(batch_id="b-active")

        async def renew_job_lease(self, **kwargs) -> bool:
            del kwargs
            return True

        async def claim_items(self, **kwargs):
            del kwargs
            return []

        async def refresh_job_progress(self, batch_id: str):
            assert batch_id == "b-active"
            return None

        async def summarize_runtime_statuses(self, *, now):  # noqa: ANN001
            del now
            return {"queued": 0, "in_progress": 1, "finalizing": 0}

        async def release_job_lease(self, *, batch_id: str, worker_id: str) -> None:
            assert batch_id == "b-active"
            assert worker_id == "w1"
            self.released = True

    repo = _BlockingShadowRepo()
    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(
            worker_id="w1",
            scheduler_shadow_mode="slice_v1",
            job_lease_seconds=120,
            scheduler_shadow_decision_timeout_seconds=5,
        ),
    )

    async def _process_items(job, items):  # noqa: ANN001
        assert job.batch_id == "b-active"
        assert items == []

    worker._process_items = _process_items  # type: ignore[assignment]

    did_work = await asyncio.wait_for(worker.process_once(), timeout=0.2)
    await asyncio.sleep(0)

    assert did_work is True
    assert repo.released is True
    assert shadow_started.is_set()
    assert observed_latencies and observed_latencies[0][0] == "fifo_v1"
    release_shadow.set()
    await worker.drain_shadow_tasks()


@pytest.mark.asyncio
async def test_batch_worker_shadow_timeout_records_bounded_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    comparisons: list[dict[str, str]] = []
    monkeypatch.setattr(
        "src.batch.worker.increment_batch_scheduler_shadow_comparison",
        lambda **kwargs: comparisons.append(kwargs),
    )
    release_shadow = asyncio.Event()

    class _TimeoutShadowRepo:
        async def recommend_next_work(self, **kwargs):
            del kwargs
            await release_shadow.wait()
            return None

        async def claim_next_job(self, **kwargs):
            del kwargs
            return _work_slice_job(batch_id="b-active")

        async def renew_job_lease(self, **kwargs) -> bool:
            del kwargs
            return True

        async def claim_items(self, **kwargs):
            del kwargs
            return []

        async def refresh_job_progress(self, batch_id: str):
            assert batch_id == "b-active"
            return None

        async def summarize_runtime_statuses(self, *, now):  # noqa: ANN001
            del now
            return {"queued": 0, "in_progress": 1, "finalizing": 0}

        async def release_job_lease(self, **kwargs) -> None:
            del kwargs

    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=_TimeoutShadowRepo(),  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(
            worker_id="w1",
            scheduler_shadow_mode="slice_v1",
            scheduler_shadow_decision_timeout_seconds=0.001,
        ),
    )

    async def _process_items(job, items):  # noqa: ANN001
        del job, items

    worker._process_items = _process_items  # type: ignore[assignment]

    assert await worker.process_once() is True
    await worker.drain_shadow_tasks()

    assert {
        "active_mode": "fifo_v1",
        "shadow_mode": "slice_v1",
        "result": "shadow_timeout",
    } in comparisons
    release_shadow.set()


@pytest.mark.asyncio
async def test_batch_worker_slice_active_records_model_capacity_shadow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(tz=UTC)
    comparisons: list[dict[str, str]] = []
    better_choices: list[str] = []
    monkeypatch.setattr(
        "src.batch.worker.increment_batch_scheduler_shadow_comparison",
        lambda **kwargs: comparisons.append(kwargs),
    )
    monkeypatch.setattr(
        "src.batch.worker.increment_batch_scheduler_shadow_better_choice",
        lambda *, reason: better_choices.append(reason),
    )

    class _SliceShadowRepo:
        def __init__(self) -> None:
            self.recommend_calls: list[dict] = []
            self.claim_next_work_calls: list[dict] = []

        async def recommend_next_work(self, **kwargs):
            self.recommend_calls.append(kwargs)
            return BatchWorkRecommendation(
                batch_id="b-capacity",
                endpoint="/v1/embeddings",
                model_group="model-a",
                tenant_scope_type="team",
                tenant_scope_id="team-1",
                service_tier="standard",
                size_class="s",
                item_count=2,
                work_units=3,
                reason="model_capacity_v1",
            )

        async def claim_next_work(self, **kwargs):
            self.claim_next_work_calls.append(kwargs)
            return BatchWorkClaim(
                claim_id="claim-active",
                worker_id="w1",
                batch_id="b-active",
                endpoint="/v1/embeddings",
                model_group="model-b",
                tenant_scope_type="team",
                tenant_scope_id="team-2",
                service_tier="standard",
                item_ids=["item-1"],
                claimed_work_units=1,
                lease_expires_at=now + timedelta(seconds=60),
            )

    class _CapacityResolver:
        async def select_model_groups(self, **kwargs):
            assert kwargs == {"max_items": 4, "max_work_units": 9}
            return [
                SimpleNamespace(
                    snapshot=SimpleNamespace(
                        model_group="model-a",
                        service_tier="standard",
                        in_flight_items=2,
                        available_in_flight_items=5,
                        in_flight_work_units=4,
                        tpm_remaining=20,
                    ),
                    max_items=4,
                    max_work_units=9,
                )
            ]

    repo = _SliceShadowRepo()
    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(
            worker_id="w1",
            scheduler_claim_mode="work_slice",
            scheduler_shadow_mode="model_capacity_v1",
            work_claim_max_items=4,
            work_claim_max_work_units=9,
        ),
        model_capacity_resolver=_CapacityResolver(),
    )

    claim = await worker._claim_next_work_slice()
    await worker.drain_shadow_tasks()

    assert claim is not None
    assert repo.recommend_calls == [
        {
            "max_items": 4,
            "max_work_units": 9,
            "allowed_model_groups": ["model-a"],
            "service_tier": "standard",
            "claim_order": "fifo",
            "capacity_model_group": "model-a",
            "capacity_service_tier": "standard",
            "capacity_max_in_flight_items": 7,
            "capacity_max_in_flight_work_units": 24,
            "allow_oversized_first_item": False,
            "reason": "model_capacity_v1",
        }
    ]
    assert repo.claim_next_work_calls == [
        {
            "worker_id": "w1",
            "max_items": 4,
            "max_work_units": 9,
            "lease_seconds": 360,
            "scheduler_mode": "slice_v1",
        }
    ]
    assert comparisons == [
        {
            "active_mode": "slice_v1",
            "shadow_mode": "model_capacity_v1",
            "result": "job_mismatch",
        }
    ]
    assert better_choices == ["model_capacity_v1"]


@pytest.mark.asyncio
async def test_batch_worker_model_capacity_active_fifo_shadow_uses_full_backlog() -> None:
    now = datetime.now(tz=UTC)

    class _ModelCapacityActiveRepo:
        def __init__(self) -> None:
            self.recommend_calls: list[dict] = []
            self.claim_next_work_calls: list[dict] = []

        async def recommend_next_work(self, **kwargs):
            self.recommend_calls.append(kwargs)
            return BatchWorkRecommendation(
                batch_id="b-shadow-fifo",
                endpoint="/v1/embeddings",
                model_group="model-a",
                tenant_scope_type="team",
                tenant_scope_id="team-1",
                service_tier="standard",
                size_class="s",
                item_count=1,
                work_units=1,
                reason="fifo_v1",
            )

        async def claim_next_work(self, **kwargs):
            self.claim_next_work_calls.append(kwargs)
            return BatchWorkClaim(
                claim_id="claim-active",
                worker_id="w1",
                batch_id="b-active",
                endpoint="/v1/embeddings",
                model_group="model-a",
                tenant_scope_type="team",
                tenant_scope_id="team-2",
                service_tier="standard",
                item_ids=["item-1"],
                claimed_work_units=1,
                lease_expires_at=now + timedelta(seconds=60),
            )

    class _CapacityResolver:
        def __init__(self) -> None:
            self.results: list[tuple[str, str]] = []

        async def select_model_groups(self, **kwargs):
            assert kwargs == {"max_items": 4, "max_work_units": 9}
            return [
                SimpleNamespace(
                    snapshot=SimpleNamespace(
                        model_group="model-a",
                        service_tier="standard",
                        in_flight_items=2,
                        available_in_flight_items=5,
                        in_flight_work_units=4,
                        tpm_remaining=20,
                    ),
                    max_items=4,
                    max_work_units=9,
                )
            ]

        def record_selection(self, snapshot):
            assert snapshot.model_group == "model-a"

        def record_claim_result(self, *, model_group: str, result: str) -> None:
            self.results.append((model_group, result))

    repo = _ModelCapacityActiveRepo()
    resolver = _CapacityResolver()
    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(
            worker_id="w1",
            scheduler_claim_mode="work_slice",
            scheduler_shadow_mode="fifo_v1",
            work_claim_max_items=4,
            work_claim_max_work_units=9,
            model_capacity_enabled=True,
        ),
        model_capacity_resolver=resolver,
    )

    claim = await worker._claim_next_work_slice()
    await worker.drain_shadow_tasks()

    assert claim is not None
    assert repo.recommend_calls == [
        {
            "max_items": 4,
            "max_work_units": 9,
            "claim_order": "fifo",
            "reason": "fifo_v1",
        }
    ]
    assert "legacy_only" not in repo.recommend_calls[0]
    assert repo.claim_next_work_calls == [
        {
            "worker_id": "w1",
            "max_items": 4,
            "max_work_units": 9,
            "lease_seconds": 360,
            "allowed_model_groups": ["model-a"],
            "service_tier": "standard",
            "claim_order": "fifo",
            "capacity_model_group": "model-a",
            "capacity_service_tier": "standard",
            "capacity_max_in_flight_items": 7,
            "capacity_max_in_flight_work_units": 24,
            "allow_oversized_first_item": False,
            "scheduler_mode": "model_capacity_v1",
        }
    ]
    assert resolver.results == [("model-a", "claimed")]


@pytest.mark.asyncio
async def test_batch_worker_slice_active_logs_fair_share_shadow_no_recommendation(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from src.batch import worker as worker_module

    now = datetime.now(tz=UTC)
    caplog.set_level(logging.INFO, logger=worker_module.__name__)
    shadow_results: list[tuple[str, str, str]] = []
    comparisons: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        worker_module,
        "increment_batch_scheduler_shadow_decision",
        lambda *, model_group, service_tier, result: shadow_results.append(
            (model_group, service_tier, result)
        ),
    )
    monkeypatch.setattr(
        worker_module,
        "increment_batch_scheduler_shadow_comparison",
        lambda *, active_mode, shadow_mode, result: comparisons.append(
            (active_mode, shadow_mode, result)
        ),
    )

    class _SliceFairShareShadowRepo:
        def __init__(self) -> None:
            self.recommend_fair_share_calls: list[dict] = []
            self.claim_next_work_calls: list[dict] = []

        async def recommend_next_fair_share_flow(self, **kwargs):
            self.recommend_fair_share_calls.append(kwargs)
            return SimpleNamespace(flow=None, result="no_active_flow")

        async def claim_next_work(self, **kwargs):
            self.claim_next_work_calls.append(kwargs)
            return BatchWorkClaim(
                claim_id="claim-active",
                worker_id="w1",
                batch_id="b-active",
                endpoint="/v1/embeddings",
                model_group="model-a",
                tenant_scope_type="team",
                tenant_scope_id="team-secret",
                service_tier="standard",
                item_ids=["item-1"],
                claimed_work_units=1,
                lease_expires_at=now + timedelta(seconds=60),
            )

    class _CapacityResolver:
        async def select_model_groups(self, **kwargs):
            assert kwargs == {"max_items": 4, "max_work_units": 9}
            return [
                SimpleNamespace(
                    snapshot=SimpleNamespace(
                        model_group="model-a",
                        service_tier="standard",
                    ),
                    max_items=4,
                    max_work_units=9,
                )
            ]

    repo = _SliceFairShareShadowRepo()
    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(
            worker_id="w1",
            scheduler_claim_mode="work_slice",
            scheduler_shadow_mode="fair_share_v1",
            work_claim_max_items=4,
            work_claim_max_work_units=9,
        ),
        model_capacity_resolver=_CapacityResolver(),
    )

    claim = await worker._claim_next_work_slice()
    await worker.drain_shadow_tasks()

    assert claim is not None
    assert len(repo.recommend_fair_share_calls) == 1
    assert repo.claim_next_work_calls == [
        {
            "worker_id": "w1",
            "max_items": 4,
            "max_work_units": 9,
            "lease_seconds": 360,
            "scheduler_mode": "slice_v1",
        }
    ]
    assert shadow_results == [("model-a", "standard", "no_active_flow")]
    assert comparisons == [("slice_v1", "fair_share_v1", "no_active_flow")]
    records = [
        record
        for record in caplog.records
        if record.getMessage() == "batch scheduler shadow job decision"
    ]
    assert len(records) == 1
    record = records[0]
    assert record.active_batch_id == "b-active"
    assert record.shadow_batch_id == ""
    assert record.active_model_group == "model-a"
    assert record.shadow_model_group == "model-a"
    assert record.shadow_result == "no_active_flow"
    assert record.shadow_flow_match is False
    assert "team-secret" not in record.__dict__.values()


@pytest.mark.asyncio
async def test_batch_worker_fair_share_shadow_does_not_block_work_slice_claim() -> None:
    now = datetime.now(tz=UTC)
    shadow_started = asyncio.Event()
    release_shadow = asyncio.Event()

    class _WorkSliceShadowRepo:
        def __init__(self) -> None:
            self.claim_next_work_calls: list[dict] = []
            self.released: list[list[str]] = []

        async def recommend_next_fair_share_flow(self, **kwargs):
            del kwargs
            shadow_started.set()
            await release_shadow.wait()
            return SimpleNamespace(flow=None, result="no_active_flow")

        async def claim_next_work(self, **kwargs):
            self.claim_next_work_calls.append(kwargs)
            return BatchWorkClaim(
                claim_id="claim-active",
                worker_id="w1",
                batch_id="b-active",
                endpoint="/v1/embeddings",
                model_group="model-a",
                tenant_scope_type="team",
                tenant_scope_id="team-1",
                service_tier="standard",
                item_ids=["item-1"],
                claimed_work_units=1,
                lease_expires_at=now + timedelta(seconds=60),
            )

        async def get_job(self, batch_id: str):
            assert batch_id == "b-active"
            return _work_slice_job(batch_id="b-active")

        async def load_claim_items(self, item_ids: list[str]):
            assert item_ids == ["item-1"]
            return [_work_slice_item()]

        async def release_claim_items(self, **kwargs) -> int:
            self.released.append(kwargs["item_ids"])
            return 1

        async def refresh_jobs_after_claim(self, batch_ids: list[str]):
            assert batch_ids == ["b-active"]
            return []

        async def summarize_runtime_statuses(self, *, now):  # noqa: ANN001
            del now
            return {"queued": 0, "in_progress": 1, "finalizing": 0}

        async def claim_next_finalization(self, **kwargs):
            del kwargs
            return None

    class _CapacityResolver:
        async def select_model_groups(self, **kwargs):
            assert kwargs == {"max_items": 4, "max_work_units": 9}
            return [
                SimpleNamespace(
                    snapshot=SimpleNamespace(model_group="model-a", service_tier="standard"),
                    max_items=4,
                    max_work_units=9,
                )
            ]

    repo = _WorkSliceShadowRepo()
    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(
            worker_id="w1",
            scheduler_claim_mode="work_slice",
            scheduler_shadow_mode="fair_share_v1",
            work_claim_max_items=4,
            work_claim_max_work_units=9,
            scheduler_shadow_decision_timeout_seconds=5,
        ),
        model_capacity_resolver=_CapacityResolver(),
    )

    async def _process_items(job, items):  # noqa: ANN001
        assert job.batch_id == "b-active"
        assert [item.item_id for item in items] == ["item-1"]

    worker._process_items = _process_items  # type: ignore[assignment]

    did_work = await asyncio.wait_for(worker.process_once(), timeout=0.2)
    await asyncio.sleep(0)

    assert did_work is True
    assert repo.claim_next_work_calls == [
        {
            "worker_id": "w1",
            "max_items": 4,
            "max_work_units": 9,
            "lease_seconds": 360,
            "scheduler_mode": "slice_v1",
        }
    ]
    assert repo.released == [["item-1"]]
    assert shadow_started.is_set()
    release_shadow.set()
    await worker.drain_shadow_tasks()


@pytest.mark.asyncio
async def test_batch_worker_capacity_claim_tries_next_model_after_empty_selection() -> None:
    now = datetime.now(tz=UTC)

    class _CapacityRepo:
        def __init__(self) -> None:
            self.calls: list[dict] = []
            self.diagnostic_calls: list[dict] = []

        async def claim_next_work(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs.get("allowed_model_groups") == ["model-b"]:
                return BatchWorkClaim(
                    claim_id="claim-b",
                    worker_id="w1",
                    batch_id="b-work",
                    endpoint="/v1/embeddings",
                    model_group="model-b",
                    tenant_scope_type="api_key",
                    tenant_scope_id="tok-1",
                    service_tier="standard",
                    item_ids=["item-1"],
                    claimed_work_units=1,
                    lease_expires_at=now + timedelta(seconds=60),
                )
            return None

        async def diagnose_model_group_work_claim_empty(self, **kwargs):
            self.diagnostic_calls.append(kwargs)
            return "oversized_head_item"

    class _CapacityResolver:
        def __init__(self) -> None:
            self.selected: list[str] = []
            self.results: list[tuple[str, str]] = []

        async def select_model_groups(self, **kwargs):
            assert kwargs == {"max_items": 3, "max_work_units": 7}
            return [
                SimpleNamespace(
                    snapshot=SimpleNamespace(
                        model_group="model-a",
                        service_tier="standard",
                        max_in_flight_items=99,
                        in_flight_items=6,
                        available_in_flight_items=16,
                        in_flight_work_units=11,
                        tpm_remaining=25,
                    ),
                    max_items=2,
                    max_work_units=5,
                ),
                SimpleNamespace(
                    snapshot=SimpleNamespace(
                        model_group="model-b",
                        service_tier="standard",
                        max_in_flight_items=42,
                        in_flight_items=3,
                        available_in_flight_items=1,
                        in_flight_work_units=2,
                        tpm_remaining=None,
                    ),
                    max_items=1,
                    max_work_units=3,
                ),
            ]

        def record_selection(self, snapshot):
            self.selected.append(snapshot.model_group)

        def record_claim_result(self, *, model_group: str, result: str) -> None:
            self.results.append((model_group, result))

    repo = _CapacityRepo()
    resolver = _CapacityResolver()
    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(
            worker_id="w1",
            scheduler_claim_mode="work_slice",
            work_claim_max_items=3,
            work_claim_max_work_units=7,
            model_capacity_enabled=True,
        ),
        model_capacity_resolver=resolver,
    )

    claim = await worker._claim_next_work_slice()
    await worker.drain_shadow_tasks()

    assert claim is not None
    assert claim.model_group == "model-b"
    assert repo.calls == [
        {
            "worker_id": "w1",
            "max_items": 2,
            "max_work_units": 5,
            "lease_seconds": 360,
            "allowed_model_groups": ["model-a"],
            "service_tier": "standard",
            "claim_order": "fifo",
            "capacity_model_group": "model-a",
            "capacity_service_tier": "standard",
            "capacity_max_in_flight_items": 22,
            "capacity_max_in_flight_work_units": 36,
            "allow_oversized_first_item": False,
            "scheduler_mode": "model_capacity_v1",
        },
        {
            "worker_id": "w1",
            "max_items": 1,
            "max_work_units": 3,
            "lease_seconds": 360,
            "allowed_model_groups": ["model-b"],
            "service_tier": "standard",
            "claim_order": "fifo",
            "capacity_model_group": "model-b",
            "capacity_service_tier": "standard",
            "capacity_max_in_flight_items": 4,
            "capacity_max_in_flight_work_units": None,
            "allow_oversized_first_item": False,
            "scheduler_mode": "model_capacity_v1",
        },
    ]
    assert resolver.selected == ["model-a", "model-b"]
    assert resolver.results == [
        ("model-a", "oversized_head_item"),
        ("model-b", "claimed"),
    ]
    assert repo.diagnostic_calls == [
        {
            "model_group": "model-a",
            "service_tier": "standard",
            "max_work_units": 5,
            "capacity_max_in_flight_items": 22,
            "capacity_max_in_flight_work_units": 36,
        }
    ]


@pytest.mark.asyncio
async def test_batch_worker_capacity_claim_uses_legacy_fallback_when_no_model_is_eligible() -> None:
    now = datetime.now(tz=UTC)

    class _LegacyRepo:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def claim_next_work(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs.get("legacy_only"):
                return BatchWorkClaim(
                    claim_id="claim-legacy",
                    worker_id="w1",
                    batch_id="b-legacy",
                    endpoint="/v1/embeddings",
                    model_group=None,
                    tenant_scope_type=None,
                    tenant_scope_id=None,
                    service_tier="standard",
                    item_ids=["item-1"],
                    claimed_work_units=1,
                    lease_expires_at=now + timedelta(seconds=60),
                )
            return None

    class _CapacityResolver:
        def __init__(self) -> None:
            self.results: list[tuple[str, str]] = []

        async def select_model_groups(self, **kwargs):
            assert kwargs == {"max_items": 3, "max_work_units": 7}
            return []

        def record_claim_result(self, *, model_group: str, result: str) -> None:
            self.results.append((model_group, result))

    repo = _LegacyRepo()
    resolver = _CapacityResolver()
    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(
            worker_id="w1",
            scheduler_claim_mode="work_slice",
            work_claim_max_items=3,
            work_claim_max_work_units=7,
            model_capacity_enabled=True,
        ),
        model_capacity_resolver=resolver,
    )

    claim = await worker._claim_next_work_slice()
    await worker.drain_shadow_tasks()

    assert claim is not None
    assert claim.batch_id == "b-legacy"
    assert repo.calls == [
        {
            "worker_id": "w1",
            "max_items": 3,
            "max_work_units": 7,
            "lease_seconds": 360,
            "legacy_only": True,
            "claim_order": "fifo",
            "scheduler_mode": "model_capacity_v1",
        }
    ]
    assert resolver.results == [("legacy", "claimed")]


@pytest.mark.asyncio
async def test_batch_worker_capacity_claim_uses_tenant_fair_share_when_enabled() -> None:
    now = datetime.now(tz=UTC)

    class _FairShareRepo:
        def __init__(self) -> None:
            self.fair_share_calls: list[dict] = []
            self.claim_next_work_called = False

        async def claim_next_fair_share_work(self, **kwargs):
            self.fair_share_calls.append(kwargs)
            return SimpleNamespace(
                result="claimed",
                claim=BatchWorkClaim(
                    claim_id="claim-flow",
                    worker_id="w1",
                    batch_id="b-flow",
                    endpoint="/v1/embeddings",
                    model_group="model-a",
                    tenant_scope_type="team",
                    tenant_scope_id="team-1",
                    service_tier="standard",
                    item_ids=["item-1"],
                    claimed_work_units=3,
                    lease_expires_at=now + timedelta(seconds=60),
                ),
            )

        async def claim_next_work(self, **kwargs):  # pragma: no cover
            del kwargs
            self.claim_next_work_called = True
            raise AssertionError("model-only claim should not run when tenant fair-share is enabled")

    class _CapacityResolver:
        def __init__(self) -> None:
            self.results: list[tuple[str, str]] = []

        async def select_model_groups(self, **kwargs):
            assert kwargs == {"max_items": 4, "max_work_units": 9}
            return [
                SimpleNamespace(
                    snapshot=SimpleNamespace(
                        model_group="model-a",
                        service_tier="standard",
                        in_flight_items=2,
                        available_in_flight_items=5,
                        in_flight_work_units=4,
                        tpm_remaining=20,
                    ),
                    max_items=4,
                    max_work_units=9,
                )
            ]

        def record_selection(self, snapshot):
            assert snapshot.model_group == "model-a"

        def record_claim_result(self, *, model_group: str, result: str) -> None:
            self.results.append((model_group, result))

    repo = _FairShareRepo()
    resolver = _CapacityResolver()
    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(
            worker_id="w1",
            scheduler_claim_mode="work_slice",
            work_claim_max_items=4,
            work_claim_max_work_units=9,
            model_capacity_enabled=True,
            tenant_fair_share_enabled=True,
            tenant_fair_share_base_quantum_work_units=16,
            tenant_fair_share_max_deficit_multiplier=8,
            tenant_max_in_flight_work_units=32,
        ),
        model_capacity_resolver=resolver,
    )

    claim = await worker._claim_next_work_slice()
    await worker.drain_shadow_tasks()

    assert claim is not None
    assert claim.batch_id == "b-flow"
    assert repo.claim_next_work_called is False
    assert repo.fair_share_calls == [
        {
            "worker_id": "w1",
            "service_tier": "standard",
            "model_group": "model-a",
            "max_items": 4,
            "max_work_units": 9,
            "lease_seconds": 360,
            "capacity_max_in_flight_items": 7,
            "capacity_max_in_flight_work_units": 24,
            "base_quantum_work_units": 16,
            "max_deficit_multiplier": 8,
            "tenant_max_in_flight_work_units": 32,
            "size_aware_scheduling_enabled": False,
            "aging_seconds_per_work_unit": 30,
            "max_age_credit_work_units": 1000,
            "min_large_job_claim_interval_seconds": 30,
            "small_job_max_work_units": 100,
            "max_active_flows_per_decision": 100,
            "max_candidate_jobs_per_flow": 50,
            "work_claim_min_items_for_microbatch": 4,
            "scheduler_mode": "fair_share_v1",
        }
    ]
    assert resolver.results == [("model-a", "claimed")]


@pytest.mark.asyncio
async def test_batch_worker_size_aware_shadow_preserves_active_smart_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.batch import worker as worker_module

    now = datetime.now(tz=UTC)
    shadow_results: list[str] = []
    observed_ratios: list[float] = []
    monkeypatch.setattr(
        worker_module,
        "increment_batch_scheduler_shadow_decision",
        lambda *, model_group, service_tier, result: shadow_results.append(result),
    )
    monkeypatch.setattr(
        worker_module,
        "observe_batch_scheduler_shadow_share_ratio",
        lambda *, model_group, service_tier, ratio: observed_ratios.append(ratio),
    )

    class _FairShareShadowRepo:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.recommend_calls: list[dict] = []
            self.fair_share_calls: list[dict] = []
            self.claim_next_work_called = False

        async def recommend_next_fair_share_flow(self, **kwargs):
            self.calls.append("recommend")
            self.recommend_calls.append(kwargs)
            return SimpleNamespace(
                result="recommended",
                claim=None,
                flow=SimpleNamespace(
                    model_group="model-a",
                    service_tier="standard",
                    tenant_scope_type="team",
                    tenant_scope_id="team-1",
                    in_flight_work_units=1,
                ),
                recommended_batch_id="b-shadow",
                recommended_size_class="xs",
                recommended_scheduler_rank=0.5,
                recommended_age_credit_work_units=1,
                recommended_policy_reason="small_remaining_work",
                expected_share=0.5,
                total_in_flight_work_units=5,
            )

        async def claim_next_fair_share_work(self, **kwargs):
            self.calls.append("claim")
            self.fair_share_calls.append(kwargs)
            return SimpleNamespace(
                result="claimed",
                claim=BatchWorkClaim(
                    claim_id="claim-flow",
                    worker_id="w1",
                    batch_id="b-active",
                    endpoint="/v1/embeddings",
                    model_group="model-a",
                    tenant_scope_type="team",
                    tenant_scope_id="team-1",
                    service_tier="standard",
                    item_ids=["item-1"],
                    claimed_work_units=3,
                    lease_expires_at=now + timedelta(seconds=60),
                ),
            )

        async def claim_next_work(self, **kwargs):  # pragma: no cover
            del kwargs
            self.claim_next_work_called = True
            raise AssertionError("model-only claim should not run when active fair-share succeeds")

    class _CapacityResolver:
        def __init__(self) -> None:
            self.results: list[tuple[str, str]] = []

        async def select_model_groups(self, **kwargs):
            assert kwargs == {"max_items": 4, "max_work_units": 9}
            return [
                SimpleNamespace(
                    snapshot=SimpleNamespace(
                        model_group="model-a",
                        service_tier="standard",
                        in_flight_items=2,
                        available_in_flight_items=5,
                        in_flight_work_units=4,
                        tpm_remaining=20,
                    ),
                    max_items=4,
                    max_work_units=9,
                )
            ]

        def record_selection(self, snapshot):
            assert snapshot.model_group == "model-a"

        def record_claim_result(self, *, model_group: str, result: str) -> None:
            self.results.append((model_group, result))

    repo = _FairShareShadowRepo()
    resolver = _CapacityResolver()
    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(
            worker_id="w1",
            scheduler_claim_mode="work_slice",
            work_claim_max_items=4,
            work_claim_max_work_units=9,
            work_claim_min_items_for_microbatch=5,
            model_capacity_enabled=True,
            scheduler_shadow_enabled=True,
            tenant_fair_share_enabled=True,
            tenant_fair_share_base_quantum_work_units=16,
            tenant_fair_share_max_deficit_multiplier=8,
            tenant_max_in_flight_work_units=32,
            size_aware_scheduling_enabled=True,
            aging_seconds_per_work_unit=11,
            max_age_credit_work_units=42,
            min_large_job_claim_interval_seconds=7,
            small_job_max_work_units=9,
        ),
        model_capacity_resolver=resolver,
    )

    claim = await worker._claim_next_work_slice()
    await worker.drain_shadow_tasks()

    assert claim is not None
    assert claim.batch_id == "b-active"
    assert repo.claim_next_work_called is False
    assert repo.calls == ["claim"]
    assert repo.recommend_calls == []
    assert repo.fair_share_calls == [
        {
            "worker_id": "w1",
            "service_tier": "standard",
            "model_group": "model-a",
            "max_items": 4,
            "max_work_units": 9,
            "lease_seconds": 360,
            "capacity_max_in_flight_items": 7,
            "capacity_max_in_flight_work_units": 24,
            "base_quantum_work_units": 16,
            "max_deficit_multiplier": 8,
            "tenant_max_in_flight_work_units": 32,
            "size_aware_scheduling_enabled": True,
            "aging_seconds_per_work_unit": 11,
            "max_age_credit_work_units": 42,
            "min_large_job_claim_interval_seconds": 7,
            "small_job_max_work_units": 9,
            "max_active_flows_per_decision": 100,
            "max_candidate_jobs_per_flow": 50,
            "work_claim_min_items_for_microbatch": 5,
            "scheduler_mode": "smart_v1",
        }
    ]
    assert shadow_results == []
    assert observed_ratios == []
    assert resolver.results == [("model-a", "claimed")]


@pytest.mark.asyncio
async def test_batch_worker_shadow_recommends_fair_share_without_claiming_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.batch import worker as worker_module

    now = datetime.now(tz=UTC)
    shadow_results: list[dict[str, str]] = []
    observed_ratios: list[dict[str, object]] = []
    monkeypatch.setattr(
        worker_module,
        "increment_batch_scheduler_shadow_decision",
        lambda *, model_group, service_tier, result: shadow_results.append(
            {
                "model_group": model_group,
                "service_tier": service_tier,
                "result": result,
            }
        ),
    )
    monkeypatch.setattr(
        worker_module,
        "observe_batch_scheduler_shadow_share_ratio",
        lambda *, model_group, service_tier, ratio: observed_ratios.append(
            {
                "model_group": model_group,
                "service_tier": service_tier,
                "ratio": ratio,
            }
        ),
    )

    class _ShadowRepo:
        def __init__(self) -> None:
            self.recommend_calls: list[dict] = []
            self.fair_share_claim_called = False
            self.claim_next_work_calls: list[dict] = []

        async def recommend_next_fair_share_flow(self, **kwargs):
            self.recommend_calls.append(kwargs)
            return SimpleNamespace(
                result="recommended",
                claim=None,
                flow=SimpleNamespace(
                    model_group="model-a",
                    service_tier="standard",
                    tenant_scope_type="team",
                    tenant_scope_id="team-1",
                    in_flight_work_units=1,
                ),
                expected_share=0.5,
                total_in_flight_work_units=5,
            )

        async def claim_next_fair_share_work(self, **kwargs):  # pragma: no cover
            del kwargs
            self.fair_share_claim_called = True
            raise AssertionError("shadow mode should not claim through fair-share")

        async def claim_next_work(self, **kwargs):
            self.claim_next_work_calls.append(kwargs)
            return BatchWorkClaim(
                claim_id="claim-model",
                worker_id="w1",
                batch_id="b-model",
                endpoint="/v1/embeddings",
                model_group="model-a",
                tenant_scope_type="team",
                tenant_scope_id="team-1",
                service_tier="standard",
                item_ids=["item-1"],
                claimed_work_units=3,
                lease_expires_at=now + timedelta(seconds=60),
            )

    class _CapacityResolver:
        def __init__(self) -> None:
            self.results: list[tuple[str, str]] = []

        async def select_model_groups(self, **kwargs):
            assert kwargs == {"max_items": 4, "max_work_units": 9}
            return [
                SimpleNamespace(
                    snapshot=SimpleNamespace(
                        model_group="model-a",
                        service_tier="standard",
                        in_flight_items=2,
                        available_in_flight_items=5,
                        in_flight_work_units=4,
                        tpm_remaining=20,
                    ),
                    max_items=4,
                    max_work_units=9,
                )
            ]

        def record_selection(self, snapshot):
            assert snapshot.model_group == "model-a"

        def record_claim_result(self, *, model_group: str, result: str) -> None:
            self.results.append((model_group, result))

    repo = _ShadowRepo()
    resolver = _CapacityResolver()
    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(
            worker_id="w1",
            scheduler_claim_mode="work_slice",
            work_claim_max_items=4,
            work_claim_max_work_units=9,
            model_capacity_enabled=True,
            scheduler_shadow_enabled=True,
            tenant_fair_share_base_quantum_work_units=16,
            tenant_fair_share_max_deficit_multiplier=8,
            tenant_max_in_flight_work_units=32,
        ),
        model_capacity_resolver=resolver,
    )

    claim = await worker._claim_next_work_slice()
    await worker.drain_shadow_tasks()

    assert claim is not None
    assert claim.batch_id == "b-model"
    assert repo.fair_share_claim_called is False
    assert repo.recommend_calls == [
        {
            "service_tier": "standard",
            "model_group": "model-a",
            "max_items": 4,
            "max_work_units": 9,
            "base_quantum_work_units": 16,
            "max_deficit_multiplier": 8,
            "tenant_max_in_flight_work_units": 32,
            "size_aware_scheduling_enabled": False,
            "aging_seconds_per_work_unit": 30,
            "max_age_credit_work_units": 1000,
            "min_large_job_claim_interval_seconds": 30,
            "small_job_max_work_units": 100,
            "max_active_flows_per_decision": 100,
            "max_candidate_jobs_per_flow": 50,
        }
    ]
    assert shadow_results == [
        {
            "model_group": "model-a",
            "service_tier": "standard",
            "result": "match",
        }
    ]
    assert observed_ratios == [
        {
            "model_group": "model-a",
            "service_tier": "standard",
            "ratio": 1.0,
        }
    ]
    assert resolver.results == [("model-a", "claimed")]


def test_batch_worker_shadow_decision_records_recommended_job_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from src.batch import worker as worker_module

    caplog.set_level(logging.INFO, logger=worker_module.__name__)
    shadow_results: list[str] = []
    observed_ratios: list[float] = []
    monkeypatch.setattr(
        worker_module,
        "increment_batch_scheduler_shadow_decision",
        lambda *, model_group, service_tier, result: shadow_results.append(result),
    )
    monkeypatch.setattr(
        worker_module,
        "observe_batch_scheduler_shadow_share_ratio",
        lambda *, model_group, service_tier, ratio: observed_ratios.append(ratio),
    )

    recommendation = SimpleNamespace(
        flow=SimpleNamespace(
            model_group="model-a",
            service_tier="standard",
            tenant_scope_type="team",
            tenant_scope_id="team-1",
            in_flight_work_units=1,
        ),
        recommended_batch_id="b-recommended",
        recommended_size_class="xs",
        recommended_scheduler_rank=1.25,
        recommended_age_credit_work_units=2,
        recommended_policy_reason="aging_credit",
        expected_share=0.5,
        total_in_flight_work_units=5,
    )
    claim = BatchWorkClaim(
        claim_id="claim-model",
        worker_id="w1",
        batch_id="b-actual",
        endpoint="/v1/embeddings",
        model_group="model-a",
        tenant_scope_type="team",
        tenant_scope_id="team-1",
        service_tier="standard",
        item_ids=["item-1"],
        claimed_work_units=3,
        lease_expires_at=datetime.now(tz=UTC) + timedelta(seconds=60),
    )

    BatchExecutorWorker._record_shadow_fair_share_decision(
        recommendation=recommendation,
        claim=claim,
        model_group="model-a",
        service_tier="standard",
    )

    assert shadow_results == ["match", "job_mismatch"]
    assert observed_ratios == [1.0]
    records = [
        record
        for record in caplog.records
        if record.getMessage() == "batch scheduler shadow job decision"
    ]
    assert len(records) == 1
    record = records[0]
    digest = hashlib.sha256(b"team-1").hexdigest()[:12]
    assert record.shadow_tenant_scope_type == "team"
    assert record.shadow_tenant_scope_id == f"team:{digest}"
    assert record.shadow_tenant_scope_id != "team-1"
    assert record.shadow_recommended_batch_id == "b-recommended"
    assert record.shadow_actual_batch_id == "b-actual"
    assert record.shadow_recommended_size_class == "xs"
    assert record.shadow_recommended_scheduler_rank == 1.25
    assert record.shadow_recommended_age_credit_work_units == 2
    assert record.shadow_recommended_policy_reason == "aging_credit"
    assert record.shadow_result == "job_mismatch"
    assert record.shadow_flow_match is True
    assert (
        BatchExecutorWorker._shadow_tenant_scope_label(
            SimpleNamespace(tenant_scope_type="api_key", tenant_scope_id="secret")
        )
        == "api_key"
    )


def test_batch_worker_shadow_work_decision_logs_without_recommendation(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from src.batch import worker as worker_module

    caplog.set_level(logging.INFO, logger=worker_module.__name__)
    shadow_results: list[tuple[str, str, str]] = []
    shadow_comparisons: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        worker_module,
        "increment_batch_scheduler_shadow_decision",
        lambda *, model_group, service_tier, result: shadow_results.append(
            (model_group, service_tier, result)
        ),
    )
    monkeypatch.setattr(
        worker_module,
        "increment_batch_scheduler_shadow_comparison",
        lambda *, active_mode, shadow_mode, result: shadow_comparisons.append(
            (active_mode, shadow_mode, result)
        ),
    )
    claim = BatchWorkClaim(
        claim_id="claim-model",
        worker_id="w1",
        batch_id="b-actual",
        endpoint="/v1/embeddings",
        model_group="model-a",
        tenant_scope_type="team",
        tenant_scope_id="team-secret",
        service_tier="standard",
        item_ids=["item-1"],
        claimed_work_units=3,
        lease_expires_at=datetime.now(tz=UTC) + timedelta(seconds=60),
    )

    BatchExecutorWorker._record_shadow_work_decision(
        recommendation=None,
        claim=claim,
        active_mode="fifo_v1",
        shadow_mode="slice_v1",
    )

    assert shadow_results == [("model-a", "standard", "no_recommendation")]
    assert shadow_comparisons == [("fifo_v1", "slice_v1", "no_recommendation")]
    records = [
        record
        for record in caplog.records
        if record.getMessage() == "batch scheduler shadow work decision"
    ]
    assert len(records) == 1
    record = records[0]
    assert record.active_batch_id == "b-actual"
    assert record.shadow_batch_id == ""
    assert record.active_model_group == "model-a"
    assert record.shadow_model_group == ""
    assert record.active_tenant_scope_type == "team"
    assert record.shadow_tenant_scope_id == ""
    assert record.shadow_result == "no_recommendation"
    assert "team-secret" not in record.__dict__.values()


def test_batch_worker_shadow_work_decision_logs_both_empty(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from src.batch import worker as worker_module

    caplog.set_level(logging.INFO, logger=worker_module.__name__)
    shadow_results: list[str] = []
    shadow_comparisons: list[str] = []
    monkeypatch.setattr(
        worker_module,
        "increment_batch_scheduler_shadow_decision",
        lambda *, model_group, service_tier, result: shadow_results.append(result),
    )
    monkeypatch.setattr(
        worker_module,
        "increment_batch_scheduler_shadow_comparison",
        lambda *, active_mode, shadow_mode, result: shadow_comparisons.append(result),
    )

    BatchExecutorWorker._record_shadow_work_decision(
        recommendation=None,
        claim=None,
        active_mode="fifo_v1",
        shadow_mode="slice_v1",
    )

    assert shadow_results == ["both_empty"]
    assert shadow_comparisons == ["both_empty"]
    records = [
        record
        for record in caplog.records
        if record.getMessage() == "batch scheduler shadow work decision"
    ]
    assert len(records) == 1
    assert records[0].shadow_result == "both_empty"
    assert records[0].active_batch_id == ""
    assert records[0].shadow_batch_id == ""


@pytest.mark.parametrize(
    "recommendation_result",
    ["no_recommendation", "no_active_flow", "tenant_in_flight_full", "flow_scan_limit_reached"],
)
def test_batch_worker_shadow_fair_share_decision_logs_without_flow(
    recommendation_result: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from src.batch import worker as worker_module

    caplog.set_level(logging.INFO, logger=worker_module.__name__)
    shadow_results: list[tuple[str, str, str]] = []
    shadow_comparisons: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        worker_module,
        "increment_batch_scheduler_shadow_decision",
        lambda *, model_group, service_tier, result: shadow_results.append(
            (model_group, service_tier, result)
        ),
    )
    monkeypatch.setattr(
        worker_module,
        "increment_batch_scheduler_shadow_comparison",
        lambda *, active_mode, shadow_mode, result: shadow_comparisons.append(
            (active_mode, shadow_mode, result)
        ),
    )
    claim = BatchWorkClaim(
        claim_id="claim-model",
        worker_id="w1",
        batch_id="b-actual",
        endpoint="/v1/embeddings",
        model_group="model-a",
        tenant_scope_type="team",
        tenant_scope_id="team-secret",
        service_tier="standard",
        item_ids=["item-1"],
        claimed_work_units=3,
        lease_expires_at=datetime.now(tz=UTC) + timedelta(seconds=60),
    )

    BatchExecutorWorker._record_shadow_fair_share_decision(
        recommendation=SimpleNamespace(flow=None, result=recommendation_result),
        claim=claim,
        model_group="model-a",
        service_tier="standard",
        active_mode="model_capacity_v1",
        shadow_mode="fair_share_v1",
    )

    assert shadow_results == [("model-a", "standard", recommendation_result)]
    assert shadow_comparisons == [
        ("model_capacity_v1", "fair_share_v1", recommendation_result)
    ]
    records = [
        record
        for record in caplog.records
        if record.getMessage() == "batch scheduler shadow job decision"
    ]
    assert len(records) == 1
    record = records[0]
    assert record.active_batch_id == "b-actual"
    assert record.shadow_batch_id == ""
    assert record.active_model_group == "model-a"
    assert record.shadow_model_group == "model-a"
    assert record.shadow_service_tier == "standard"
    assert record.shadow_tenant_scope_id == ""
    assert record.shadow_result == recommendation_result
    assert record.shadow_flow_match is False
    assert "team-secret" not in record.__dict__.values()


@pytest.mark.asyncio
async def test_batch_worker_disabled_model_group_logs_fair_share_shadow_skip(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from src.batch import worker as worker_module

    now = datetime.now(tz=UTC)
    caplog.set_level(logging.INFO, logger=worker_module.__name__)
    shadow_results: list[tuple[str, str, str]] = []
    shadow_comparisons: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        worker_module,
        "increment_batch_scheduler_shadow_decision",
        lambda *, model_group, service_tier, result: shadow_results.append(
            (model_group, service_tier, result)
        ),
    )
    monkeypatch.setattr(
        worker_module,
        "increment_batch_scheduler_shadow_comparison",
        lambda *, active_mode, shadow_mode, result: shadow_comparisons.append(
            (active_mode, shadow_mode, result)
        ),
    )

    class _DisabledShadowRepo:
        def __init__(self) -> None:
            self.claim_next_work_calls: list[dict] = []

        async def claim_next_fair_share_work(self, **kwargs):  # pragma: no cover
            del kwargs
            raise AssertionError("disabled fair-share model group should not claim fair-share")

        async def recommend_next_fair_share_flow(self, **kwargs):  # pragma: no cover
            del kwargs
            raise AssertionError("disabled fair-share model group should not shadow fair-share")

        async def claim_next_work(self, **kwargs):
            self.claim_next_work_calls.append(kwargs)
            return BatchWorkClaim(
                claim_id="claim-model",
                worker_id="w1",
                batch_id="b-model",
                endpoint="/v1/embeddings",
                model_group="model-a",
                tenant_scope_type="team",
                tenant_scope_id="team-secret",
                service_tier="standard",
                item_ids=["item-1"],
                claimed_work_units=3,
                lease_expires_at=now + timedelta(seconds=60),
            )

    class _CapacityResolver:
        def __init__(self) -> None:
            self.results: list[tuple[str, str]] = []

        async def select_model_groups(self, **kwargs):
            assert kwargs == {"max_items": 4, "max_work_units": 9}
            return [
                SimpleNamespace(
                    snapshot=SimpleNamespace(
                        model_group="model-a",
                        service_tier="standard",
                        in_flight_items=0,
                        available_in_flight_items=4,
                        in_flight_work_units=0,
                        tpm_remaining=16,
                    ),
                    max_items=4,
                    max_work_units=9,
                )
            ]

        def record_selection(self, snapshot):
            assert snapshot.model_group == "model-a"

        def record_claim_result(self, *, model_group: str, result: str) -> None:
            self.results.append((model_group, result))

    repo = _DisabledShadowRepo()
    resolver = _CapacityResolver()
    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(
            worker_id="w1",
            scheduler_claim_mode="work_slice",
            scheduler_shadow_mode="fair_share_v1",
            work_claim_max_items=4,
            work_claim_max_work_units=9,
            model_capacity_enabled=True,
            tenant_fair_share_disabled_model_groups=("model-a",),
        ),
        model_capacity_resolver=resolver,
    )

    claim = await worker._claim_next_work_slice()

    assert claim is not None
    assert repo.claim_next_work_calls == [
        {
            "worker_id": "w1",
            "max_items": 4,
            "max_work_units": 9,
            "lease_seconds": 360,
            "allowed_model_groups": ["model-a"],
            "service_tier": "standard",
            "claim_order": "fifo",
            "capacity_model_group": "model-a",
            "capacity_service_tier": "standard",
            "capacity_max_in_flight_items": 4,
            "capacity_max_in_flight_work_units": 16,
            "allow_oversized_first_item": False,
            "scheduler_mode": "model_capacity_v1",
        }
    ]
    assert resolver.results == [("model-a", "claimed")]
    assert shadow_results == [("model-a", "standard", "fair_share_disabled")]
    assert shadow_comparisons == [
        ("model_capacity_v1", "fair_share_v1", "fair_share_disabled")
    ]
    records = [
        record
        for record in caplog.records
        if record.getMessage() == "batch scheduler shadow job decision"
    ]
    assert len(records) == 1
    record = records[0]
    assert record.active_batch_id == "b-model"
    assert record.shadow_batch_id == ""
    assert record.active_model_group == "model-a"
    assert record.shadow_model_group == "model-a"
    assert record.shadow_service_tier == "standard"
    assert record.shadow_tenant_scope_id == ""
    assert record.shadow_result == "fair_share_disabled"
    assert record.shadow_flow_match is False
    assert "team-secret" not in record.__dict__.values()


@pytest.mark.asyncio
async def test_batch_worker_disabled_fair_share_model_group_uses_model_capacity() -> None:
    now = datetime.now(tz=UTC)

    class _DisabledRepo:
        def __init__(self) -> None:
            self.claim_next_work_calls: list[dict] = []

        async def claim_next_fair_share_work(self, **kwargs):  # pragma: no cover
            del kwargs
            raise AssertionError("disabled fair-share model group should not use fair-share")

        async def recommend_next_fair_share_flow(self, **kwargs):  # pragma: no cover
            del kwargs
            raise AssertionError("disabled fair-share model group should not shadow fair-share")

        async def claim_next_work(self, **kwargs):
            self.claim_next_work_calls.append(kwargs)
            return BatchWorkClaim(
                claim_id="claim-model",
                worker_id="w1",
                batch_id="b-model",
                endpoint="/v1/embeddings",
                model_group="model-a",
                tenant_scope_type="team",
                tenant_scope_id="team-1",
                service_tier="standard",
                item_ids=["item-1"],
                claimed_work_units=3,
                lease_expires_at=now + timedelta(seconds=60),
            )

    class _CapacityResolver:
        def __init__(self) -> None:
            self.results: list[tuple[str, str]] = []

        async def select_model_groups(self, **kwargs):
            assert kwargs == {"max_items": 4, "max_work_units": 9}
            return [
                SimpleNamespace(
                    snapshot=SimpleNamespace(
                        model_group="model-a",
                        service_tier="standard",
                        in_flight_items=0,
                        available_in_flight_items=4,
                        in_flight_work_units=0,
                        tpm_remaining=16,
                    ),
                    max_items=4,
                    max_work_units=9,
                )
            ]

        def record_selection(self, snapshot):
            assert snapshot.model_group == "model-a"

        def record_claim_result(self, *, model_group: str, result: str) -> None:
            self.results.append((model_group, result))

    repo = _DisabledRepo()
    resolver = _CapacityResolver()
    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(
            worker_id="w1",
            scheduler_claim_mode="work_slice",
            work_claim_max_items=4,
            work_claim_max_work_units=9,
            model_capacity_enabled=True,
            scheduler_shadow_enabled=True,
            tenant_fair_share_enabled=True,
            tenant_fair_share_disabled_model_groups=("model-a",),
        ),
        model_capacity_resolver=resolver,
    )

    claim = await worker._claim_next_work_slice()

    assert claim is not None
    assert claim.batch_id == "b-model"
    assert len(repo.claim_next_work_calls) == 1
    assert resolver.results == [("model-a", "claimed")]


@pytest.mark.parametrize("fair_share_result", ["no_active_flow", "transaction_unavailable"])
@pytest.mark.asyncio
async def test_batch_worker_fair_share_falls_back_to_model_capacity(
    fair_share_result: str,
) -> None:
    now = datetime.now(tz=UTC)

    class _FallbackRepo:
        def __init__(self) -> None:
            self.fair_share_calls: list[dict] = []
            self.claim_next_work_calls: list[dict] = []

        async def claim_next_fair_share_work(self, **kwargs):
            self.fair_share_calls.append(kwargs)
            return SimpleNamespace(result=fair_share_result, claim=None)

        async def claim_next_work(self, **kwargs):
            self.claim_next_work_calls.append(kwargs)
            return BatchWorkClaim(
                claim_id="claim-model",
                worker_id="w1",
                batch_id="b-model",
                endpoint="/v1/embeddings",
                model_group="model-a",
                tenant_scope_type="team",
                tenant_scope_id="team-1",
                service_tier="standard",
                item_ids=["item-1"],
                claimed_work_units=3,
                lease_expires_at=now + timedelta(seconds=60),
            )

    class _CapacityResolver:
        def __init__(self) -> None:
            self.results: list[tuple[str, str]] = []

        async def select_model_groups(self, **kwargs):
            assert kwargs == {"max_items": 4, "max_work_units": 9}
            return [
                SimpleNamespace(
                    snapshot=SimpleNamespace(
                        model_group="model-a",
                        service_tier="standard",
                        in_flight_items=2,
                        available_in_flight_items=5,
                        in_flight_work_units=4,
                        tpm_remaining=20,
                    ),
                    max_items=4,
                    max_work_units=9,
                )
            ]

        def record_selection(self, snapshot):
            assert snapshot.model_group == "model-a"

        def record_claim_result(self, *, model_group: str, result: str) -> None:
            self.results.append((model_group, result))

    repo = _FallbackRepo()
    resolver = _CapacityResolver()
    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(
            worker_id="w1",
            scheduler_claim_mode="work_slice",
            work_claim_max_items=4,
            work_claim_max_work_units=9,
            model_capacity_enabled=True,
            tenant_fair_share_enabled=True,
            tenant_fair_share_base_quantum_work_units=16,
            tenant_fair_share_max_deficit_multiplier=8,
            tenant_max_in_flight_work_units=32,
        ),
        model_capacity_resolver=resolver,
    )

    claim = await worker._claim_next_work_slice()

    assert claim is not None
    assert claim.batch_id == "b-model"
    assert len(repo.fair_share_calls) == 1
    assert repo.claim_next_work_calls == [
        {
            "worker_id": "w1",
            "max_items": 4,
            "max_work_units": 9,
            "lease_seconds": 360,
            "allowed_model_groups": ["model-a"],
            "service_tier": "standard",
            "claim_order": "fifo",
            "capacity_model_group": "model-a",
            "capacity_service_tier": "standard",
            "capacity_max_in_flight_items": 7,
            "capacity_max_in_flight_work_units": 24,
            "allow_oversized_first_item": False,
            "scheduler_mode": "fair_share_v1",
        }
    ]
    assert resolver.results == [("model-a", "claimed")]


@pytest.mark.parametrize(
    "fair_share_result",
    ["empty_flow", "flow_scan_limit_reached", "lock_busy", "tenant_in_flight_full"],
)
@pytest.mark.asyncio
async def test_batch_worker_fair_share_non_fallback_result_does_not_model_claim_same_group(
    fair_share_result: str,
) -> None:
    now = datetime.now(tz=UTC)

    class _LockBusyRepo:
        def __init__(self) -> None:
            self.fair_share_calls: list[dict] = []
            self.claim_next_work_calls: list[dict] = []

        async def claim_next_fair_share_work(self, **kwargs):
            self.fair_share_calls.append(kwargs)
            return SimpleNamespace(result=fair_share_result, claim=None)

        async def claim_next_work(self, **kwargs):
            self.claim_next_work_calls.append(kwargs)
            assert kwargs.get("legacy_only") is True
            return BatchWorkClaim(
                claim_id="claim-legacy",
                worker_id="w1",
                batch_id="b-legacy",
                endpoint="/v1/embeddings",
                model_group=None,
                tenant_scope_type=None,
                tenant_scope_id=None,
                service_tier="standard",
                item_ids=["item-1"],
                claimed_work_units=1,
                lease_expires_at=now + timedelta(seconds=60),
            )

    class _CapacityResolver:
        def __init__(self) -> None:
            self.results: list[tuple[str, str]] = []

        async def select_model_groups(self, **kwargs):
            assert kwargs == {"max_items": 4, "max_work_units": 9}
            return [
                SimpleNamespace(
                    snapshot=SimpleNamespace(
                        model_group="model-a",
                        service_tier="standard",
                        in_flight_items=2,
                        available_in_flight_items=5,
                        in_flight_work_units=4,
                        tpm_remaining=20,
                    ),
                    max_items=4,
                    max_work_units=9,
                )
            ]

        def record_selection(self, snapshot):
            assert snapshot.model_group == "model-a"

        def record_claim_result(self, *, model_group: str, result: str) -> None:
            self.results.append((model_group, result))

    repo = _LockBusyRepo()
    resolver = _CapacityResolver()
    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(
            worker_id="w1",
            scheduler_claim_mode="work_slice",
            work_claim_max_items=4,
            work_claim_max_work_units=9,
            model_capacity_enabled=True,
            tenant_fair_share_enabled=True,
        ),
        model_capacity_resolver=resolver,
    )

    claim = await worker._claim_next_work_slice()

    assert claim is not None
    assert claim.batch_id == "b-legacy"
    assert len(repo.fair_share_calls) == 1
    assert repo.claim_next_work_calls == [
        {
            "worker_id": "w1",
            "max_items": 4,
            "max_work_units": 9,
            "lease_seconds": 360,
            "legacy_only": True,
            "claim_order": "fifo",
            "scheduler_mode": "fair_share_v1",
        }
    ]
    assert resolver.results == [("model-a", fair_share_result), ("legacy", "claimed")]


@pytest.mark.asyncio
async def test_batch_worker_work_slice_finalization_first_uses_job_lease():
    class _FinalizationRepo:
        def __init__(self) -> None:
            self.claim_next_work_called = False
            self.released = False

        async def claim_next_finalization(self, **kwargs):
            assert kwargs == {"worker_id": "w1", "lease_seconds": 120}
            return _work_slice_job(status=BatchJobStatus.FINALIZING)

        async def claim_next_work(self, **kwargs):  # pragma: no cover
            del kwargs
            self.claim_next_work_called = True
            raise AssertionError("work claim should not run before prioritized finalization")

        async def renew_job_lease(self, **kwargs) -> bool:
            del kwargs
            return True

        async def release_job_lease(self, *, batch_id: str, worker_id: str) -> None:
            assert batch_id == "b-work"
            assert worker_id == "w1"
            self.released = True

        async def summarize_runtime_statuses(self, *, now):  # noqa: ANN001
            del now
            return {"queued": 0, "in_progress": 0, "finalizing": 1}

    repo = _FinalizationRepo()
    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1", scheduler_claim_mode="work_slice"),
    )
    finalized: list[str] = []

    async def _finalize(job):  # noqa: ANN001
        finalized.append(job.batch_id)

    worker._finalize_artifacts = _finalize  # type: ignore[assignment]

    did_work = await worker.process_once()

    assert did_work is True
    assert finalized == ["b-work"]
    assert repo.claim_next_work_called is False
    assert repo.released is True


@pytest.mark.asyncio
async def test_batch_worker_work_slice_zeros_saturation_after_success(monkeypatch):
    now = datetime.now(tz=UTC)

    class _SaturationRepo:
        async def claim_next_finalization(self, **kwargs):
            del kwargs
            return None

        async def claim_next_work(self, **kwargs):
            del kwargs
            return BatchWorkClaim(
                claim_id="claim-sat",
                worker_id="w1",
                batch_id="b-work",
                endpoint="/v1/embeddings",
                model_group="m-1",
                tenant_scope_type="api_key",
                tenant_scope_id="tok-1",
                service_tier="standard",
                item_ids=["item-1"],
                claimed_work_units=1,
                lease_expires_at=now + timedelta(seconds=60),
            )

        async def get_job(self, batch_id: str):
            assert batch_id == "b-work"
            return _work_slice_job()

        async def load_claim_items(self, item_ids: list[str]):
            return [_work_slice_item()]

        async def release_claim_items(self, **kwargs) -> int:
            del kwargs
            return 0

        async def refresh_jobs_after_claim(self, batch_ids: list[str]):
            del batch_ids
            return []

        async def summarize_runtime_statuses(self, *, now):  # noqa: ANN001
            del now
            return {"queued": 0, "in_progress": 1, "finalizing": 0}

    saturation_calls: list[dict] = []
    monkeypatch.setattr(
        "src.batch.worker.set_batch_worker_saturation",
        lambda **kwargs: saturation_calls.append(kwargs),
    )

    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=_SaturationRepo(),  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1", scheduler_claim_mode="work_slice"),
    )

    async def _process_items(job, items):  # noqa: ANN001
        del job, items

    worker._process_items = _process_items  # type: ignore[assignment]

    did_work = await worker.process_once()

    assert did_work is True
    # Guards the regression from the first review: the success path used to
    # skip the saturation reset entirely. With zero calls, indexing would
    # IndexError; with the wrong active value, the equality fails.
    assert saturation_calls, "set_batch_worker_saturation should be called on success path"
    assert saturation_calls[-1]["active"] == 0
    assert saturation_calls[-1]["worker_id"] == "w1"


@pytest.mark.asyncio
async def test_batch_worker_work_slice_cancels_pending_items_when_cancel_requested():
    now = datetime.now(tz=UTC)

    class _CancelRepo:
        def __init__(self) -> None:
            self.release_calls: list[dict] = []
            self.mark_cancelled_called_with: str | None = None

        async def claim_next_finalization(self, **kwargs):
            del kwargs
            return None

        async def claim_next_work(self, **kwargs):
            del kwargs
            return BatchWorkClaim(
                claim_id="claim-cancel",
                worker_id="w1",
                batch_id="b-cancel",
                endpoint="/v1/embeddings",
                model_group="m-1",
                tenant_scope_type="api_key",
                tenant_scope_id="tok-1",
                service_tier="standard",
                item_ids=["item-1", "item-2"],
                claimed_work_units=2,
                lease_expires_at=now + timedelta(seconds=60),
            )

        async def get_job(self, batch_id: str):
            assert batch_id == "b-cancel"
            job = _work_slice_job(batch_id="b-cancel")
            job.cancel_requested_at = now
            return job

        async def load_claim_items(self, item_ids):  # pragma: no cover
            del item_ids
            raise AssertionError("load_claim_items must not run on cancel path")

        async def release_claim_items(self, **kwargs) -> int:
            self.release_calls.append(kwargs)
            return len(kwargs["item_ids"])

        async def mark_pending_items_cancelled(self, batch_id: str) -> int:
            self.mark_cancelled_called_with = batch_id
            return 2

        async def refresh_jobs_after_claim(self, batch_ids):
            del batch_ids
            return []

        async def summarize_runtime_statuses(self, *, now):  # noqa: ANN001
            del now
            return {"queued": 0, "in_progress": 0, "finalizing": 0}

    repo = _CancelRepo()
    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=repo,  # type: ignore[arg-type]
        storage=_FakeStorage(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1", scheduler_claim_mode="work_slice"),
    )

    did_work = await worker.process_once()

    assert did_work is True
    # Cancel branch released items exactly once; the finally must NOT release
    # them again now that needs_finally_release short-circuits the second call.
    assert len(repo.release_calls) == 1
    assert repo.release_calls[0]["item_ids"] == ["item-1", "item-2"]
    assert repo.release_calls[0]["worker_id"] == "w1"
    assert repo.mark_cancelled_called_with == "b-cancel"
