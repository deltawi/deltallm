from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from src.batch.models import BatchFileRecord, BatchJobRecord, BatchJobStatus
from src.batch.service import BatchService
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

    async def read_bytes(self, storage_key: str) -> bytes:
        self.reads.append(storage_key)
        return b"{}"


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
async def test_create_embeddings_batch_reads_input_from_file_storage_backend() -> None:
    now = datetime.now(tz=UTC)

    class _Repo:
        async def get_file(self, file_id: str):
            assert file_id == "f1"
            return BatchFileRecord(
                file_id="f1",
                purpose="batch",
                filename="x.jsonl",
                bytes=1,
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
    active_storage = _DummyStorage()
    active_storage.backend_name = "s3"
    service = BatchService(
        repository=_Repo(),  # type: ignore[arg-type]
        storage=active_storage,  # type: ignore[arg-type]
        storage_registry={"local": local_storage, "s3": active_storage},  # type: ignore[arg-type]
    )

    def _fake_parse(payload: bytes, *, endpoint: str, auth):  # noqa: ANN001
        assert payload == b"{}"
        assert endpoint == "/v1/embeddings"
        assert auth.api_key == "key-a"
        return [type("Item", (), {"line_number": 1, "custom_id": "c1", "request_body": {"model": "m1"}})()], "m1"

    service._parse_input_jsonl = _fake_parse  # type: ignore[method-assign]

    result = await service.create_embeddings_batch(
        auth=UserAPIKeyAuth(api_key="key-a"),
        input_file_id="f1",
        endpoint="/v1/embeddings",
        metadata=None,
        completion_window=None,
    )

    assert result["status"] == "queued"
    assert local_storage.reads == ["legacy/input-file"]
    assert active_storage.reads == []


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
            return [await self.get_job("b1")]

    service = BatchService(repository=_Repo(), storage=_DummyStorage())  # type: ignore[arg-type]
    auth = UserAPIKeyAuth(api_key="key-b", team_id="team-1")
    listed = await service.list_batches(auth=auth, limit=20)
    got = await service.get_batch(batch_id="b1", auth=auth)
    assert len(listed["data"]) == 1
    assert got["id"] == listed["data"][0]["id"]
