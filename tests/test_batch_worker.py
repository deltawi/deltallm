from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.batch.models import BatchItemRecord, BatchJobRecord, BatchJobStatus
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
async def test_batch_worker_retries_finalizing_job_after_storage_failure():
    now = datetime.now(tz=UTC)

    class _FinalizingRepo:
        def __init__(self) -> None:
            self.claim_count = 0
            self.released = 0
            self.deleted_files: list[str] = []
            self.finalized = False
            self.created_files = 0
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

        async def write_lines(self, *, purpose: str, filename: str, lines):  # noqa: ANN001
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

    did_work = await worker.process_once()

    assert did_work is True
    assert storage.deleted == ["batch_output/1.jsonl"]
    assert repo.deleted_files == ["file-1"]
    assert repo.released == 1
    assert repo.finalized is False
    assert repo.rescheduled == [60]

    did_work = await worker.process_once()

    assert did_work is True
    assert repo.finalized is True
    assert repo.released == 2


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
