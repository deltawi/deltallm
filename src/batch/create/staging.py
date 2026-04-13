from __future__ import annotations

from contextlib import aclosing
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator, Iterable, Protocol

from src.batch.create.models import BatchCreateSessionRecord, BatchCreateStagedRequest
from src.batch.storage import BatchArtifactLineTooLongError, BatchArtifactListEntry, BatchArtifactStorage
from src.metrics import increment_batch_artifact_failure, increment_batch_create_session_action

logger = logging.getLogger(__name__)

_DEFAULT_STAGE_PURPOSE = "batch-create-stage"


@dataclass
class StagedBatchCreateArtifact:
    storage_backend: str
    storage_key: str
    bytes_size: int
    checksum: str | None = None


def staged_artifact_from_session(session: BatchCreateSessionRecord) -> StagedBatchCreateArtifact:
    return StagedBatchCreateArtifact(
        storage_backend=session.staged_storage_backend,
        storage_key=session.staged_storage_key,
        bytes_size=session.staged_bytes,
        checksum=session.staged_checksum,
    )


class BatchCreateStagingBackend(Protocol):
    async def write_records(
        self,
        records: Iterable[BatchCreateStagedRequest] | AsyncIterator[BatchCreateStagedRequest],
        *,
        filename: str,
    ) -> StagedBatchCreateArtifact:
        ...

    def read_records(self, artifact: StagedBatchCreateArtifact) -> AsyncIterator[BatchCreateStagedRequest]:
        ...

    async def delete(self, artifact: StagedBatchCreateArtifact) -> None:
        ...

    async def list_orphan_candidates(
        self,
        *,
        older_than: datetime,
        limit: int,
    ) -> list[StagedBatchCreateArtifact]:
        ...


class BatchCreateArtifactStorageBackend:
    def __init__(
        self,
        *,
        storage: BatchArtifactStorage,
        storage_registry: dict[str, BatchArtifactStorage] | None = None,
        stage_purpose: str = _DEFAULT_STAGE_PURPOSE,
        chunk_size: int = 65_536,
        max_line_bytes: int = 1_048_576,
    ) -> None:
        self.storage = storage
        active_backend = str(getattr(storage, "backend_name", "local") or "local").strip().lower()
        self.storage_registry = {
            str(key).strip().lower(): value
            for key, value in (storage_registry or {}).items()
        }
        self.storage_registry.setdefault(active_backend, storage)
        self.stage_purpose = stage_purpose
        self.chunk_size = chunk_size
        self.max_line_bytes = max_line_bytes

    def _storage_for_backend(self, backend: str | None) -> BatchArtifactStorage:
        normalized = str(backend or getattr(self.storage, "backend_name", "local") or "local").strip().lower()
        selected = self.storage_registry.get(normalized)
        if selected is None:
            raise RuntimeError(
                f"Storage backend '{normalized}' is unavailable; keep batch create-stage storage configured until sessions expire"
            )
        return selected

    async def write_records(
        self,
        records: Iterable[BatchCreateStagedRequest] | AsyncIterator[BatchCreateStagedRequest],
        *,
        filename: str,
    ) -> StagedBatchCreateArtifact:
        async def _lines() -> AsyncIterator[str]:
            def _serialize(record: BatchCreateStagedRequest) -> str:
                line = json.dumps(record.to_jsonable(), separators=(",", ":"), ensure_ascii=True)
                if len(line.encode("utf-8")) > self.max_line_bytes:
                    raise BatchArtifactLineTooLongError(line_number=record.line_number, max_line_bytes=self.max_line_bytes)
                return line

            if hasattr(records, "__aiter__"):
                async for record in records:  # type: ignore[union-attr]
                    yield _serialize(record)
                return
            for record in records:  # type: ignore[not-an-iterable]
                yield _serialize(record)

        backend_name = str(getattr(self.storage, "backend_name", "local") or "local")
        try:
            storage_key, bytes_size, checksum = await self.storage.write_lines_stream(
                purpose=self.stage_purpose,
                filename=filename,
                lines=_lines(),
            )
        except Exception:
            increment_batch_artifact_failure(operation="write", backend=backend_name)
            increment_batch_create_session_action(action="stage_write", status="error")
            logger.warning("batch create-stage artifact write failed backend=%s filename=%s", backend_name, filename, exc_info=True)
            raise
        if bytes_size <= 0 or not storage_key:
            increment_batch_create_session_action(action="stage_write", status="error")
            raise ValueError("staged batch-create artifact is empty")
        increment_batch_create_session_action(action="stage_write", status="success")
        return StagedBatchCreateArtifact(
            storage_backend=backend_name,
            storage_key=storage_key,
            bytes_size=bytes_size,
            checksum=checksum,
        )

    async def _iter_records(self, artifact: StagedBatchCreateArtifact) -> AsyncIterator[BatchCreateStagedRequest]:
        storage = self._storage_for_backend(artifact.storage_backend)
        async with aclosing(
            storage.iter_lines(
                artifact.storage_key,
                chunk_size=self.chunk_size,
                max_line_bytes=self.max_line_bytes,
            )
        ) as lines:
            async for raw_line in lines:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid staged batch-create JSONL: {exc.msg}") from exc
                yield BatchCreateStagedRequest.from_jsonable(payload)

    def read_records(self, artifact: StagedBatchCreateArtifact) -> AsyncIterator[BatchCreateStagedRequest]:
        return self._iter_records(artifact)

    async def delete(self, artifact: StagedBatchCreateArtifact) -> None:
        storage = self._storage_for_backend(artifact.storage_backend)
        try:
            await storage.delete(artifact.storage_key)
        except Exception:
            increment_batch_artifact_failure(
                operation="delete",
                backend=str(artifact.storage_backend or getattr(storage, "backend_name", "unknown")),
            )
            increment_batch_create_session_action(action="stage_delete", status="error")
            logger.warning(
                "batch create-stage artifact delete failed backend=%s storage_key=%s",
                artifact.storage_backend,
                artifact.storage_key,
                exc_info=True,
            )
            raise
        increment_batch_create_session_action(action="stage_delete", status="success")

    async def list_orphan_candidates(
        self,
        *,
        older_than: datetime,
        limit: int,
    ) -> list[StagedBatchCreateArtifact]:
        bounded_limit = max(1, int(limit))
        if not self.storage_registry:
            return []

        candidates: list[tuple[datetime, str, BatchArtifactListEntry]] = []
        for backend_name, storage in self.storage_registry.items():
            for entry in await storage.list_entries(
                prefix=self.stage_purpose,
                older_than=older_than,
                limit=bounded_limit,
            ):
                candidates.append((entry.modified_at, backend_name, entry))

        candidates.sort(key=lambda item: (item[0], item[1], item[2].storage_key))
        return [
            StagedBatchCreateArtifact(
                storage_backend=backend_name,
                storage_key=entry.storage_key,
                bytes_size=0,
                checksum=None,
            )
            for _, backend_name, entry in candidates[:bounded_limit]
        ]
