from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from src.batch.models import BatchFileRecord, BatchJobRecord, BatchJobStatus
from src.batch.service import BatchService
from src.models.responses import UserAPIKeyAuth


class _DummyRepo:
    pass


class _DummyStorage:
    async def read_bytes(self, storage_key: str) -> bytes:
        del storage_key
        return b"{}"


def _service() -> BatchService:
    return BatchService(repository=_DummyRepo(), storage=_DummyStorage())  # type: ignore[arg-type]


def test_parse_input_jsonl_accepts_embeddings_lines() -> None:
    service = _service()
    auth = UserAPIKeyAuth(api_key="sk-test", models=["text-embedding-3-small"])
    payload = b'{"custom_id":"item-1","url":"/v1/embeddings","body":{"model":"text-embedding-3-small","input":"hello"}}\n'
    items, model = service._parse_input_jsonl(payload, endpoint="/v1/embeddings", auth=auth)
    assert len(items) == 1
    assert model == "text-embedding-3-small"


def test_parse_input_jsonl_rejects_duplicate_custom_id() -> None:
    service = _service()
    auth = UserAPIKeyAuth(api_key="sk-test")
    payload = (
        b'{"custom_id":"dup","url":"/v1/embeddings","body":{"model":"text-embedding-3-small","input":"a"}}\n'
        b'{"custom_id":"dup","url":"/v1/embeddings","body":{"model":"text-embedding-3-small","input":"b"}}\n'
    )
    with pytest.raises(HTTPException) as exc:
        service._parse_input_jsonl(payload, endpoint="/v1/embeddings", auth=auth)
    assert exc.value.status_code == 400


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
