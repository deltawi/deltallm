from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Iterable
from uuid import uuid4

try:
    import boto3

    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False
    boto3 = None  # type: ignore[assignment]


class BatchArtifactStorage:
    backend_name = "unknown"

    async def write_bytes(self, *, purpose: str, filename: str, content: bytes) -> tuple[str, int, str]:
        raise NotImplementedError

    async def read_bytes(self, storage_key: str) -> bytes:
        raise NotImplementedError

    async def delete(self, storage_key: str) -> None:
        raise NotImplementedError

    async def write_lines(self, *, purpose: str, filename: str, lines: Iterable[str]) -> tuple[str, int, str]:
        payload = ("\n".join(lines) + "\n").encode("utf-8")
        return await self.write_bytes(purpose=purpose, filename=filename, content=payload)


class LocalBatchArtifactStorage(BatchArtifactStorage):
    backend_name = "local"

    def __init__(self, base_dir: str) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def write_bytes(self, *, purpose: str, filename: str, content: bytes) -> tuple[str, int, str]:
        safe_name = filename.replace("/", "_")
        storage_key = f"{purpose}/{uuid4().hex}-{safe_name}"
        target = self.base_dir / storage_key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        checksum = hashlib.sha256(content).hexdigest()
        return storage_key, len(content), checksum

    async def read_bytes(self, storage_key: str) -> bytes:
        target = self.base_dir / storage_key
        return target.read_bytes()

    async def delete(self, storage_key: str) -> None:
        target = self.base_dir / storage_key
        if target.exists():
            target.unlink()


class S3BatchArtifactStorage(BatchArtifactStorage):
    backend_name = "s3"

    def __init__(
        self,
        *,
        bucket: str,
        region: str = "us-east-1",
        prefix: str = "deltallm/batch-artifacts",
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        client: object | None = None,
    ) -> None:
        if not bucket:
            raise ValueError("S3 batch storage bucket is required")
        if client is None and not BOTO3_AVAILABLE:
            raise ImportError("boto3 package required for S3 batch storage. Install the 'batch-s3' extra.")
        self.bucket = bucket
        self.region = region
        self.prefix = prefix.strip("/")
        self.endpoint_url = endpoint_url
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self._client = client

    @property
    def s3(self):
        if self._client is None:
            kwargs: dict[str, str] = {"region_name": self.region}
            if self.endpoint_url:
                kwargs["endpoint_url"] = self.endpoint_url
            if self.access_key_id:
                kwargs["aws_access_key_id"] = self.access_key_id
            if self.secret_access_key:
                kwargs["aws_secret_access_key"] = self.secret_access_key
            self._client = boto3.client("s3", **kwargs)
        return self._client

    def _storage_key(self, *, purpose: str, filename: str) -> str:
        safe_name = filename.replace("/", "_")
        relative = f"{purpose}/{uuid4().hex}-{safe_name}"
        return f"{self.prefix}/{relative}" if self.prefix else relative

    async def write_bytes(self, *, purpose: str, filename: str, content: bytes) -> tuple[str, int, str]:
        storage_key = self._storage_key(purpose=purpose, filename=filename)
        checksum = hashlib.sha256(content).hexdigest()
        await asyncio.to_thread(
            self.s3.put_object,
            Bucket=self.bucket,
            Key=storage_key,
            Body=content,
            Metadata={"sha256": checksum, "purpose": purpose, "filename": filename},
        )
        return storage_key, len(content), checksum

    async def read_bytes(self, storage_key: str) -> bytes:
        def _read() -> bytes:
            response = self.s3.get_object(Bucket=self.bucket, Key=storage_key)
            return response["Body"].read()

        return await asyncio.to_thread(_read)

    async def delete(self, storage_key: str) -> None:
        await asyncio.to_thread(self.s3.delete_object, Bucket=self.bucket, Key=storage_key)
