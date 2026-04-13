from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.bootstrap import BootstrapStatus
from src.bootstrap.audit import init_audit_runtime, shutdown_audit_runtime
from src.bootstrap.batch import _drain_worker_task, init_batch_runtime, shutdown_batch_runtime


def _audit_config(*, enabled: bool, retention_enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(
        general_settings=SimpleNamespace(
            audit_enabled=enabled,
            audit_retention_worker_enabled=retention_enabled,
            audit_retention_interval_seconds=60,
            audit_retention_scan_limit=100,
            audit_metadata_retention_days=30,
            audit_payload_retention_days=7,
        )
    )


def _batch_config(
    *,
    enabled: bool,
    worker_enabled: bool,
    gc_enabled: bool,
    create_sessions_enabled: bool = False,
    create_session_cleanup_enabled: bool = False,
    storage_backend: str = "local",
    s3_bucket: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        general_settings=SimpleNamespace(
            embeddings_batch_enabled=enabled,
            embeddings_batch_worker_enabled=worker_enabled,
            embeddings_batch_gc_enabled=gc_enabled,
            embeddings_batch_storage_backend=storage_backend,
            embeddings_batch_storage_dir="/tmp/batch-artifacts",
            embeddings_batch_s3_bucket=s3_bucket,
            embeddings_batch_s3_region="us-east-1",
            embeddings_batch_s3_prefix="deltallm/batch-artifacts",
            embeddings_batch_s3_endpoint_url=None,
            embeddings_batch_s3_access_key_id=None,
            embeddings_batch_s3_secret_access_key=None,
            embeddings_batch_s3_spool_max_bytes=8_388_608,
            batch_metadata_retention_days=14,
            embeddings_batch_poll_interval_seconds=5,
            embeddings_batch_heartbeat_interval_seconds=15,
            embeddings_batch_job_lease_seconds=120,
            embeddings_batch_item_lease_seconds=360,
            embeddings_batch_finalization_retry_delay_seconds=60,
            embeddings_batch_worker_concurrency=4,
            embeddings_batch_item_buffer_multiplier=2,
            embeddings_batch_storage_chunk_size=65_536,
            embeddings_batch_finalization_page_size=500,
            embeddings_batch_create_buffer_size=200,
            embeddings_batch_create_sessions_enabled=create_sessions_enabled,
            embeddings_batch_create_session_cleanup_enabled=create_session_cleanup_enabled,
            embeddings_batch_create_session_cleanup_interval_seconds=300,
            embeddings_batch_create_session_cleanup_scan_limit=25,
            embeddings_batch_create_stage_orphan_grace_seconds=1800,
            embeddings_batch_create_session_completed_retention_seconds=604_800,
            embeddings_batch_create_session_retryable_retention_seconds=259_200,
            embeddings_batch_create_session_failed_retention_seconds=1_209_600,
            embeddings_batch_create_soft_precheck_enabled=False,
            embeddings_batch_create_idempotency_enabled=False,
            embeddings_batch_create_promotion_insert_chunk_size=500,
            embeddings_batch_max_file_bytes=52_428_800,
            embeddings_batch_max_items_per_batch=10_000,
            embeddings_batch_max_line_bytes=1_048_576,
            embeddings_batch_max_pending_batches_per_scope=20,
            embeddings_batch_item_claim_limit=10,
            embeddings_batch_max_attempts=3,
            batch_completed_artifact_retention_days=7,
            batch_failed_artifact_retention_days=2,
            embeddings_batch_gc_interval_seconds=60,
            embeddings_batch_gc_scan_limit=100,
        )
    )


@pytest.mark.asyncio
async def test_init_audit_runtime_disabled_leaves_state_empty() -> None:
    app = SimpleNamespace(state=SimpleNamespace(prisma_manager=SimpleNamespace(client=object())))

    runtime = await init_audit_runtime(app, _audit_config(enabled=False, retention_enabled=False))

    assert app.state.audit_repository is None
    assert app.state.audit_service is None
    assert runtime.retention_worker is None
    assert runtime.retention_task is None
    assert runtime.statuses == (BootstrapStatus("audit", "disabled"),)


@pytest.mark.asyncio
async def test_init_and_shutdown_audit_runtime_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    created: dict[str, object] = {}

    class FakeAuditService:
        def __init__(self, repository) -> None:  # noqa: ANN001
            self.repository = repository
            self.started = False
            self.stopped = False
            created["service"] = self

        async def start(self) -> None:
            self.started = True

        async def shutdown(self) -> None:
            self.stopped = True

    class FakeAuditWorker:
        def __init__(self, repository, config) -> None:  # noqa: ANN001
            self.repository = repository
            self.config = config
            self.stopped = False
            created["worker"] = self

        async def run(self) -> None:
            await asyncio.sleep(3600)

        def stop(self) -> None:
            self.stopped = True

    monkeypatch.setattr("src.bootstrap.audit.AuditRepository", lambda client: {"client": client})
    monkeypatch.setattr("src.bootstrap.audit.AuditService", FakeAuditService)
    monkeypatch.setattr("src.bootstrap.audit.AuditRetentionWorker", FakeAuditWorker)

    app = SimpleNamespace(state=SimpleNamespace(prisma_manager=SimpleNamespace(client="db-client")))

    runtime = await init_audit_runtime(app, _audit_config(enabled=True, retention_enabled=True))

    assert app.state.audit_repository == {"client": "db-client"}
    assert isinstance(app.state.audit_service, FakeAuditService)
    assert created["service"].started is True
    assert runtime.retention_worker is created["worker"]
    assert runtime.retention_task is not None
    assert runtime.statuses == (
        BootstrapStatus("audit", "ready"),
        BootstrapStatus("audit_retention_worker", "ready"),
    )

    await shutdown_audit_runtime(app, runtime)

    assert created["worker"].stopped is True
    assert created["service"].stopped is True


@pytest.mark.asyncio
async def test_init_batch_runtime_disabled_sets_batch_state_to_none() -> None:
    app = SimpleNamespace(state=SimpleNamespace())

    runtime = await init_batch_runtime(app, _batch_config(enabled=False, worker_enabled=False, gc_enabled=False), repository=object())

    assert app.state.batch_storage is None
    assert app.state.batch_storage_registry is None
    assert app.state.batch_service is None
    assert app.state.batch_create_session_repository is None
    assert app.state.batch_create_staging_backend is None
    assert app.state.batch_create_session_cleanup_worker is None
    assert runtime.worker is None
    assert runtime.gc_worker is None
    assert runtime.statuses == (BootstrapStatus("embeddings_batch", "disabled"),)


@pytest.mark.asyncio
async def test_init_and_shutdown_batch_runtime_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    created: dict[str, object] = {}

    class FakeBatchService:
        def __init__(
            self,
            repository,  # noqa: ANN001
            storage,  # noqa: ANN001
            metadata_retention_days,  # noqa: ANN001
            storage_registry=None,  # noqa: ANN001
            storage_chunk_size=65_536,  # noqa: ANN001
            create_buffer_size=200,  # noqa: ANN001
            max_file_bytes=52_428_800,  # noqa: ANN001
            max_items_per_batch=10_000,  # noqa: ANN001
            max_line_bytes=1_048_576,  # noqa: ANN001
            max_pending_batches_per_scope=20,  # noqa: ANN001
            callable_target_grant_service=None,  # noqa: ANN001
            callable_target_scope_policy_mode="enforce",  # noqa: ANN001
        ) -> None:
            self.repository = repository
            self.storage = storage
            self.storage_registry = storage_registry
            self.metadata_retention_days = metadata_retention_days
            self.storage_chunk_size = storage_chunk_size
            self.create_buffer_size = create_buffer_size
            self.max_file_bytes = max_file_bytes
            self.max_items_per_batch = max_items_per_batch
            self.max_line_bytes = max_line_bytes
            self.max_pending_batches_per_scope = max_pending_batches_per_scope
            self.callable_target_grant_service = callable_target_grant_service
            self.callable_target_scope_policy_mode = callable_target_scope_policy_mode
            created["service"] = self

    class FakeBatchWorker:
        def __init__(self, app, repository, storage, config) -> None:  # noqa: ANN001
            self.app = app
            self.repository = repository
            self.storage = storage
            self.config = config
            self.stopped = False
            created["worker"] = self

        async def run(self) -> None:
            while not self.stopped:
                await asyncio.sleep(0.01)

        def stop(self) -> None:
            self.stopped = True

    class FakeGCWorker:
        def __init__(self, repository, storage, storage_registry=None, config=None) -> None:  # noqa: ANN001
            self.repository = repository
            self.storage = storage
            self.storage_registry = storage_registry
            self.config = config
            self.stopped = False
            created["gc_worker"] = self

        async def run(self) -> None:
            while not self.stopped:
                await asyncio.sleep(0.01)

        def stop(self) -> None:
            self.stopped = True

    class FakeCompletionOutboxWorker:
        def __init__(self, app, repository, config) -> None:  # noqa: ANN001
            self.app = app
            self.repository = repository
            self.config = config
            self.stopped = False
            created["completion_outbox_worker"] = self

        async def run(self) -> None:
            while not self.stopped:
                await asyncio.sleep(0.01)

        def stop(self) -> None:
            self.stopped = True

    monkeypatch.setattr("src.bootstrap.batch.LocalBatchArtifactStorage", lambda path: {"path": path})
    monkeypatch.setattr("src.bootstrap.batch.BatchService", FakeBatchService)
    monkeypatch.setattr("src.bootstrap.batch.BatchExecutorWorker", FakeBatchWorker)
    monkeypatch.setattr("src.bootstrap.batch.BatchCompletionOutboxWorker", FakeCompletionOutboxWorker)
    monkeypatch.setattr("src.bootstrap.batch.BatchRetentionCleanupWorker", FakeGCWorker)

    app = SimpleNamespace(state=SimpleNamespace())
    repository = SimpleNamespace(create_sessions="create-session-repo")

    runtime = await init_batch_runtime(
        app,
        _batch_config(enabled=True, worker_enabled=True, gc_enabled=True),
        repository=repository,
    )

    assert app.state.batch_storage == {"path": "/tmp/batch-artifacts"}
    assert app.state.batch_storage_registry == {"local": {"path": "/tmp/batch-artifacts"}}
    assert app.state.batch_create_session_repository == "create-session-repo"
    assert app.state.batch_create_staging_backend is None
    assert app.state.batch_create_session_cleanup_worker is None
    assert isinstance(app.state.batch_service, FakeBatchService)
    assert created["service"].repository is repository
    assert created["service"].storage_registry == {"local": {"path": "/tmp/batch-artifacts"}}
    assert created["service"].storage_chunk_size == 65_536
    assert created["service"].create_buffer_size == 200
    assert runtime.worker is created["worker"]
    assert runtime.worker_task is not None
    assert runtime.completion_outbox_worker is created["completion_outbox_worker"]
    assert runtime.completion_outbox_task is not None
    assert runtime.gc_worker is created["gc_worker"]
    assert runtime.gc_task is not None
    assert runtime.create_session_staging_backend is None
    assert runtime.create_session_cleanup_worker is None
    assert runtime.create_session_cleanup_task is None
    assert created["gc_worker"].storage_registry == {"local": {"path": "/tmp/batch-artifacts"}}
    assert created["worker"].config.worker_concurrency == 4
    assert created["worker"].config.item_buffer_multiplier == 2
    assert created["worker"].config.finalization_page_size == 500
    assert runtime.statuses == (
        BootstrapStatus("embeddings_batch", "ready"),
        BootstrapStatus("embeddings_batch_worker", "ready"),
        BootstrapStatus("embeddings_batch_completion_outbox", "ready"),
        BootstrapStatus("embeddings_batch_gc", "ready"),
        BootstrapStatus("embeddings_batch_create_session_cleanup", "disabled"),
    )

    await shutdown_batch_runtime(runtime)

    assert created["worker"].stopped is True
    assert created["completion_outbox_worker"].stopped is True
    assert created["gc_worker"].stopped is True


@pytest.mark.asyncio
async def test_init_and_shutdown_batch_runtime_with_create_session_cleanup_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[str, object] = {}

    class FakeBatchService:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            del args, kwargs

    class FakeCompletionOutboxWorker:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            self.stopped = False

        async def run(self) -> None:
            while not self.stopped:
                await asyncio.sleep(0.01)

        def stop(self) -> None:
            self.stopped = True

    class FakeStagingBackend:
        def __init__(self, *, storage, storage_registry=None, stage_purpose="batch-create-stage", chunk_size=65_536, max_line_bytes=1_048_576) -> None:  # noqa: ANN001,E501
            self.storage = storage
            self.storage_registry = storage_registry
            self.stage_purpose = stage_purpose
            self.chunk_size = chunk_size
            self.max_line_bytes = max_line_bytes
            created["staging_backend"] = self

    class FakeCreateSessionCleanupWorker:
        def __init__(self, *, repository, staging, config) -> None:  # noqa: ANN001
            self.repository = repository
            self.staging = staging
            self.config = config
            self.stopped = False
            created["cleanup_worker"] = self

        async def run(self) -> None:
            while not self.stopped:
                await asyncio.sleep(0.01)

        def stop(self) -> None:
            self.stopped = True

    monkeypatch.setattr("src.bootstrap.batch.LocalBatchArtifactStorage", lambda path: {"path": path})
    monkeypatch.setattr("src.bootstrap.batch.BatchService", FakeBatchService)
    monkeypatch.setattr("src.bootstrap.batch.BatchCompletionOutboxWorker", FakeCompletionOutboxWorker)
    monkeypatch.setattr("src.bootstrap.batch.BatchCreateArtifactStorageBackend", FakeStagingBackend)
    monkeypatch.setattr("src.bootstrap.batch.BatchCreateSessionCleanupWorker", FakeCreateSessionCleanupWorker)

    app = SimpleNamespace(state=SimpleNamespace())
    repository = SimpleNamespace(create_sessions="create-session-repo")

    runtime = await init_batch_runtime(
        app,
        _batch_config(
            enabled=True,
            worker_enabled=False,
            gc_enabled=False,
            create_sessions_enabled=True,
            create_session_cleanup_enabled=True,
        ),
        repository=repository,
    )

    assert app.state.batch_create_session_repository == "create-session-repo"
    assert app.state.batch_create_staging_backend is created["staging_backend"]
    assert app.state.batch_create_session_cleanup_worker is created["cleanup_worker"]
    assert runtime.create_session_staging_backend is created["staging_backend"]
    assert runtime.create_session_cleanup_worker is created["cleanup_worker"]
    assert runtime.create_session_cleanup_task is not None
    assert created["staging_backend"].storage == {"path": "/tmp/batch-artifacts"}
    assert created["staging_backend"].storage_registry == {"local": {"path": "/tmp/batch-artifacts"}}
    assert created["staging_backend"].chunk_size == 65_536
    assert created["staging_backend"].max_line_bytes == 1_048_576
    assert created["cleanup_worker"].repository == "create-session-repo"
    assert created["cleanup_worker"].staging is created["staging_backend"]
    assert created["cleanup_worker"].config.interval_seconds == 300
    assert created["cleanup_worker"].config.scan_limit == 25
    assert created["cleanup_worker"].config.orphan_grace_seconds == 1800
    assert runtime.statuses == (
        BootstrapStatus("embeddings_batch", "ready"),
        BootstrapStatus("embeddings_batch_worker", "disabled"),
        BootstrapStatus("embeddings_batch_completion_outbox", "ready"),
        BootstrapStatus("embeddings_batch_gc", "disabled"),
        BootstrapStatus("embeddings_batch_create_session_cleanup", "ready"),
    )

    await shutdown_batch_runtime(runtime)

    assert created["cleanup_worker"].stopped is True


@pytest.mark.asyncio
async def test_init_batch_runtime_with_create_sessions_enabled_builds_staging_backend_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[str, object] = {}

    class FakeBatchService:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            del args, kwargs

    class FakeCompletionOutboxWorker:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            self.stopped = False

        async def run(self) -> None:
            while not self.stopped:
                await asyncio.sleep(0.01)

        def stop(self) -> None:
            self.stopped = True

    class FakeStagingBackend:
        def __init__(self, *, storage, storage_registry=None, stage_purpose="batch-create-stage", chunk_size=65_536, max_line_bytes=1_048_576) -> None:  # noqa: ANN001,E501
            self.storage = storage
            self.storage_registry = storage_registry
            self.stage_purpose = stage_purpose
            self.chunk_size = chunk_size
            self.max_line_bytes = max_line_bytes
            created["staging_backend"] = self

    monkeypatch.setattr("src.bootstrap.batch.LocalBatchArtifactStorage", lambda path: {"path": path})
    monkeypatch.setattr("src.bootstrap.batch.BatchService", FakeBatchService)
    monkeypatch.setattr("src.bootstrap.batch.BatchCompletionOutboxWorker", FakeCompletionOutboxWorker)
    monkeypatch.setattr("src.bootstrap.batch.BatchCreateArtifactStorageBackend", FakeStagingBackend)

    app = SimpleNamespace(state=SimpleNamespace())
    repository = SimpleNamespace(create_sessions="create-session-repo")

    runtime = await init_batch_runtime(
        app,
        _batch_config(
            enabled=True,
            worker_enabled=False,
            gc_enabled=False,
            create_sessions_enabled=True,
            create_session_cleanup_enabled=False,
        ),
        repository=repository,
    )

    assert app.state.batch_create_session_repository == "create-session-repo"
    assert app.state.batch_create_staging_backend is created["staging_backend"]
    assert app.state.batch_create_session_cleanup_worker is None
    assert runtime.create_session_staging_backend is created["staging_backend"]
    assert runtime.create_session_cleanup_worker is None
    assert runtime.create_session_cleanup_task is None
    assert created["staging_backend"].storage == {"path": "/tmp/batch-artifacts"}
    assert created["staging_backend"].storage_registry == {"local": {"path": "/tmp/batch-artifacts"}}
    assert created["staging_backend"].chunk_size == 65_536
    assert created["staging_backend"].max_line_bytes == 1_048_576
    assert runtime.statuses == (
        BootstrapStatus("embeddings_batch", "ready"),
        BootstrapStatus("embeddings_batch_worker", "disabled"),
        BootstrapStatus("embeddings_batch_completion_outbox", "ready"),
        BootstrapStatus("embeddings_batch_gc", "disabled"),
        BootstrapStatus("embeddings_batch_create_session_cleanup", "disabled"),
    )

    await shutdown_batch_runtime(runtime)


@pytest.mark.asyncio
async def test_init_batch_runtime_selects_s3_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    created: dict[str, object] = {}

    class FakeBatchService:
        def __init__(
            self,
            repository,
            storage,
            metadata_retention_days=30,
            storage_registry=None,
            storage_chunk_size=65_536,
            create_buffer_size=200,
            max_file_bytes=52_428_800,
            max_items_per_batch=10_000,
            max_line_bytes=1_048_576,
            max_pending_batches_per_scope=20,
            callable_target_grant_service=None,
            callable_target_scope_policy_mode="enforce",
        ) -> None:  # noqa: ANN001
            del metadata_retention_days, storage_chunk_size, create_buffer_size, max_file_bytes
            del max_items_per_batch, max_line_bytes, max_pending_batches_per_scope
            del callable_target_grant_service, callable_target_scope_policy_mode
            self.repository = repository
            self.storage = storage
            self.storage_registry = storage_registry
            created["service"] = self

    monkeypatch.setattr(
        "src.bootstrap.batch.S3BatchArtifactStorage",
        lambda **kwargs: {"backend": "s3", **kwargs},
    )
    monkeypatch.setattr("src.bootstrap.batch.LocalBatchArtifactStorage", lambda path: {"backend": "local", "path": path})
    monkeypatch.setattr("src.bootstrap.batch.BatchService", FakeBatchService)

    app = SimpleNamespace(state=SimpleNamespace())
    repository = object()

    runtime = await init_batch_runtime(
        app,
        _batch_config(enabled=True, worker_enabled=False, gc_enabled=False, storage_backend="s3", s3_bucket="batch-bucket"),
        repository=repository,
    )

    assert app.state.batch_storage["backend"] == "s3"
    assert app.state.batch_storage["bucket"] == "batch-bucket"
    assert app.state.batch_storage_registry["local"]["path"] == "/tmp/batch-artifacts"
    assert app.state.batch_storage_registry["s3"]["backend"] == "s3"
    assert created["service"].storage == app.state.batch_storage
    assert created["service"].storage_registry == app.state.batch_storage_registry
    assert runtime.worker is None
    assert runtime.gc_worker is None


@pytest.mark.asyncio
async def test_init_batch_runtime_allows_s3_when_local_storage_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeBatchService:
        def __init__(self, repository, storage, storage_registry=None, **kwargs) -> None:  # noqa: ANN001
            del repository, kwargs
            self.storage = storage
            self.storage_registry = storage_registry

    def _fail_local(path: str):  # noqa: ANN001
        raise RuntimeError("local storage unavailable")

    monkeypatch.setattr("src.bootstrap.batch.LocalBatchArtifactStorage", _fail_local)
    monkeypatch.setattr(
        "src.bootstrap.batch.S3BatchArtifactStorage",
        lambda **kwargs: {"backend": "s3", **kwargs},
    )
    monkeypatch.setattr("src.bootstrap.batch.BatchService", FakeBatchService)

    app = SimpleNamespace(state=SimpleNamespace())

    runtime = await init_batch_runtime(
        app,
        _batch_config(enabled=True, worker_enabled=False, gc_enabled=False, storage_backend="s3", s3_bucket="batch-bucket"),
        repository=object(),
    )

    assert app.state.batch_storage == {"backend": "s3", "bucket": "batch-bucket", "region": "us-east-1", "prefix": "deltallm/batch-artifacts", "endpoint_url": None, "access_key_id": None, "secret_access_key": None, "spool_max_bytes": 8_388_608}
    assert "local" not in app.state.batch_storage_registry
    assert app.state.batch_storage_registry["s3"]["backend"] == "s3"
    assert runtime.worker is None
    assert runtime.gc_worker is None


@pytest.mark.asyncio
async def test_init_batch_runtime_rejects_s3_without_bucket() -> None:
    app = SimpleNamespace(state=SimpleNamespace())

    with pytest.raises(RuntimeError, match="embeddings_batch_s3_bucket"):
        await init_batch_runtime(
            app,
            _batch_config(enabled=True, worker_enabled=False, gc_enabled=False, storage_backend="s3", s3_bucket=None),
            repository=object(),
        )


@pytest.mark.asyncio
async def test_drain_worker_task_waits_for_natural_exit() -> None:
    finished = asyncio.Event()

    async def _worker() -> None:
        await asyncio.sleep(0.01)
        finished.set()

    task = asyncio.create_task(_worker())
    await _drain_worker_task(task, label="test worker", timeout=1.0)
    assert finished.is_set()
    assert task.done() and not task.cancelled()


@pytest.mark.asyncio
async def test_drain_worker_task_cancels_on_timeout() -> None:
    started = asyncio.Event()

    async def _worker() -> None:
        started.set()
        await asyncio.sleep(10.0)

    task = asyncio.create_task(_worker())
    await started.wait()
    await _drain_worker_task(task, label="test worker", timeout=0.05)
    assert task.done()
    assert task.cancelled()
