from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.bootstrap import BootstrapStatus
from src.bootstrap.audit import init_audit_runtime, shutdown_audit_runtime
from src.bootstrap.batch import init_batch_runtime, shutdown_batch_runtime


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
            batch_metadata_retention_days=14,
            embeddings_batch_poll_interval_seconds=5,
            embeddings_batch_heartbeat_interval_seconds=15,
            embeddings_batch_job_lease_seconds=120,
            embeddings_batch_item_lease_seconds=360,
            embeddings_batch_finalization_retry_delay_seconds=60,
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
            callable_target_grant_service=None,  # noqa: ANN001
            callable_target_scope_policy_mode="enforce",  # noqa: ANN001
        ) -> None:
            self.repository = repository
            self.storage = storage
            self.storage_registry = storage_registry
            self.metadata_retention_days = metadata_retention_days
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
            await asyncio.sleep(3600)

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
            await asyncio.sleep(3600)

        def stop(self) -> None:
            self.stopped = True

    monkeypatch.setattr("src.bootstrap.batch.LocalBatchArtifactStorage", lambda path: {"path": path})
    monkeypatch.setattr("src.bootstrap.batch.BatchService", FakeBatchService)
    monkeypatch.setattr("src.bootstrap.batch.BatchExecutorWorker", FakeBatchWorker)
    monkeypatch.setattr("src.bootstrap.batch.BatchRetentionCleanupWorker", FakeGCWorker)

    app = SimpleNamespace(state=SimpleNamespace())
    repository = object()

    runtime = await init_batch_runtime(
        app,
        _batch_config(enabled=True, worker_enabled=True, gc_enabled=True),
        repository=repository,
    )

    assert app.state.batch_storage == {"path": "/tmp/batch-artifacts"}
    assert app.state.batch_storage_registry == {"local": {"path": "/tmp/batch-artifacts"}}
    assert isinstance(app.state.batch_service, FakeBatchService)
    assert created["service"].repository is repository
    assert created["service"].storage_registry == {"local": {"path": "/tmp/batch-artifacts"}}
    assert runtime.worker is created["worker"]
    assert runtime.worker_task is not None
    assert runtime.gc_worker is created["gc_worker"]
    assert runtime.gc_task is not None
    assert created["gc_worker"].storage_registry == {"local": {"path": "/tmp/batch-artifacts"}}
    assert runtime.statuses == (
        BootstrapStatus("embeddings_batch", "ready"),
        BootstrapStatus("embeddings_batch_worker", "ready"),
        BootstrapStatus("embeddings_batch_gc", "ready"),
    )

    await shutdown_batch_runtime(runtime)

    assert created["worker"].stopped is True
    assert created["gc_worker"].stopped is True


@pytest.mark.asyncio
async def test_init_batch_runtime_selects_s3_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    created: dict[str, object] = {}

    class FakeBatchService:
        def __init__(self, repository, storage, storage_registry=None, metadata_retention_days=30, callable_target_grant_service=None, callable_target_scope_policy_mode="enforce") -> None:  # noqa: ANN001
            del metadata_retention_days, callable_target_grant_service, callable_target_scope_policy_mode
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
async def test_init_batch_runtime_rejects_s3_without_bucket() -> None:
    app = SimpleNamespace(state=SimpleNamespace())

    with pytest.raises(RuntimeError, match="embeddings_batch_s3_bucket"):
        await init_batch_runtime(
            app,
            _batch_config(enabled=True, worker_enabled=False, gc_enabled=False, storage_backend="s3", s3_bucket=None),
            repository=object(),
        )
