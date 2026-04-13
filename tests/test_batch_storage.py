from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
from pathlib import Path

import pytest

from src.batch.storage import BatchArtifactLineTooLongError, LocalBatchArtifactStorage, S3BatchArtifactStorage


@pytest.mark.asyncio
async def test_local_batch_storage_round_trip(tmp_path: Path) -> None:
    storage = LocalBatchArtifactStorage(str(tmp_path / "batch-artifacts"))

    storage_key, size, checksum = await storage.write_bytes(
        purpose="batch",
        filename="input.jsonl",
        content=b'{"custom_id":"1"}\n',
    )

    assert len(Path(storage_key).parts) == 5
    assert size > 0
    assert checksum
    assert await storage.read_bytes(storage_key) == b'{"custom_id":"1"}\n'

    await storage.delete(storage_key)

    assert not (tmp_path / "batch-artifacts" / storage_key).exists()


@pytest.mark.asyncio
async def test_local_batch_storage_skips_persisting_empty_write(tmp_path: Path) -> None:
    base_dir = tmp_path / "batch-artifacts"
    storage = LocalBatchArtifactStorage(str(base_dir))

    storage_key, size, checksum = await storage.write_bytes(
        purpose="batch",
        filename="empty.jsonl",
        content=b"",
    )

    assert storage_key == ""
    assert size == 0
    assert checksum
    assert list(base_dir.rglob("*")) == []


class _FakeS3Body:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return self.payload


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
async def test_s3_batch_storage_round_trip() -> None:
    client = _FakeS3Client()
    storage = S3BatchArtifactStorage(
        bucket="batch-bucket",
        prefix="prefix",
        client=client,
    )

    storage_key, size, checksum = await storage.write_bytes(
        purpose="batch_output",
        filename="out.jsonl",
        content=b'{"custom_id":"1","response":{}}\n',
    )

    assert len(Path(storage_key).parts) == 6
    assert storage_key.startswith("prefix/batch_output/")
    assert size > 0
    assert checksum
    assert await storage.read_bytes(storage_key) == b'{"custom_id":"1","response":{}}\n'

    await storage.delete(storage_key)

    assert ("batch-bucket", storage_key) not in client.objects


@pytest.mark.asyncio
async def test_s3_batch_storage_skips_uploading_empty_write() -> None:
    client = _FakeS3Client()
    storage = S3BatchArtifactStorage(
        bucket="batch-bucket",
        prefix="prefix",
        client=client,
    )

    storage_key, size, checksum = await storage.write_bytes(
        purpose="batch_output",
        filename="empty.jsonl",
        content=b"",
    )

    assert storage_key == ""
    assert size == 0
    assert checksum
    assert client.objects == {}


@pytest.mark.asyncio
async def test_storage_write_lines_stream_and_iter_lines_round_trip(tmp_path: Path) -> None:
    storage = LocalBatchArtifactStorage(str(tmp_path / "batch-artifacts"))

    async def _lines():
        yield '{"custom_id":"1"}'
        yield '{"custom_id":"2"}'

    storage_key, size, checksum = await storage.write_lines_stream(
        purpose="batch_output",
        filename="out.jsonl",
        lines=_lines(),
    )

    assert size > 0
    assert checksum

    lines = [line async for line in storage.iter_lines(storage_key)]

    assert lines == ['{"custom_id":"1"}', '{"custom_id":"2"}']


@pytest.mark.asyncio
async def test_iter_lines_raises_before_unbounded_oversized_line_buffer(tmp_path: Path) -> None:
    storage = LocalBatchArtifactStorage(str(tmp_path / "batch-artifacts"))
    storage_key, _, _ = await storage.write_bytes(
        purpose="batch",
        filename="input.jsonl",
        content=b"a" * 32,
    )

    with pytest.raises(BatchArtifactLineTooLongError) as exc:
        async for _line in storage.iter_lines(storage_key, chunk_size=8, max_line_bytes=16):
            pass

    assert exc.value.line_number == 1


@pytest.mark.asyncio
async def test_local_batch_storage_list_keys_filters_by_prefix_and_age(tmp_path: Path) -> None:
    storage = LocalBatchArtifactStorage(str(tmp_path / "batch-artifacts"))
    older_key = "batch-create-stage/2026/04/13/20260413T010000000000Z-a-older.jsonl"
    newer_key = "batch-create-stage/2026/04/13/20260413T120000000000Z-b-newer.jsonl"
    older_target = tmp_path / "batch-artifacts" / older_key
    newer_target = tmp_path / "batch-artifacts" / newer_key
    older_target.parent.mkdir(parents=True, exist_ok=True)
    older_target.write_bytes(b"older\n")
    newer_target.write_bytes(b"newer\n")

    keys = await storage.list_keys(
        prefix="batch-create-stage",
        older_than=datetime(2026, 4, 13, 3, 0, tzinfo=UTC),
        limit=10,
    )

    assert older_key in keys
    assert newer_key not in keys


