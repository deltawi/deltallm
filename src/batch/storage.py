from __future__ import annotations

import asyncio
import contextlib
import hashlib
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
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


@dataclass(frozen=True)
class BatchArtifactListEntry:
    storage_key: str
    modified_at: datetime


def _safe_artifact_filename(filename: str) -> str:
    return filename.replace("/", "_")


def _build_sharded_storage_key(*, purpose: str, filename: str, now: datetime) -> str:
    timestamp = now.astimezone(UTC)
    return (
        f"{purpose}/{timestamp:%Y/%m/%d}/"
        f"{timestamp:%Y%m%dT%H%M%S%fZ}-{uuid4().hex}-{_safe_artifact_filename(filename)}"
    )


def _parse_sharded_filename_timestamp(filename: str) -> datetime | None:
    prefix, _separator, _remainder = str(filename or "").partition("-")
    if not prefix:
        return None
    try:
        return datetime.strptime(prefix, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def _parse_sharded_day(day_dir: Path) -> date | None:
    try:
        year = int(day_dir.parent.parent.name)
        month = int(day_dir.parent.name)
        day = int(day_dir.name)
        return date(year, month, day)
    except (TypeError, ValueError):
        return None


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

    async def list_entries(
        self,
        *,
        prefix: str,
        older_than: datetime,
        limit: int,
    ) -> list[BatchArtifactListEntry]:
        raise NotImplementedError

    async def list_keys(
        self,
        *,
        prefix: str,
        older_than: datetime,
        limit: int,
    ) -> list[str]:
        return [
            entry.storage_key
            for entry in await self.list_entries(
                prefix=prefix,
                older_than=older_than,
                limit=limit,
            )
        ]

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
        storage_key = _build_sharded_storage_key(
            purpose=purpose,
            filename=filename,
            now=datetime.now(tz=UTC),
        )
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

    async def list_entries(
        self,
        *,
        prefix: str,
        older_than: datetime,
        limit: int,
    ) -> list[BatchArtifactListEntry]:
        normalized_prefix = str(prefix or "").strip("/")
        cutoff = older_than.astimezone(UTC)
        bounded_limit = max(1, int(limit))

        def _scan_day(day_dir: Path, *, remaining: int) -> list[BatchArtifactListEntry]:
            matches: list[BatchArtifactListEntry] = []
            for path in sorted(day_dir.iterdir()):
                if path.name.startswith(".tmp-") or path.suffix != ".jsonl":
                    continue
                modified_at = _parse_sharded_filename_timestamp(path.name)
                if modified_at is not None:
                    if modified_at >= cutoff:
                        break
                else:
                    try:
                        stat = path.stat()
                    except FileNotFoundError:
                        continue
                    modified_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
                    if modified_at >= cutoff:
                        continue
                matches.append(
                    BatchArtifactListEntry(
                        storage_key=str(path.relative_to(self.base_dir).as_posix()),
                        modified_at=modified_at,
                    )
                )
                if len(matches) >= remaining:
                    break
            matches.sort(key=lambda entry: (entry.modified_at, entry.storage_key))
            return matches[:remaining]

        def _scan_legacy(root: Path, *, remaining: int) -> list[BatchArtifactListEntry]:
            matches: list[BatchArtifactListEntry] = []
            for path in sorted(root.iterdir()):
                if not path.is_file():
                    continue
                relative_path = path.relative_to(self.base_dir)
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    continue
                modified_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
                if modified_at >= cutoff:
                    continue
                matches.append(
                    BatchArtifactListEntry(
                        storage_key=str(relative_path.as_posix()),
                        modified_at=modified_at,
                    )
                )
            matches.sort(key=lambda entry: (entry.modified_at, entry.storage_key))
            return matches[:remaining]

        def _scan() -> list[BatchArtifactListEntry]:
            root = self.base_dir / normalized_prefix if normalized_prefix else self.base_dir
            if not root.exists():
                return []
            entries: list[BatchArtifactListEntry] = []
            should_scan_legacy = True
            for year_dir in sorted(root.iterdir()):
                if not year_dir.is_dir():
                    continue
                for month_dir in sorted(year_dir.iterdir()):
                    if not month_dir.is_dir():
                        continue
                    for day_dir in sorted(month_dir.iterdir()):
                        if not day_dir.is_dir():
                            continue
                        day_value = _parse_sharded_day(day_dir)
                        if day_value is None:
                            continue
                        if day_value > cutoff.date():
                            should_scan_legacy = True
                            break
                        remaining = bounded_limit - len(entries)
                        if remaining <= 0:
                            return entries[:bounded_limit]
                        entries.extend(_scan_day(day_dir, remaining=remaining))
                        if len(entries) >= bounded_limit:
                            return entries[:bounded_limit]
                    else:
                        continue
                    break
                else:
                    continue
                break
            if should_scan_legacy and len(entries) < bounded_limit:
                entries.extend(_scan_legacy(root, remaining=bounded_limit - len(entries)))
            return entries[:bounded_limit]

        return await asyncio.to_thread(_scan)

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
        relative = _build_sharded_storage_key(
            purpose=purpose,
            filename=filename,
            now=datetime.now(tz=UTC),
        )
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

    async def list_entries(
        self,
        *,
        prefix: str,
        older_than: datetime,
        limit: int,
    ) -> list[BatchArtifactListEntry]:
        bounded_limit = max(1, int(limit))
        cutoff = older_than.astimezone(UTC)
        normalized_prefix = str(prefix or "").strip("/")
        effective_prefix = normalized_prefix
        if self.prefix:
            effective_prefix = (
                normalized_prefix
                if normalized_prefix.startswith(f"{self.prefix}/") or normalized_prefix == self.prefix
                else f"{self.prefix}/{normalized_prefix}" if normalized_prefix else self.prefix
            )
        continuation_token: str | None = None
        matches: list[BatchArtifactListEntry] = []

        while len(matches) < bounded_limit:
            kwargs: dict[str, object] = {
                "Bucket": self.bucket,
                "Prefix": effective_prefix,
                "MaxKeys": bounded_limit,
            }
            if continuation_token:
                kwargs["ContinuationToken"] = continuation_token
            response = await asyncio.to_thread(self.s3.list_objects_v2, **kwargs)
            contents = list(response.get("Contents") or [])
            for entry in contents:
                key = str(entry.get("Key") or "")
                if not key:
                    continue
                last_modified = entry.get("LastModified")
                if isinstance(last_modified, datetime):
                    modified_at = last_modified.astimezone(UTC)
                elif isinstance(last_modified, str):
                    modified_at = datetime.fromisoformat(last_modified.replace("Z", "+00:00")).astimezone(UTC)
                else:
                    continue
                if modified_at >= cutoff:
                    continue
                matches.append(BatchArtifactListEntry(storage_key=key, modified_at=modified_at))
                if len(matches) >= bounded_limit:
                    break
            if len(matches) >= bounded_limit or not response.get("IsTruncated"):
                break
            continuation_token = str(response.get("NextContinuationToken") or "")
            if not continuation_token:
                break

        matches.sort(key=lambda entry: (entry.modified_at, entry.storage_key))
        return matches[:bounded_limit]

    async def delete(self, storage_key: str) -> None:
        await asyncio.to_thread(self.s3.delete_object, Bucket=self.bucket, Key=storage_key)
