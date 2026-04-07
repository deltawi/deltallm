from __future__ import annotations

import asyncio
import contextlib
import hashlib
import tempfile
from pathlib import Path
from typing import AsyncIterable, AsyncIterator, Iterable
from uuid import uuid4

try:
    import boto3

    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False
    boto3 = None  # type: ignore[assignment]


class BatchArtifactLineTooLongError(ValueError):
    def __init__(self, *, line_number: int, max_line_bytes: int) -> None:
        super().__init__(f"Line {line_number} exceeds max_line_bytes={max_line_bytes}")
        self.line_number = line_number
        self.max_line_bytes = max_line_bytes


class BatchArtifactStorage:
    backend_name = "unknown"

    async def write_chunks(
        self,
        *,
        purpose: str,
        filename: str,
        chunks: AsyncIterable[bytes],
    ) -> tuple[str, int, str]:
        raise NotImplementedError

    async def iter_bytes(self, storage_key: str, chunk_size: int = 65_536) -> AsyncIterator[bytes]:
        raise NotImplementedError

    async def delete(self, storage_key: str) -> None:
        raise NotImplementedError

    async def write_bytes(self, *, purpose: str, filename: str, content: bytes) -> tuple[str, int, str]:
        async def _chunks() -> AsyncIterator[bytes]:
            if content:
                yield content

        return await self.write_chunks(purpose=purpose, filename=filename, chunks=_chunks())

    async def read_bytes(self, storage_key: str) -> bytes:
        parts: list[bytes] = []
        async for chunk in self.iter_bytes(storage_key):
            parts.append(chunk)
        return b"".join(parts)

    async def iter_lines(
        self,
        storage_key: str,
        chunk_size: int = 65_536,
        max_line_bytes: int | None = None,
    ) -> AsyncIterator[str]:
        buffer = b""
        line_number = 1
        async for chunk in self.iter_bytes(storage_key, chunk_size=chunk_size):
            buffer += chunk
            while True:
                newline = buffer.find(b"\n")
                if newline == -1:
                    break
                raw_line = buffer[:newline]
                if max_line_bytes is not None and len(raw_line) > max_line_bytes:
                    raise BatchArtifactLineTooLongError(line_number=line_number, max_line_bytes=max_line_bytes)
                buffer = buffer[newline + 1 :]
                yield raw_line.decode("utf-8")
                line_number += 1
            if max_line_bytes is not None and len(buffer) > max_line_bytes:
                raise BatchArtifactLineTooLongError(line_number=line_number, max_line_bytes=max_line_bytes)
        if buffer:
            yield buffer.decode("utf-8")

    async def write_lines(self, *, purpose: str, filename: str, lines: Iterable[str]) -> tuple[str, int, str]:
        async def _chunks() -> AsyncIterator[bytes]:
            for line in lines:
                yield (line + "\n").encode("utf-8")

        return await self.write_chunks(purpose=purpose, filename=filename, chunks=_chunks())

    async def write_lines_stream(
        self,
        *,
        purpose: str,
        filename: str,
        lines: AsyncIterable[str],
    ) -> tuple[str, int, str]:
        async def _chunks() -> AsyncIterator[bytes]:
            async for line in lines:
                yield (line + "\n").encode("utf-8")

        return await self.write_chunks(purpose=purpose, filename=filename, chunks=_chunks())


class LocalBatchArtifactStorage(BatchArtifactStorage):
    backend_name = "local"

    def __init__(self, base_dir: str) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _build_target(self, *, purpose: str, filename: str) -> tuple[str, Path]:
        safe_name = filename.replace("/", "_")
        storage_key = f"{purpose}/{uuid4().hex}-{safe_name}"
        return storage_key, self.base_dir / storage_key

    async def write_chunks(
        self,
        *,
        purpose: str,
        filename: str,
        chunks: AsyncIterable[bytes],
    ) -> tuple[str, int, str]:
        storage_key, target = self._build_target(purpose=purpose, filename=filename)
        checksum = hashlib.sha256()
        total_bytes = 0
        temp_target: Path | None = None
        handle = None
        try:
            async for chunk in chunks:
                if not chunk:
                    continue
                if handle is None:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temp_target = target.parent / f".tmp-{uuid4().hex}-{target.name}"
                    handle = temp_target.open("wb")
                total_bytes += len(chunk)
                checksum.update(chunk)
                await asyncio.to_thread(handle.write, chunk)
            if handle is None:
                return "", 0, checksum.hexdigest()
            await asyncio.to_thread(handle.flush)
            await asyncio.to_thread(handle.close)
            await asyncio.to_thread(temp_target.replace, target)
        except Exception:
            with contextlib.suppress(Exception):
                if handle is not None:
                    await asyncio.to_thread(handle.close)
            if temp_target is not None:
                with contextlib.suppress(FileNotFoundError):
                    await asyncio.to_thread(temp_target.unlink)
            raise
        return storage_key, total_bytes, checksum.hexdigest()

    async def iter_bytes(self, storage_key: str, chunk_size: int = 65_536) -> AsyncIterator[bytes]:
        target = self.base_dir / storage_key
        handle = target.open("rb")
        try:
            while True:
                chunk = await asyncio.to_thread(handle.read, chunk_size)
                if not chunk:
                    break
                yield chunk
        finally:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(handle.close)

    async def delete(self, storage_key: str) -> None:
        target = self.base_dir / storage_key
        if target.exists():
            await asyncio.to_thread(target.unlink)


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
        spool_max_bytes: int = 8_388_608,
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
        self.spool_max_bytes = spool_max_bytes
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

    async def write_chunks(
        self,
        *,
        purpose: str,
        filename: str,
        chunks: AsyncIterable[bytes],
    ) -> tuple[str, int, str]:
        storage_key = self._storage_key(purpose=purpose, filename=filename)
        checksum = hashlib.sha256()
        total_bytes = 0
        spool = tempfile.SpooledTemporaryFile(max_size=self.spool_max_bytes)
        try:
            async for chunk in chunks:
                if not chunk:
                    continue
                total_bytes += len(chunk)
                checksum.update(chunk)
                await asyncio.to_thread(spool.write, chunk)
            if total_bytes == 0:
                return "", 0, checksum.hexdigest()
            await asyncio.to_thread(spool.seek, 0)
            await asyncio.to_thread(
                self.s3.upload_fileobj,
                spool,
                self.bucket,
                storage_key,
                ExtraArgs={"Metadata": {"sha256": checksum.hexdigest(), "purpose": purpose, "filename": filename}},
            )
        finally:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(spool.close)
        return storage_key, total_bytes, checksum.hexdigest()

    async def iter_bytes(self, storage_key: str, chunk_size: int = 65_536) -> AsyncIterator[bytes]:
        spool = tempfile.SpooledTemporaryFile(max_size=self.spool_max_bytes)
        try:
            await asyncio.to_thread(self.s3.download_fileobj, self.bucket, storage_key, spool)
            await asyncio.to_thread(spool.seek, 0)
            while True:
                chunk = await asyncio.to_thread(spool.read, chunk_size)
                if not chunk:
                    break
                yield chunk
        finally:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(spool.close)

    async def delete(self, storage_key: str) -> None:
        await asyncio.to_thread(self.s3.delete_object, Bucket=self.bucket, Key=storage_key)
