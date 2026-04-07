from __future__ import annotations

from pathlib import Path

import pytest

from src.batch.storage import LocalBatchArtifactStorage, S3BatchArtifactStorage


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


class _FakeS3Body:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return self.payload


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, Metadata=None) -> None:  # noqa: ANN001
        del Metadata
        self.objects[(Bucket, Key)] = Body

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        return {"Body": _FakeS3Body(self.objects[(Bucket, Key)])}

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
