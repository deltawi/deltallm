from __future__ import annotations

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

    def upload_fileobj(self, fileobj, bucket: str, key: str, ExtraArgs=None) -> None:  # noqa: ANN001, N803
        del ExtraArgs
        self.objects[(bucket, key)] = fileobj.read()

    def download_fileobj(self, bucket: str, key: str, fileobj) -> None:  # noqa: ANN001
        fileobj.write(self.objects[(bucket, key)])
        fileobj.seek(0)

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.objects.pop((Bucket, Key), None)


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
