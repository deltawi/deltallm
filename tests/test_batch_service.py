from __future__ import annotations

from datetime import UTC, datetime
import logging

import pytest
from fastapi import HTTPException

from src.batch.models import BatchFileRecord, BatchJobRecord, BatchJobStatus
from src.batch.service import BatchService
from src.metrics.batch import deltallm_batch_artifact_failures_metric
from src.db.callable_targets import CallableTargetBindingRecord
from src.db.callable_target_policies import CallableTargetScopePolicyRecord
from src.models.responses import UserAPIKeyAuth
from src.services.callable_target_grants import CallableTargetGrantService


class _DummyRepo:
    pass


class _DummyStorage:
    backend_name = "local"

    def __init__(self) -> None:
        self.reads: list[str] = []
        self.lines_by_key: dict[str, list[str]] = {}
        self.writes: list[bytes] = []

    async def read_bytes(self, storage_key: str) -> bytes:
        self.reads.append(storage_key)
        return b"{}"

    async def iter_lines(self, storage_key: str, chunk_size: int = 65_536, max_line_bytes: int | None = None):  # noqa: ARG002
        del max_line_bytes
        self.reads.append(storage_key)
        for line in self.lines_by_key.get(storage_key, []):
            yield line

    async def write_chunks(self, *, purpose: str, filename: str, chunks):  # noqa: ANN001
        del purpose, filename
        payload = bytearray()
        async for chunk in chunks:
            payload.extend(chunk)
        self.writes.append(bytes(payload))
        return "batch/file.jsonl", len(payload), "checksum"


def _metric_counter_value(metric, **labels) -> float:  # noqa: ANN001
    return float(metric.labels(**labels)._value.get())


class _FakeCallableTargetBindingRepository:
    def __init__(self, bindings: list[CallableTargetBindingRecord]) -> None:
        self.bindings = list(bindings)

    async def list_bindings(self, *, callable_key=None, scope_type=None, scope_id=None, limit=200, offset=0):  # noqa: ANN001, ANN201
        items = list(self.bindings)
        if callable_key:
            items = [item for item in items if item.callable_key == callable_key]
        if scope_type:
            items = [item for item in items if item.scope_type == scope_type]
        if scope_id:
            items = [item for item in items if item.scope_id == scope_id]
        sliced = items[offset : offset + limit]
        return sliced, len(items)


class _FakeCallableTargetScopePolicyRepository:
    def __init__(self, policies: list[CallableTargetScopePolicyRecord]) -> None:
        self.policies = list(policies)

    async def list_policies(self, *, scope_type=None, scope_id=None, limit=200, offset=0):  # noqa: ANN001, ANN201
        items = list(self.policies)
        if scope_type:
            items = [item for item in items if item.scope_type == scope_type]
        if scope_id:
            items = [item for item in items if item.scope_id == scope_id]
        sliced = items[offset : offset + limit]
        return sliced, len(items)


def _service(
    *,
    callable_target_grant_service: CallableTargetGrantService | None = None,
    callable_target_scope_policy_mode: str = "enforce",
    storage: _DummyStorage | None = None,
    storage_registry: dict[str, _DummyStorage] | None = None,
) -> BatchService:
    return BatchService(
        repository=_DummyRepo(),
        storage=storage or _DummyStorage(),
        storage_registry=storage_registry,
        callable_target_grant_service=callable_target_grant_service,
        callable_target_scope_policy_mode=callable_target_scope_policy_mode,
    )  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_parse_input_jsonl_accepts_embeddings_lines() -> None:
    grant_service = CallableTargetGrantService(
        repository=_FakeCallableTargetBindingRepository(
            [
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-1",
                    callable_key="text-embedding-3-small",
                    scope_type="organization",
                    scope_id="org-1",
                    enabled=True,
                )
            ]
        )
    )
    await grant_service.reload()
    auth = UserAPIKeyAuth(api_key="sk-test", organization_id="org-1")
    service = _service(callable_target_grant_service=grant_service)
    payload = b'{"custom_id":"item-1","url":"/v1/embeddings","body":{"model":"text-embedding-3-small","input":"hello"}}\n'
    items, model = service._parse_input_jsonl(payload, endpoint="/v1/embeddings", auth=auth)
    assert len(items) == 1
    assert model == "text-embedding-3-small"


@pytest.mark.asyncio
async def test_parse_input_jsonl_rejects_duplicate_custom_id() -> None:
    grant_service = CallableTargetGrantService(
        repository=_FakeCallableTargetBindingRepository(
            [
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-1",
                    callable_key="text-embedding-3-small",
                    scope_type="organization",
                    scope_id="org-1",
                    enabled=True,
                )
            ]
        )
    )
    await grant_service.reload()
    service = _service(callable_target_grant_service=grant_service)
    auth = UserAPIKeyAuth(api_key="sk-test", organization_id="org-1")
    payload = (
        b'{"custom_id":"dup","url":"/v1/embeddings","body":{"model":"text-embedding-3-small","input":"a"}}\n'
        b'{"custom_id":"dup","url":"/v1/embeddings","body":{"model":"text-embedding-3-small","input":"b"}}\n'
    )
    with pytest.raises(HTTPException) as exc:
        service._parse_input_jsonl(payload, endpoint="/v1/embeddings", auth=auth)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_parse_input_jsonl_rejects_model_outside_effective_scope() -> None:
    grant_service = CallableTargetGrantService(
        repository=_FakeCallableTargetBindingRepository(
            [
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-org-1",
                    callable_key="text-embedding-3-small",
                    scope_type="organization",
                    scope_id="org-1",
                    enabled=True,
                ),
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-org-2",
                    callable_key="text-embedding-3-large",
                    scope_type="organization",
                    scope_id="org-1",
                    enabled=True,
                ),
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-user-1",
                    callable_key="text-embedding-3-small",
                    scope_type="user",
                    scope_id="user-1",
                    enabled=True,
                ),
            ]
        )
    )
    await grant_service.reload()
    service = _service(callable_target_grant_service=grant_service)
    auth = UserAPIKeyAuth(
        api_key="sk-test",
        organization_id="org-1",
        user_id="user-1",
    )
    payload = b'{"custom_id":"item-1","url":"/v1/embeddings","body":{"model":"text-embedding-3-large","input":"hello"}}\n'

    with pytest.raises(HTTPException) as exc:
        service._parse_input_jsonl(payload, endpoint="/v1/embeddings", auth=auth)

    assert exc.value.status_code == 403
    assert exc.value.detail == "Model 'text-embedding-3-large' is not allowed for this key"