@pytest.mark.asyncio
async def test_local_batch_storage_list_keys_returns_oldest_entries_first(tmp_path: Path) -> None:
    storage = LocalBatchArtifactStorage(str(tmp_path / "batch-artifacts"))
    oldest_key = "batch-create-stage/2026/04/13/20260413T010000000000Z-a-oldest.jsonl"
    newer_key = "batch-create-stage/2026/04/13/20260413T020000000000Z-b-newer.jsonl"
    oldest_target = tmp_path / "batch-artifacts" / oldest_key
    newer_target = tmp_path / "batch-artifacts" / newer_key
    oldest_target.parent.mkdir(parents=True, exist_ok=True)
    oldest_target.write_bytes(b"oldest\n")
    newer_target.write_bytes(b"newer\n")

    keys = await storage.list_keys(
        prefix="batch-create-stage",
        older_than=datetime(2026, 4, 13, 3, 0, tzinfo=UTC),
        limit=1,
    )

    assert keys == [oldest_key]


@pytest.mark.asyncio
async def test_local_batch_storage_list_keys_uses_sharded_filenames_without_statting_each_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = LocalBatchArtifactStorage(str(tmp_path / "batch-artifacts"))
    day_dir = tmp_path / "batch-artifacts" / "batch-create-stage" / "2026" / "04" / "13"
    day_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "20260413T010000000000Z-a-oldest.jsonl",
        "20260413T020000000000Z-b-older.jsonl",
        "20260413T120000000000Z-c-newer.jsonl",
    ):
        (day_dir / name).write_bytes(b"line\n")

    original_stat = Path.stat

    def _stat(self: Path, *args, **kwargs):  # noqa: ANN001
        if self.name.endswith(".jsonl"):
            raise AssertionError("sharded file listing should not stat each file")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", _stat)

    keys = await storage.list_keys(
        prefix="batch-create-stage",
        older_than=datetime(2026, 4, 13, 3, 0, tzinfo=UTC),
        limit=2,
    )

    assert keys == [
        "batch-create-stage/2026/04/13/20260413T010000000000Z-a-oldest.jsonl",
        "batch-create-stage/2026/04/13/20260413T020000000000Z-b-older.jsonl",
    ]


@pytest.mark.asyncio
async def test_local_batch_storage_list_keys_keeps_legacy_flat_files_visible_after_sharded_rollover(
    tmp_path: Path,
) -> None:
    storage = LocalBatchArtifactStorage(str(tmp_path / "batch-artifacts"))
    legacy_target = tmp_path / "batch-artifacts" / "batch-create-stage" / "legacy.jsonl"
    legacy_target.parent.mkdir(parents=True, exist_ok=True)
    legacy_target.write_bytes(b"legacy\n")
    old_time = datetime.now(tz=UTC) - timedelta(days=2)
    os.utime(legacy_target, (old_time.timestamp(), old_time.timestamp()))

    current_artifact_key = "batch-create-stage/2026/04/13/20260413T120000000000Z-a-current.jsonl"
    current_target = tmp_path / "batch-artifacts" / current_artifact_key
    current_target.parent.mkdir(parents=True, exist_ok=True)
    current_target.write_bytes(b"current\n")

    keys = await storage.list_keys(
        prefix="batch-create-stage",
        older_than=datetime(2026, 4, 12, 23, 0, tzinfo=UTC),
        limit=10,
    )

    assert "batch-create-stage/legacy.jsonl" in keys
    assert current_artifact_key not in keys


@pytest.mark.asyncio
async def test_s3_batch_storage_list_keys_filters_by_prefix_and_age() -> None:
    client = _FakeS3Client()
    storage = S3BatchArtifactStorage(
        bucket="batch-bucket",
        prefix="prefix",
        client=client,
    )
    old_key, _, _ = await storage.write_bytes(
        purpose="batch-create-stage",
        filename="older.jsonl",
        content=b"older\n",
    )
    new_key, _, _ = await storage.write_bytes(
        purpose="batch-create-stage",
        filename="newer.jsonl",
        content=b"newer\n",
    )
    client.modified_at[("batch-bucket", old_key)] = datetime.now(tz=UTC) - timedelta(hours=2)

    keys = await storage.list_keys(
        prefix="batch-create-stage",
        older_than=datetime.now(tz=UTC) - timedelta(hours=1),
        limit=10,
    )

    assert old_key in keys
    assert new_key not in keys


@pytest.mark.asyncio
async def test_s3_batch_storage_list_keys_returns_oldest_entries_first() -> None:
    client = _FakeS3Client()
    storage = S3BatchArtifactStorage(
        bucket="batch-bucket",
        prefix="prefix",
        client=client,
    )
    oldest_key, _, _ = await storage.write_bytes(
        purpose="batch-create-stage",
        filename="oldest.jsonl",
        content=b"oldest\n",
    )
    newer_key, _, _ = await storage.write_bytes(
        purpose="batch-create-stage",
        filename="newer.jsonl",
        content=b"newer\n",
    )
    client.modified_at[("batch-bucket", oldest_key)] = datetime.now(tz=UTC) - timedelta(hours=3)
    client.modified_at[("batch-bucket", newer_key)] = datetime.now(tz=UTC) - timedelta(hours=2)

    keys = await storage.list_keys(
        prefix="batch-create-stage",
        older_than=datetime.now(tz=UTC) - timedelta(hours=1),
        limit=1,
    )

    assert keys == [oldest_key]
