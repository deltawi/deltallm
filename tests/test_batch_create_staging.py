from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.batch.create import BatchCreateArtifactStorageBackend, BatchCreateStagedRequest, StagedBatchCreateArtifact
from src.batch.storage import BatchArtifactLineTooLongError, LocalBatchArtifactStorage, S3BatchArtifactStorage


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.modified_at: dict[tuple[str, str], datetime] = {}

    def upload_fileobj(self, fileobj, bucket: str, key: str, ExtraArgs=None) -> None:  # noqa: ANN001, N803
        del ExtraArgs
        self.objects[(bucket, key)] = fileobj.read()
        self.modified_at[(bucket, key)] = datetime.now(tz=UTC)

    def download_fileobj(self, bucket: str, key: str, fileobj) -> None:  # noqa: ANN001
        fileobj.write(self.objects[(bucket, key)])
        fileobj.seek(0)

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.objects.pop((Bucket, Key), None)
        self.modified_at.pop((Bucket, Key), None)

    def list_objects_v2(self, *, Bucket: str, Prefix: str, MaxKeys: int, ContinuationToken=None):  # noqa: ANN001, N803
        del ContinuationToken
        contents = []
        for (bucket, key), _payload in sorted(self.objects.items()):
            if bucket != Bucket or not key.startswith(Prefix):
                continue
            contents.append({"Key": key, "LastModified": self.modified_at[(bucket, key)]})
            if len(contents) >= MaxKeys:
                break
        return {"Contents": contents, "IsTruncated": False}


@pytest.mark.asyncio
async def test_batch_create_staging_round_trip_local(tmp_path: Path) -> None:
    backend = BatchCreateArtifactStorageBackend(
        storage=LocalBatchArtifactStorage(str(tmp_path / "batch-artifacts")),
    )

    artifact = await backend.write_records(
        [
            BatchCreateStagedRequest(line_number=1, custom_id="req-1", request_body={"model": "m1", "input": "a"}),
            BatchCreateStagedRequest(line_number=2, custom_id="req-2", request_body={"model": "m1", "input": "b"}),
        ],
        filename="session-1.jsonl",
    )

    records = [record async for record in backend.read_records(artifact)]

    assert artifact.storage_backend == "local"
    assert artifact.storage_key.startswith("batch-create-stage/")
    assert len(Path(artifact.storage_key).parts) == 5
    assert [record.custom_id for record in records] == ["req-1", "req-2"]
    assert records[0].request_body["input"] == "a"

    await backend.delete(artifact)

    assert not (tmp_path / "batch-artifacts" / artifact.storage_key).exists()


@pytest.mark.asyncio
async def test_batch_create_staging_round_trip_s3() -> None:
    client = _FakeS3Client()
    backend = BatchCreateArtifactStorageBackend(
        storage=S3BatchArtifactStorage(
            bucket="batch-bucket",
            prefix="prefix",
            client=client,
        )
    )

    artifact = await backend.write_records(
        [
            BatchCreateStagedRequest(line_number=1, custom_id="req-1", request_body={"model": "m1", "input": "a"}),
        ],
        filename="session-1.jsonl",
    )

    records = [record async for record in backend.read_records(artifact)]

    assert artifact.storage_backend == "s3"
    assert artifact.storage_key.startswith("prefix/batch-create-stage/")
    assert len(Path(artifact.storage_key).parts) == 6
    assert len(records) == 1
    assert records[0].custom_id == "req-1"

    await backend.delete(artifact)

    assert ("batch-bucket", artifact.storage_key) not in client.objects


@pytest.mark.asyncio
async def test_batch_create_staging_rejects_empty_artifact(tmp_path: Path) -> None:
    backend = BatchCreateArtifactStorageBackend(
        storage=LocalBatchArtifactStorage(str(tmp_path / "batch-artifacts")),
    )

    with pytest.raises(ValueError, match="empty"):
        await backend.write_records([], filename="empty.jsonl")


@pytest.mark.asyncio
async def test_batch_create_staging_rejects_invalid_json_line(tmp_path: Path) -> None:
    storage = LocalBatchArtifactStorage(str(tmp_path / "batch-artifacts"))
    storage_key, bytes_size, checksum = await storage.write_bytes(
        purpose="batch-create-stage",
        filename="invalid.jsonl",
        content=b'{"line_number":1,"custom_id":"req-1","request_body":{}\n',
    )
    backend = BatchCreateArtifactStorageBackend(storage=storage)

    with pytest.raises(ValueError, match="Invalid staged batch-create JSONL"):
        async for _record in backend.read_records(
            StagedBatchCreateArtifact(
                storage_backend="local",
                storage_key=storage_key,
                bytes_size=bytes_size,
                checksum=checksum,
            )
        ):
            pass


@pytest.mark.asyncio
async def test_batch_create_staging_rejects_oversized_record_on_write_without_leaking_local_artifact(
    tmp_path: Path,
) -> None:
    backend = BatchCreateArtifactStorageBackend(
        storage=LocalBatchArtifactStorage(str(tmp_path / "batch-artifacts")),
        max_line_bytes=32,
    )

    with pytest.raises(BatchArtifactLineTooLongError):
        await backend.write_records(
            [
                BatchCreateStagedRequest(
                    line_number=1,
                    custom_id="req-1",
                    request_body={"model": "m1", "input": "x" * 128},
                )
            ],
            filename="too-large.jsonl",
        )

    assert list((tmp_path / "batch-artifacts").rglob("*")) == []