@pytest.mark.asyncio
async def test_parse_input_jsonl_uses_explicit_callable_target_grants() -> None:
    grant_service = CallableTargetGrantService(
        repository=_FakeCallableTargetBindingRepository(
            [
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-1",
                    callable_key="text-embedding-3-small",
                    scope_type="organization",
                    scope_id="org-1",
                    enabled=True,
                )
            ]
        )
    )
    await grant_service.reload()
    service = _service(callable_target_grant_service=grant_service)
    auth = UserAPIKeyAuth(
        api_key="sk-test",
        organization_id="org-1",
        models=["text-embedding-3-small", "text-embedding-3-large"],
    )
    payload = b'{"custom_id":"item-1","url":"/v1/embeddings","body":{"model":"text-embedding-3-small","input":"hello"}}\n'

    items, model = service._parse_input_jsonl(payload, endpoint="/v1/embeddings", auth=auth)

    assert len(items) == 1
    assert model == "text-embedding-3-small"


@pytest.mark.asyncio
async def test_parse_input_jsonl_honors_enforced_scope_policy_mode() -> None:
    grant_service = CallableTargetGrantService(
        repository=_FakeCallableTargetBindingRepository(
            [
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-org-1",
                    callable_key="text-embedding-3-small",
                    scope_type="organization",
                    scope_id="org-1",
                    enabled=True,
                )
            ]
        ),
        policy_repository=_FakeCallableTargetScopePolicyRepository(
            [
                CallableTargetScopePolicyRecord(
                    callable_target_scope_policy_id="ctp-team-1",
                    scope_type="team",
                    scope_id="team-1",
                    mode="restrict",
                )
            ]
        ),
    )
    await grant_service.reload()
    service = _service(
        callable_target_grant_service=grant_service,
        callable_target_scope_policy_mode="enforce",
    )
    auth = UserAPIKeyAuth(
        api_key="sk-test",
        team_id="team-1",
        organization_id="org-1",
        models=["text-embedding-3-small"],
    )
    payload = b'{"custom_id":"item-1","url":"/v1/embeddings","body":{"model":"text-embedding-3-small","input":"hello"}}\n'

    with pytest.raises(HTTPException) as exc:
        service._parse_input_jsonl(payload, endpoint="/v1/embeddings", auth=auth)

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_file_content_denies_different_key_without_team_match() -> None:
    now = datetime.now(tz=UTC)

    class _Repo:
        async def get_file(self, file_id: str):
            del file_id
            return BatchFileRecord(
                file_id="f1",
                purpose="batch",
                filename="x.jsonl",
                bytes=1,
                status="processed",
                storage_backend="local",
                storage_key="k1",
                checksum=None,
                created_by_api_key="key-a",
                created_by_user_id=None,
                created_by_team_id=None,
                created_at=now,
                expires_at=None,
            )

        async def count_active_jobs_for_scope(self, **kwargs):
            del kwargs
            return 0

    service = BatchService(repository=_Repo(), storage=_DummyStorage())  # type: ignore[arg-type]
    with pytest.raises(HTTPException) as exc:
        await service.get_file_content(file_id="f1", auth=UserAPIKeyAuth(api_key="key-b"))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_file_content_uses_file_storage_backend_from_registry() -> None:
    now = datetime.now(tz=UTC)

    class _Repo:
        async def get_file(self, file_id: str):
            del file_id
            return BatchFileRecord(
                file_id="f1",
                purpose="batch",
                filename="x.jsonl",
                bytes=1,
                status="processed",
                storage_backend="local",
                storage_key="legacy/local-file",
                checksum=None,
                created_by_api_key="key-a",
                created_by_user_id=None,
                created_by_team_id=None,
                created_at=now,
                expires_at=None,
            )

    local_storage = _DummyStorage()
    active_storage = _DummyStorage()
    active_storage.backend_name = "s3"
    service = BatchService(
        repository=_Repo(),  # type: ignore[arg-type]
        storage=active_storage,  # type: ignore[arg-type]
        storage_registry={"local": local_storage, "s3": active_storage},  # type: ignore[arg-type]
    )

    content = await service.get_file_content(file_id="f1", auth=UserAPIKeyAuth(api_key="key-a"))

    assert content == b"{}"
    assert local_storage.reads == ["legacy/local-file"]
    assert active_storage.reads == []


@pytest.mark.asyncio
async def test_get_file_content_allows_same_organization_without_team_match() -> None:
    now = datetime.now(tz=UTC)

    class _Repo:
        async def get_file(self, file_id: str):
            del file_id
            return BatchFileRecord(
                file_id="f1",
                purpose="batch_output",
                filename="x.jsonl",
                bytes=1,
                status="processed",
                storage_backend="local",
                storage_key="org/output-file",
                checksum=None,
                created_by_api_key="key-a",
                created_by_user_id=None,
                created_by_team_id=None,
                created_at=now,
                expires_at=None,
                created_by_organization_id="org-1",
            )

    storage = _DummyStorage()
    service = BatchService(repository=_Repo(), storage=storage)  # type: ignore[arg-type]

    content = await service.get_file_content(
        file_id="f1",
        auth=UserAPIKeyAuth(api_key="key-b", organization_id="org-1"),
    )

    assert content == b"{}"
    assert storage.reads == ["org/output-file"]