@pytest.mark.asyncio
async def test_batch_create_staging_rejects_oversized_record_on_write_without_uploading_s3_artifact() -> None:
    client = _FakeS3Client()
    backend = BatchCreateArtifactStorageBackend(
        storage=S3BatchArtifactStorage(
            bucket="batch-bucket",
            prefix="prefix",
            client=client,
        ),
        max_line_bytes=32,
    )

    with pytest.raises(BatchArtifactLineTooLongError):
        await backend.write_records(
            [
                BatchCreateStagedRequest(
                    line_number=1,
                    custom_id="req-1",
                    request_body={"model": "m1", "input": "x" * 128},
                )
            ],
            filename="too-large.jsonl",
        )

    assert client.objects == {}


@pytest.mark.asyncio
async def test_batch_create_staging_lists_old_orphan_candidates_across_backends(tmp_path: Path) -> None:
    local_storage = LocalBatchArtifactStorage(str(tmp_path / "batch-artifacts"))
    s3_client = _FakeS3Client()
    s3_storage = S3BatchArtifactStorage(
        bucket="batch-bucket",
        prefix="prefix",
        client=s3_client,
    )
    backend = BatchCreateArtifactStorageBackend(
        storage=local_storage,
        storage_registry={"local": local_storage, "s3": s3_storage},
    )
    local_artifact = await backend.write_records(
        [BatchCreateStagedRequest(line_number=1, custom_id="req-local", request_body={"model": "m1", "input": "a"})],
        filename="local.jsonl",
    )
    s3_backend = BatchCreateArtifactStorageBackend(storage=s3_storage, storage_registry={"s3": s3_storage})
    s3_artifact = await s3_backend.write_records(
        [BatchCreateStagedRequest(line_number=1, custom_id="req-s3", request_body={"model": "m1", "input": "a"})],
        filename="s3.jsonl",
    )
    artifacts = await backend.list_orphan_candidates(
        older_than=datetime.now(tz=UTC) + timedelta(hours=1),
        limit=10,
    )

    keys = {(artifact.storage_backend, artifact.storage_key) for artifact in artifacts}
    assert ("local", local_artifact.storage_key) in keys
    assert ("s3", s3_artifact.storage_key) in keys


@pytest.mark.asyncio
async def test_batch_create_staging_orphan_selection_is_fair_across_backends(tmp_path: Path) -> None:
    local_storage = LocalBatchArtifactStorage(str(tmp_path / "batch-artifacts"))
    s3_client = _FakeS3Client()
    s3_storage = S3BatchArtifactStorage(
        bucket="batch-bucket",
        prefix="prefix",
        client=s3_client,
    )
    backend = BatchCreateArtifactStorageBackend(
        storage=local_storage,
        storage_registry={"local": local_storage, "s3": s3_storage},
    )

    await backend.write_records(
        [BatchCreateStagedRequest(line_number=1, custom_id="req-local-1", request_body={"model": "m1", "input": "a"})],
        filename="local-1.jsonl",
    )
    await backend.write_records(
        [BatchCreateStagedRequest(line_number=1, custom_id="req-local-2", request_body={"model": "m1", "input": "b"})],
        filename="local-2.jsonl",
    )
    s3_backend = BatchCreateArtifactStorageBackend(storage=s3_storage, storage_registry={"s3": s3_storage})
    s3_artifact = await s3_backend.write_records(
        [BatchCreateStagedRequest(line_number=1, custom_id="req-s3", request_body={"model": "m1", "input": "c"})],
        filename="s3.jsonl",
    )
    s3_client.modified_at[("batch-bucket", s3_artifact.storage_key)] = datetime.now(tz=UTC) - timedelta(hours=1)

    artifacts = await backend.list_orphan_candidates(
        older_than=datetime.now(tz=UTC) + timedelta(hours=1),
        limit=2,
    )

    assert len(artifacts) == 2
    assert {artifact.storage_backend for artifact in artifacts} == {"local", "s3"}


@pytest.mark.asyncio
async def test_batch_create_staging_orphan_selection_uses_full_limit_when_one_backend_is_empty(
    tmp_path: Path,
) -> None:
    local_storage = LocalBatchArtifactStorage(str(tmp_path / "batch-artifacts"))
    s3_client = _FakeS3Client()
    s3_storage = S3BatchArtifactStorage(
        bucket="batch-bucket",
        prefix="prefix",
        client=s3_client,
    )
    backend = BatchCreateArtifactStorageBackend(
        storage=local_storage,
        storage_registry={"local": local_storage, "s3": s3_storage},
    )
    expected_keys: set[str] = set()
    for index in range(3):
        artifact = await backend.write_records(
            [
                BatchCreateStagedRequest(
                    line_number=1,
                    custom_id=f"req-local-{index}",
                    request_body={"model": "m1", "input": str(index)},
                )
            ],
            filename=f"local-{index}.jsonl",
        )
        expected_keys.add(artifact.storage_key)

    artifacts = await backend.list_orphan_candidates(
        older_than=datetime.now(tz=UTC) + timedelta(hours=1),
        limit=3,
    )

    assert len(artifacts) == 3
    assert {artifact.storage_backend for artifact in artifacts} == {"local"}
    assert {artifact.storage_key for artifact in artifacts} == expected_keys