@pytest.mark.asyncio
async def test_get_file_content_denies_same_organization_for_team_owned_file_with_different_team() -> None:
    now = datetime.now(tz=UTC)

    class _Repo:
        async def get_file(self, file_id: str):
            del file_id
            return BatchFileRecord(
                file_id="f1",
                purpose="batch_output",
                filename="x.jsonl",
                bytes=1,
                status="processed",
                storage_backend="local",
                storage_key="team/output-file",
                checksum=None,
                created_by_api_key="key-a",
                created_by_user_id=None,
                created_by_team_id="team-1",
                created_at=now,
                expires_at=None,
                created_by_organization_id="org-1",
            )

    service = BatchService(repository=_Repo(), storage=_DummyStorage())  # type: ignore[arg-type]

    with pytest.raises(HTTPException) as exc:
        await service.get_file_content(
            file_id="f1",
            auth=UserAPIKeyAuth(api_key="key-b", team_id="team-2", organization_id="org-1"),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_create_embeddings_batch_reads_input_from_file_storage_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(tz=UTC)
    monkeypatch.setattr("src.batch.service.ensure_batch_model_allowed", lambda *args, **kwargs: None)

    class _Repo:
        def __init__(self) -> None:
            self.active_scope_kwargs: dict[str, str | None] | None = None

        async def get_file(self, file_id: str):
            assert file_id == "f1"
            return BatchFileRecord(
                file_id="f1",
                purpose="batch",
                filename="x.jsonl",
                bytes=100,
                status="processed",
                storage_backend="local",
                storage_key="legacy/input-file",
                checksum=None,
                created_by_api_key="key-a",
                created_by_user_id=None,
                created_by_team_id=None,
                created_at=now,
                expires_at=None,
            )

        async def count_active_jobs_for_scope(self, **kwargs):
            self.active_scope_kwargs = kwargs
            return 0

        async def create_job(self, **kwargs):
            del kwargs
            return BatchJobRecord(
                batch_id="b1",
                endpoint="/v1/embeddings",
                status=BatchJobStatus.VALIDATING,
                execution_mode="managed_internal",
                input_file_id="f1",
                output_file_id=None,
                error_file_id=None,
                model="m1",
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
                cancel_requested_at=None,
                status_last_updated_at=now,
                created_by_api_key="key-a",
                created_by_user_id=None,
                created_by_team_id=None,
                created_at=now,
                started_at=None,
                completed_at=None,
                expires_at=None,
            )

        async def create_items(self, batch_id: str, items):  # noqa: ANN001
            assert batch_id == "b1"
            assert len(items) == 1
            return 1

        async def set_job_queued(self, batch_id: str, total_items: int):
            assert batch_id == "b1"
            assert total_items == 1
            return BatchJobRecord(
                batch_id="b1",
                endpoint="/v1/embeddings",
                status=BatchJobStatus.QUEUED,
                execution_mode="managed_internal",
                input_file_id="f1",
                output_file_id=None,
                error_file_id=None,
                model="m1",
                metadata={},
                provider_batch_id=None,
                provider_status=None,
                provider_error=None,
                provider_last_sync_at=None,
                total_items=1,
                in_progress_items=0,
                completed_items=0,
                failed_items=0,
                cancelled_items=0,
                locked_by=None,
                lease_expires_at=None,
                cancel_requested_at=None,
                status_last_updated_at=now,
                created_by_api_key="key-a",
                created_by_user_id=None,
                created_by_team_id=None,
                created_at=now,
                started_at=None,
                completed_at=None,
                expires_at=None,
            )

    local_storage = _DummyStorage()
    local_storage.lines_by_key["legacy/input-file"] = [
        '{"custom_id":"c1","url":"/v1/embeddings","body":{"model":"m1","input":"hello"}}'
    ]
    active_storage = _DummyStorage()
    active_storage.backend_name = "s3"
    repo = _Repo()
    service = BatchService(
        repository=repo,  # type: ignore[arg-type]
        storage=active_storage,  # type: ignore[arg-type]
        storage_registry={"local": local_storage, "s3": active_storage},  # type: ignore[arg-type]
    )

    result = await service.create_embeddings_batch(
        auth=UserAPIKeyAuth(api_key="key-a", models=["m1"]),
        input_file_id="f1",
        endpoint="/v1/embeddings",
        metadata=None,
        completion_window=None,
    )

    assert result["status"] == "queued"
    assert local_storage.reads == ["legacy/input-file"]
    assert active_storage.reads == []
    assert repo.active_scope_kwargs == {"created_by_api_key": "key-a", "created_by_team_id": None}


@pytest.mark.asyncio
async def test_get_file_content_increments_artifact_read_failure_metric() -> None:
    now = datetime.now(tz=UTC)

    class _Repo:
        async def get_file(self, file_id: str):
            assert file_id == "f1"
            return BatchFileRecord(
                file_id="f1",
                purpose="batch",
                filename="x.jsonl",
                bytes=100,
                status="processed",
                storage_backend="local",
                storage_key="legacy/input-file",
                checksum=None,
                created_by_api_key="key-a",
                created_by_user_id=None,
                created_by_team_id=None,
                created_at=now,
                expires_at=None,
            )

        async def count_active_jobs_for_scope(self, **kwargs):
            del kwargs
            return 0

    class _FailingStorage(_DummyStorage):
        async def read_bytes(self, storage_key: str) -> bytes:
            self.reads.append(storage_key)
            raise RuntimeError("storage offline")

    storage = _FailingStorage()
    service = BatchService(
        repository=_Repo(),  # type: ignore[arg-type]
        storage=storage,  # type: ignore[arg-type]
    )
    before = _metric_counter_value(
        deltallm_batch_artifact_failures_metric,
        operation="read",
        backend="local",
    )

    with pytest.raises(RuntimeError, match="storage offline"):
        await service.get_file_content(file_id="f1", auth=UserAPIKeyAuth(api_key="key-a"))

    after = _metric_counter_value(
        deltallm_batch_artifact_failures_metric,
        operation="read",
        backend="local",
    )
    assert storage.reads == ["legacy/input-file"]
    assert after == before + 1


@pytest.mark.asyncio
async def test_create_embeddings_batch_increments_artifact_read_failure_metric_on_input_read(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(tz=UTC)
    monkeypatch.setattr("src.batch.service.ensure_batch_model_allowed", lambda *args, **kwargs: None)

    class _Repo:
        async def get_file(self, file_id: str):
            del file_id
            return BatchFileRecord(
                file_id="f1",
                purpose="batch",
                filename="x.jsonl",
                bytes=100,
                status="processed",
                storage_backend="local",
                storage_key="legacy/input-file",
                checksum=None,
                created_by_api_key="key-a",
                created_by_user_id=None,
                created_by_team_id=None,
                created_at=now,
                expires_at=None,
            )

        async def count_active_jobs_for_scope(self, **kwargs):
            del kwargs
            return 0

    class _FailingStorage(_DummyStorage):
        async def iter_lines(self, storage_key: str, chunk_size: int = 65_536, max_line_bytes: int | None = None):  # noqa: ARG002
            self.reads.append(storage_key)
            raise RuntimeError("input read failed")
            yield  # pragma: no cover

    storage = _FailingStorage()
    service = BatchService(
        repository=_Repo(),  # type: ignore[arg-type]
        storage=storage,  # type: ignore[arg-type]
    )
    before = _metric_counter_value(
        deltallm_batch_artifact_failures_metric,
        operation="read",
        backend="local",
    )

    with pytest.raises(RuntimeError, match="input read failed"):
        await service.create_embeddings_batch(
            auth=UserAPIKeyAuth(api_key="key-a"),
            input_file_id="f1",
            endpoint="/v1/embeddings",
            metadata=None,
            completion_window=None,
        )

    after = _metric_counter_value(
        deltallm_batch_artifact_failures_metric,
        operation="read",
        backend="local",
    )
    assert storage.reads == ["legacy/input-file"]
    assert after == before + 1


@pytest.mark.asyncio
async def test_batch_service_refresh_runtime_metrics_logs_debug_on_failure(caplog: pytest.LogCaptureFixture) -> None:
    class _Repo:
        async def summarize_runtime_statuses(self, *, now):  # noqa: ANN001
            del now
            raise RuntimeError("metrics unavailable")

    service = BatchService(
        repository=_Repo(),  # type: ignore[arg-type]
        storage=_DummyStorage(),  # type: ignore[arg-type]
    )

    with caplog.at_level(logging.DEBUG):
        await service._refresh_batch_runtime_metrics()

    assert "batch service runtime metrics refresh failed" in caplog.text


@pytest.mark.asyncio
async def test_batch_service_refresh_runtime_metrics_logs_debug_on_publish_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _Repo:
        async def summarize_runtime_statuses(self, *, now):  # noqa: ANN001
            del now
            return {"queued": 1, "in_progress": 0, "finalizing": 0}

    service = BatchService(
        repository=_Repo(),  # type: ignore[arg-type]
        storage=_DummyStorage(),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(
        "src.batch.service.publish_batch_runtime_summary",
        lambda summary: (_ for _ in ()).throw(RuntimeError("publish unavailable")),  # noqa: ARG005
    )

    with caplog.at_level(logging.DEBUG):
        await service._refresh_batch_runtime_metrics()

    assert "batch service runtime metrics refresh failed" in caplog.text


@pytest.mark.asyncio
async def test_create_file_enforces_max_file_bytes() -> None:
    class _Upload:
        def __init__(self) -> None:
            self.filename = "batch.jsonl"
            self._chunks = [b"a" * 10, b"b" * 10, b""]

        async def read(self, size: int = -1):  # noqa: ARG002
            return self._chunks.pop(0)

    service = BatchService(
        repository=_DummyRepo(),  # type: ignore[arg-type]
        storage=_DummyStorage(),  # type: ignore[arg-type]
        max_file_bytes=15,
    )

    with pytest.raises(HTTPException) as exc:
        await service.create_file(
            auth=UserAPIKeyAuth(api_key="key-a"),
            upload=_Upload(),  # type: ignore[arg-type]
            purpose="batch",
        )

    assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_create_embeddings_batch_enforces_pending_batch_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(tz=UTC)
    monkeypatch.setattr("src.batch.service.ensure_batch_model_allowed", lambda *args, **kwargs: None)

    class _Repo:
        async def get_file(self, file_id: str):
            del file_id
            return BatchFileRecord(
                file_id="f1",
                purpose="batch",
                filename="x.jsonl",
                bytes=100,
                status="processed",
                storage_backend="local",
                storage_key="legacy/input-file",
                checksum=None,
                created_by_api_key="key-a",
                created_by_user_id=None,
                created_by_team_id=None,
                created_at=now,
                expires_at=None,
            )

        async def count_active_jobs_for_scope(self, **kwargs):
            del kwargs
            return 2

    local_storage = _DummyStorage()
    local_storage.lines_by_key["legacy/input-file"] = [
        '{"custom_id":"c1","url":"/v1/embeddings","body":{"model":"m1","input":"hello"}}'
    ]
    service = BatchService(
        repository=_Repo(),  # type: ignore[arg-type]
        storage=local_storage,  # type: ignore[arg-type]
        max_pending_batches_per_scope=2,
    )

    with pytest.raises(HTTPException) as exc:
        await service.create_embeddings_batch(
            auth=UserAPIKeyAuth(api_key="key-a"),
            input_file_id="f1",
            endpoint="/v1/embeddings",
            metadata=None,
            completion_window=None,
        )

    assert exc.value.status_code == 429
    assert local_storage.reads == []


@pytest.mark.asyncio
async def test_create_embeddings_batch_uses_transactional_scope_lock_for_pending_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(tz=UTC)
    monkeypatch.setattr("src.batch.service.ensure_batch_model_allowed", lambda *args, **kwargs: None)

    class _TxDB:
        def __init__(self) -> None:
            self.entries = 0

        def tx(self):  # noqa: ANN201
            return self

        async def __aenter__(self):  # noqa: ANN202
            self.entries += 1
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN202
            del exc_type, exc, tb
            return False

    class _Repo:
        def __init__(self, prisma, state, *, within_transaction: bool = False):  # noqa: ANN001
            self.prisma = prisma
            self.state = state
            self.within_transaction = within_transaction

        def with_prisma(self, prisma_client):  # noqa: ANN201
            return _Repo(prisma_client, self.state, within_transaction=True)

        async def get_file(self, file_id: str):
            del file_id
            return BatchFileRecord(
                file_id="f1",
                purpose="batch",
                filename="x.jsonl",
                bytes=100,
                status="processed",
                storage_backend="local",
                storage_key="legacy/input-file",
                checksum=None,
                created_by_api_key="key-a",
                created_by_user_id=None,
                created_by_team_id=None,
                created_at=now,
                expires_at=None,
            )

        async def acquire_scope_advisory_lock(self, *, scope_type: str, scope_id: str) -> None:
            self.state["locks"].append((scope_type, scope_id, self.prisma))

        async def count_active_jobs_for_scope(self, **kwargs):
            self.state["counts"].append((kwargs, self.prisma))
            return 0

        async def create_job(self, **kwargs):
            self.state["create_job_prismas"].append(self.prisma)
            del kwargs
            return BatchJobRecord(
                batch_id="b1",
                endpoint="/v1/embeddings",
                status=BatchJobStatus.VALIDATING,
                execution_mode="managed_internal",
                input_file_id="f1",
                output_file_id=None,
                error_file_id=None,
                model="m1",
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
                cancel_requested_at=None,
                status_last_updated_at=now,
                created_by_api_key="key-a",
                created_by_user_id=None,
                created_by_team_id=None,
                created_at=now,
                started_at=None,
                completed_at=None,
                expires_at=None,
            )

        async def create_items(self, batch_id: str, items):  # noqa: ANN001
            assert batch_id == "b1"
            self.state["create_items_calls"] += 1
            return len(items)

        async def set_job_queued(self, batch_id: str, total_items: int):
            assert batch_id == "b1"
            assert total_items == 1
            return BatchJobRecord(
                batch_id="b1",
                endpoint="/v1/embeddings",
                status=BatchJobStatus.QUEUED,
                execution_mode="managed_internal",
                input_file_id="f1",
                output_file_id=None,
                error_file_id=None,
                model="m1",
                metadata={},
                provider_batch_id=None,
                provider_status=None,
                provider_error=None,
                provider_last_sync_at=None,
                total_items=1,
                in_progress_items=0,
                completed_items=0,
                failed_items=0,
                cancelled_items=0,
                locked_by=None,
                lease_expires_at=None,
                cancel_requested_at=None,
                status_last_updated_at=now,
                created_by_api_key="key-a",
                created_by_user_id=None,
                created_by_team_id=None,
                created_at=now,
                started_at=None,
                completed_at=None,
                expires_at=None,
            )

    state = {"locks": [], "counts": [], "create_job_prismas": [], "create_items_calls": 0}
    tx_db = _TxDB()
    local_storage = _DummyStorage()
    local_storage.lines_by_key["legacy/input-file"] = [
        '{"custom_id":"c1","url":"/v1/embeddings","body":{"model":"m1","input":"hello"}}'
    ]
    repo = _Repo(tx_db, state)
    service = BatchService(
        repository=repo,  # type: ignore[arg-type]
        storage=local_storage,  # type: ignore[arg-type]
        max_pending_batches_per_scope=2,
    )

    result = await service.create_embeddings_batch(
        auth=UserAPIKeyAuth(api_key="key-a"),
        input_file_id="f1",
        endpoint="/v1/embeddings",
        metadata=None,
        completion_window=None,
    )

    assert result["status"] == "queued"
    assert tx_db.entries == 1
    assert state["locks"] == [("api_key", "key-a", tx_db)]
    assert state["counts"] == [
        ({"created_by_api_key": "key-a", "created_by_team_id": None}, tx_db),
        ({"created_by_api_key": "key-a", "created_by_team_id": None}, tx_db),
    ]
    assert state["create_job_prismas"] == [tx_db]
    assert state["create_items_calls"] == 1


@pytest.mark.asyncio
async def test_create_embeddings_batch_non_transactional_scope_limit_skips_advisory_lock(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    now = datetime.now(tz=UTC)
    monkeypatch.setattr("src.batch.service.ensure_batch_model_allowed", lambda *args, **kwargs: None)

    class _NoTxDB:
        pass

    class _Repo:
        def __init__(self) -> None:
            self.prisma = _NoTxDB()
            self.lock_calls: list[tuple[str, str]] = []
            self.count_calls: list[dict[str, str | None]] = []

        async def get_file(self, file_id: str):
            del file_id
            return BatchFileRecord(
                file_id="f1",
                purpose="batch",
                filename="x.jsonl",
                bytes=100,
                status="processed",
                storage_backend="local",
                storage_key="legacy/input-file",
                checksum=None,
                created_by_api_key="key-a",
                created_by_user_id=None,
                created_by_team_id=None,
                created_at=now,
                expires_at=None,
            )

        async def acquire_scope_advisory_lock(self, *, scope_type: str, scope_id: str) -> None:
            self.lock_calls.append((scope_type, scope_id))

        async def count_active_jobs_for_scope(self, **kwargs):
            self.count_calls.append(kwargs)
            return 0

        async def create_job(self, **kwargs):
            del kwargs
            return BatchJobRecord(
                batch_id="b1",
                endpoint="/v1/embeddings",
                status=BatchJobStatus.VALIDATING,
                execution_mode="managed_internal",
                input_file_id="f1",
                output_file_id=None,
                error_file_id=None,
                model="m1",
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
                cancel_requested_at=None,
                status_last_updated_at=now,
                created_by_api_key="key-a",
                created_by_user_id=None,
                created_by_team_id=None,
                created_at=now,
                started_at=None,
                completed_at=None,
                expires_at=None,
            )

        async def create_items(self, batch_id: str, items):  # noqa: ANN001
            assert batch_id == "b1"
            return len(items)

        async def set_job_queued(self, batch_id: str, total_items: int):
            assert batch_id == "b1"
            assert total_items == 1
            return BatchJobRecord(
                batch_id="b1",
                endpoint="/v1/embeddings",
                status=BatchJobStatus.QUEUED,
                execution_mode="managed_internal",
                input_file_id="f1",
                output_file_id=None,
                error_file_id=None,
                model="m1",
                metadata={},
                provider_batch_id=None,
                provider_status=None,
                provider_error=None,
                provider_last_sync_at=None,
                total_items=1,
                in_progress_items=0,
                completed_items=0,
                failed_items=0,
                cancelled_items=0,
                locked_by=None,
                lease_expires_at=None,
                cancel_requested_at=None,
                status_last_updated_at=now,
                created_by_api_key="key-a",
                created_by_user_id=None,
                created_by_team_id=None,
                created_at=now,
                started_at=None,
                completed_at=None,
                expires_at=None,
            )

    repo = _Repo()
    local_storage = _DummyStorage()
    local_storage.lines_by_key["legacy/input-file"] = [
        '{"custom_id":"c1","url":"/v1/embeddings","body":{"model":"m1","input":"hello"}}'
    ]
    service = BatchService(
        repository=repo,  # type: ignore[arg-type]
        storage=local_storage,  # type: ignore[arg-type]
        max_pending_batches_per_scope=2,
    )

    with caplog.at_level(logging.DEBUG):
        result = await service.create_embeddings_batch(
            auth=UserAPIKeyAuth(api_key="key-a"),
            input_file_id="f1",
            endpoint="/v1/embeddings",
            metadata=None,
            completion_window=None,
        )

    assert result["status"] == "queued"
    assert repo.lock_calls == []
    assert repo.count_calls == [
        {"created_by_api_key": "key-a", "created_by_team_id": None},
        {"created_by_api_key": "key-a", "created_by_team_id": None},
    ]
    assert "best-effort without transaction support" in caplog.text


@pytest.mark.asyncio
async def test_create_embeddings_batch_queues_job_inside_same_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(tz=UTC)
    monkeypatch.setattr("src.batch.service.ensure_batch_model_allowed", lambda *args, **kwargs: None)

    class _TxDB:
        def __init__(self) -> None:
            self.entries = 0

        def tx(self):  # noqa: ANN201
            return self

        async def __aenter__(self):  # noqa: ANN202
            self.entries += 1
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN202
            del exc_type, exc, tb
            return False

    class _Repo:
        def __init__(self, prisma, state, *, within_transaction: bool = False):  # noqa: ANN001
            self.prisma = prisma
            self.state = state
            self.within_transaction = within_transaction

        def with_prisma(self, prisma_client):  # noqa: ANN201
            return _Repo(prisma_client, self.state, within_transaction=True)

        async def get_file(self, file_id: str):
            del file_id
            return BatchFileRecord(
                file_id="f1",
                purpose="batch",
                filename="x.jsonl",
                bytes=100,
                status="processed",
                storage_backend="local",
                storage_key="legacy/input-file",
                checksum=None,
                created_by_api_key="key-a",
                created_by_user_id=None,
                created_by_team_id=None,
                created_at=now,
                expires_at=None,
            )

        async def acquire_scope_advisory_lock(self, *, scope_type: str, scope_id: str) -> None:
            self.state["locks"].append((scope_type, scope_id, self.prisma))

        async def count_active_jobs_for_scope(self, **kwargs):
            self.state["counts"].append((kwargs, self.prisma))
            return 0

        async def create_job(self, **kwargs):
            self.state["create_job_prismas"].append(self.prisma)
            del kwargs
            return BatchJobRecord(
                batch_id="b1",
                endpoint="/v1/embeddings",
                status=BatchJobStatus.VALIDATING,
                execution_mode="managed_internal",
                input_file_id="f1",
                output_file_id=None,
                error_file_id=None,
                model="m1",
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
                cancel_requested_at=None,
                status_last_updated_at=now,
                created_by_api_key="key-a",
                created_by_user_id=None,
                created_by_team_id=None,
                created_at=now,
                started_at=None,
                completed_at=None,
                expires_at=None,
            )

        async def create_items(self, batch_id: str, items):  # noqa: ANN001
            assert batch_id == "b1"
            self.state["create_items_prismas"].append(self.prisma)
            return len(items)

        async def set_job_queued(self, batch_id: str, total_items: int):
            assert batch_id == "b1"
            assert total_items == 1
            self.state["set_job_queued_prismas"].append(self.prisma)
            return BatchJobRecord(
                batch_id="b1",
                endpoint="/v1/embeddings",
                status=BatchJobStatus.QUEUED,
                execution_mode="managed_internal",
                input_file_id="f1",
                output_file_id=None,
                error_file_id=None,
                model="m1",
                metadata={},
                provider_batch_id=None,
                provider_status=None,
                provider_error=None,
                provider_last_sync_at=None,
                total_items=1,
                in_progress_items=0,
                completed_items=0,
                failed_items=0,
                cancelled_items=0,
                locked_by=None,
                lease_expires_at=None,
                cancel_requested_at=None,
                status_last_updated_at=now,
                created_by_api_key="key-a",
                created_by_user_id=None,
                created_by_team_id=None,
                created_at=now,
                started_at=None,
                completed_at=None,
                expires_at=None,
            )

    state = {"locks": [], "counts": [], "create_job_prismas": [], "create_items_prismas": [], "set_job_queued_prismas": []}
    tx_db = _TxDB()
    local_storage = _DummyStorage()
    local_storage.lines_by_key["legacy/input-file"] = [
        '{"custom_id":"c1","url":"/v1/embeddings","body":{"model":"m1","input":"hello"}}'
    ]
    repo = _Repo(tx_db, state)
    service = BatchService(
        repository=repo,  # type: ignore[arg-type]
        storage=local_storage,  # type: ignore[arg-type]
    )

    result = await service.create_embeddings_batch(
        auth=UserAPIKeyAuth(api_key="key-a"),
        input_file_id="f1",
        endpoint="/v1/embeddings",
        metadata=None,
        completion_window=None,
    )

    assert result["status"] == "queued"
    assert tx_db.entries == 1
    assert state["create_job_prismas"] == [tx_db]
    assert state["create_items_prismas"] == [tx_db]
    assert state["set_job_queued_prismas"] == [tx_db]


@pytest.mark.asyncio
async def test_create_embeddings_batch_blocks_in_transaction_before_create_job(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(tz=UTC)
    monkeypatch.setattr("src.batch.service.ensure_batch_model_allowed", lambda *args, **kwargs: None)

    class _TxDB:
        def tx(self):  # noqa: ANN201
            return self

        async def __aenter__(self):  # noqa: ANN202
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN202
            del exc_type, exc, tb
            return False

    class _Repo:
        def __init__(self, prisma, state, *, within_transaction: bool = False):  # noqa: ANN001
            self.prisma = prisma
            self.state = state
            self.within_transaction = within_transaction

        def with_prisma(self, prisma_client):  # noqa: ANN201
            return _Repo(prisma_client, self.state, within_transaction=True)

        async def get_file(self, file_id: str):
            del file_id
            return BatchFileRecord(
                file_id="f1",
                purpose="batch",
                filename="x.jsonl",
                bytes=100,
                status="processed",
                storage_backend="local",
                storage_key="legacy/input-file",
                checksum=None,
                created_by_api_key="key-a",
                created_by_user_id=None,
                created_by_team_id=None,
                created_at=now,
                expires_at=None,
            )

        async def acquire_scope_advisory_lock(self, *, scope_type: str, scope_id: str) -> None:
            self.state["locks"].append((scope_type, scope_id))

        async def count_active_jobs_for_scope(self, **kwargs):
            del kwargs
            if not self.within_transaction:
                return 0
            return 2

        async def create_job(self, **kwargs):
            del kwargs
            self.state["create_job_called"] = True
            raise AssertionError("create_job should not be called when limit is reached")

    state = {"locks": [], "create_job_called": False}
    tx_db = _TxDB()
    local_storage = _DummyStorage()
    local_storage.lines_by_key["legacy/input-file"] = [
        '{"custom_id":"c1","url":"/v1/embeddings","body":{"model":"m1","input":"hello"}}'
    ]
    service = BatchService(
        repository=_Repo(tx_db, state),  # type: ignore[arg-type]
        storage=local_storage,  # type: ignore[arg-type]
        max_pending_batches_per_scope=2,
    )

    with pytest.raises(HTTPException) as exc:
        await service.create_embeddings_batch(
            auth=UserAPIKeyAuth(api_key="key-a"),
            input_file_id="f1",
            endpoint="/v1/embeddings",
            metadata=None,
            completion_window=None,
        )

    assert exc.value.status_code == 429
    assert state["locks"] == [("api_key", "key-a")]
    assert state["create_job_called"] is False


@pytest.mark.asyncio
async def test_create_embeddings_batch_rejects_invalid_later_line_before_creating_job(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(tz=UTC)
    monkeypatch.setattr("src.batch.service.ensure_batch_model_allowed", lambda *args, **kwargs: None)

    class _Repo:
        def __init__(self) -> None:
            self.create_job_called = False

        async def get_file(self, file_id: str):
            del file_id
            return BatchFileRecord(
                file_id="f1",
                purpose="batch",
                filename="x.jsonl",
                bytes=100,
                status="processed",
                storage_backend="local",
                storage_key="legacy/input-file",
                checksum=None,
                created_by_api_key="key-a",
                created_by_user_id=None,
                created_by_team_id=None,
                created_at=now,
                expires_at=None,
            )

        async def count_active_jobs_for_scope(self, **kwargs):
            del kwargs
            return 0

        async def create_job(self, **kwargs):
            del kwargs
            self.create_job_called = True
            raise AssertionError("create_job should not be called for invalid input")

    local_storage = _DummyStorage()
    local_storage.lines_by_key["legacy/input-file"] = [
        '{"custom_id":"c1","url":"/v1/embeddings","body":{"model":"m1","input":"hello"}}',
        '{"custom_id":"c2","url":"/v1/embeddings","body":',
    ]
    repo = _Repo()
    service = BatchService(
        repository=repo,  # type: ignore[arg-type]
        storage=local_storage,  # type: ignore[arg-type]
    )

    with pytest.raises(HTTPException) as exc:
        await service.create_embeddings_batch(
            auth=UserAPIKeyAuth(api_key="key-a"),
            input_file_id="f1",
            endpoint="/v1/embeddings",
            metadata=None,
            completion_window=None,
        )

    assert exc.value.status_code == 400
    assert repo.create_job_called is False


@pytest.mark.asyncio
async def test_list_batches_and_get_batch_share_team_scope() -> None:
    now = datetime.now(tz=UTC)

    class _Repo:
        async def get_job(self, batch_id: str):
            del batch_id
            return BatchJobRecord(
                batch_id="b1",
                endpoint="/v1/embeddings",
                status=BatchJobStatus.COMPLETED,
                execution_mode="managed_internal",
                input_file_id="f1",
                output_file_id=None,
                error_file_id=None,
                model="m1",
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
                locked_by=None,
                lease_expires_at=None,
                cancel_requested_at=None,
                status_last_updated_at=now,
                created_by_api_key="key-a",
                created_by_user_id=None,
                created_by_team_id="team-1",
                created_at=now,
                started_at=now,
                completed_at=now,
                expires_at=None,
            )

        async def list_jobs(self, **kwargs):
            assert kwargs["created_by_api_key"] == "key-b"
            assert kwargs["created_by_team_id"] == "team-1"
            assert kwargs["created_by_organization_id"] is None
            return [await self.get_job("b1")]

    service = BatchService(repository=_Repo(), storage=_DummyStorage())  # type: ignore[arg-type]
    auth = UserAPIKeyAuth(api_key="key-b", team_id="team-1")
    listed = await service.list_batches(auth=auth, limit=20)
    got = await service.get_batch(batch_id="b1", auth=auth)
    assert len(listed["data"]) == 1
    assert got["id"] == listed["data"][0]["id"]


@pytest.mark.asyncio
async def test_list_batches_and_get_batch_share_organization_scope() -> None:
    now = datetime.now(tz=UTC)

    class _Repo:
        async def get_job(self, batch_id: str):
            del batch_id
            return BatchJobRecord(
                batch_id="b1",
                endpoint="/v1/embeddings",
                status=BatchJobStatus.COMPLETED,
                execution_mode="managed_internal",
                input_file_id="f1",
                output_file_id=None,
                error_file_id=None,
                model="m1",
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
                locked_by=None,
                lease_expires_at=None,
                cancel_requested_at=None,
                status_last_updated_at=now,
                created_by_api_key="key-a",
                created_by_user_id=None,
                created_by_team_id=None,
                created_at=now,
                started_at=now,
                completed_at=now,
                expires_at=None,
                created_by_organization_id="org-1",
            )

        async def list_jobs(self, **kwargs):
            assert kwargs["created_by_api_key"] == "key-b"
            assert kwargs["created_by_team_id"] is None
            assert kwargs["created_by_organization_id"] == "org-1"
            return [await self.get_job("b1")]

    service = BatchService(repository=_Repo(), storage=_DummyStorage())  # type: ignore[arg-type]
    auth = UserAPIKeyAuth(api_key="key-b", organization_id="org-1")
    listed = await service.list_batches(auth=auth, limit=20)
    got = await service.get_batch(batch_id="b1", auth=auth)
    assert len(listed["data"]) == 1
    assert got["id"] == listed["data"][0]["id"]


@pytest.mark.asyncio
async def test_list_batches_prefers_team_scope_over_org_scope_for_runtime_visibility() -> None:
    class _Repo:
        async def list_jobs(self, **kwargs):
            assert kwargs["created_by_api_key"] == "key-b"
            assert kwargs["created_by_team_id"] == "team-1"
            assert kwargs["created_by_organization_id"] is None
            return []

    service = BatchService(repository=_Repo(), storage=_DummyStorage())  # type: ignore[arg-type]
    auth = UserAPIKeyAuth(api_key="key-b", team_id="team-1", organization_id="org-1")
    listed = await service.list_batches(auth=auth, limit=20)

    assert listed == {"object": "list", "data": []}


@pytest.mark.asyncio
async def test_get_batch_denies_same_organization_for_team_owned_batch_with_different_team() -> None:
    now = datetime.now(tz=UTC)

    class _Repo:
        async def get_job(self, batch_id: str):
            del batch_id
            return BatchJobRecord(
                batch_id="b1",
                endpoint="/v1/embeddings",
                status=BatchJobStatus.COMPLETED,
                execution_mode="managed_internal",
                input_file_id="f1",
                output_file_id=None,
                error_file_id=None,
                model="m1",
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
                locked_by=None,
                lease_expires_at=None,
                cancel_requested_at=None,
                status_last_updated_at=now,
                created_by_api_key="key-a",
                created_by_user_id=None,
                created_by_team_id="team-1",
                created_at=now,
                started_at=now,
                completed_at=now,
                expires_at=None,
                created_by_organization_id="org-1",
            )

    service = BatchService(repository=_Repo(), storage=_DummyStorage())  # type: ignore[arg-type]

    with pytest.raises(HTTPException) as exc:
        await service.get_batch(
            batch_id="b1",
            auth=UserAPIKeyAuth(api_key="key-b", team_id="team-2", organization_id="org-1"),
        )

    assert exc.value.status_code == 403
