from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, AsyncIterator

from fastapi import HTTPException, UploadFile, status

from src.batch.access import can_access_owned_resource
from src.batch.models import BatchItemCreate, BatchJobStatus, OPENAI_BATCH_COMPLETION_WINDOW
from src.batch.request_validation import parse_batch_input_line
from src.batch.repository import BatchRepository
from src.batch.storage import BatchArtifactLineTooLongError, BatchArtifactStorage
from src.metrics import (
    increment_batch_artifact_failure,
    publish_batch_runtime_summary,
)
from src.models.responses import UserAPIKeyAuth
from src.services.callable_target_grants import CallableTargetGrantService
from src.services.model_visibility import CallableTargetPolicyMode

if TYPE_CHECKING:
    from src.batch.create import BatchCreateSessionService

logger = logging.getLogger(__name__)

OPENAI_BATCH_STATUS_VALUES = frozenset(
    {
        "validating",
        "failed",
        "in_progress",
        "finalizing",
        "completed",
        "expired",
        "cancelling",
        "cancelled",
    }
)


@dataclass
class BatchCreateResponseResult:
    response: dict[str, Any]
    audit_metadata: dict[str, Any]


class BatchService:
    def __init__(
        self,
        *,
        repository: BatchRepository,
        storage: BatchArtifactStorage,
        storage_registry: dict[str, BatchArtifactStorage] | None = None,
        metadata_retention_days: int = 30,
        storage_chunk_size: int = 65_536,
        max_file_bytes: int = 52_428_800,
        max_items_per_batch: int = 10_000,
        max_line_bytes: int = 1_048_576,
        callable_target_grant_service: CallableTargetGrantService | None = None,
        callable_target_scope_policy_mode: CallableTargetPolicyMode | str = "enforce",
        create_session_service: "BatchCreateSessionService" | None = None,
    ) -> None:
        self.repository = repository
        self.storage = storage
        active_backend = str(getattr(storage, "backend_name", "local") or "local").strip().lower()
        self.storage_registry = {
            str(key).strip().lower(): value
            for key, value in (storage_registry or {}).items()
        }
        self.storage_registry.setdefault(active_backend, storage)
        self.metadata_retention_days = metadata_retention_days
        self.storage_chunk_size = storage_chunk_size
        self.max_file_bytes = max_file_bytes
        self.max_items_per_batch = max_items_per_batch
        self.max_line_bytes = max_line_bytes
        self.callable_target_grant_service = callable_target_grant_service
        self.callable_target_scope_policy_mode = callable_target_scope_policy_mode
        self.create_session_service = create_session_service

    def bind_create_session_service(self, create_session_service: "BatchCreateSessionService" | None) -> None:
        self.create_session_service = create_session_service

    async def _refresh_batch_runtime_metrics(self) -> None:
        try:
            summary = await self.repository.summarize_runtime_statuses(now=datetime.now(tz=UTC))
            publish_batch_runtime_summary(summary)
        except Exception:
            logger.debug("batch service runtime metrics refresh failed", exc_info=True)
            return

    def _storage_for_backend(self, backend: str | None) -> BatchArtifactStorage:
        normalized = str(backend or getattr(self.storage, "backend_name", "local") or "local").strip().lower()
        storage = self.storage_registry.get(normalized)
        if storage is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Storage backend '{normalized}' is unavailable; keep legacy batch storage configured until referenced files expire",
            )
        return storage

    async def _upload_chunks(self, upload: UploadFile) -> AsyncIterator[bytes]:
        total_bytes = 0
        while True:
            chunk = await upload.read(self.storage_chunk_size)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > self.max_file_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=f"Uploaded file exceeds embeddings_batch_max_file_bytes ({self.max_file_bytes})",
                )
            yield chunk

    async def _cleanup_unrecorded_artifact(self, *, storage_key: str, backend: str) -> None:
        if not storage_key:
            return
        try:
            await self.storage.delete(storage_key)
        except Exception as exc:
            increment_batch_artifact_failure(operation="delete_orphan", backend=backend)
            logger.warning(
                "batch artifact orphan cleanup failed backend=%s storage_key=%s error=%s",
                backend,
                storage_key,
                exc,
            )

    async def _iter_storage_lines(
        self,
        *,
        storage: BatchArtifactStorage,
        storage_key: str,
    ) -> AsyncIterator[tuple[int, str]]:
        line_number = 0
        try:
            async for line in storage.iter_lines(
                storage_key,
                chunk_size=self.storage_chunk_size,
                max_line_bytes=self.max_line_bytes,
            ):
                line_number += 1
                yield line_number, line
        except BatchArtifactLineTooLongError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Line {exc.line_number} exceeds embeddings_batch_max_line_bytes ({self.max_line_bytes})",
            ) from exc
        except Exception as exc:
            backend = str(getattr(storage, "backend_name", "unknown") or "unknown")
            increment_batch_artifact_failure(operation="read", backend=backend)
            logger.warning(
                "batch artifact read failed backend=%s storage_key=%s error=%s",
                backend,
                storage_key,
                exc,
            )
            raise

    def _parse_input_line(
        self,
        raw_line: str,
        *,
        line_number: int,
        endpoint: str,
        auth: UserAPIKeyAuth,
        seen_custom_ids: set[str],
    ) -> tuple[BatchItemCreate | None, str | None]:
        parsed = parse_batch_input_line(
            raw_line,
            line_number=line_number,
            endpoint=endpoint,
            auth=auth,
            seen_custom_ids=seen_custom_ids,
            callable_target_grant_service=self.callable_target_grant_service,
            callable_target_scope_policy_mode=self.callable_target_scope_policy_mode,
        )
        if parsed is None:
            return None, None
        return (
            BatchItemCreate(
                line_number=parsed.line_number,
                custom_id=parsed.custom_id,
                request_body=parsed.request_body,
            ),
            parsed.model,
        )

    async def create_file(self, *, auth: UserAPIKeyAuth, upload: UploadFile, purpose: str) -> dict[str, Any]:
        storage_key: str | None = None
        record = None
        backend = str(getattr(self.storage, "backend_name", "local") or "local")
        try:
            storage_key, bytes_size, checksum = await self.storage.write_chunks(
                purpose=purpose,
                filename=upload.filename or "batch.jsonl",
                chunks=self._upload_chunks(upload),
            )
            if bytes_size <= 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")
            filename = upload.filename or "batch.jsonl"
            record = await self.repository.create_file(
                purpose=purpose,
                filename=filename,
                bytes_size=bytes_size,
                storage_backend=backend,
                storage_key=storage_key,
                checksum=checksum,
                created_by_api_key=auth.api_key,
                created_by_user_id=auth.user_id,
                created_by_team_id=auth.team_id,
                created_by_organization_id=auth.organization_id,
                expires_at=datetime.now(tz=UTC) + timedelta(days=self.metadata_retention_days),
            )
            if record is None:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable")
            return self.file_to_response(record)
        except Exception:
            if record is None and storage_key:
                await self._cleanup_unrecorded_artifact(storage_key=storage_key, backend=backend)
            raise

    async def get_file_content(self, *, file_id: str, auth: UserAPIKeyAuth) -> bytes:
        file_record = await self.repository.get_file(file_id)
        if file_record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
        if not can_access_owned_resource(
            owner_api_key=file_record.created_by_api_key,
            owner_team_id=file_record.created_by_team_id,
            owner_organization_id=file_record.created_by_organization_id,
            auth=auth,
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="File access denied")
        backend = str(file_record.storage_backend or "unknown")
        try:
            return await self._storage_for_backend(file_record.storage_backend).read_bytes(file_record.storage_key)
        except Exception as exc:
            increment_batch_artifact_failure(operation="read", backend=backend)
            logger.warning(
                "batch artifact read failed file_id=%s backend=%s storage_key=%s error=%s",
                file_id,
                backend,
                file_record.storage_key,
                exc,
            )
            raise

    async def create_embeddings_batch(
        self,
        *,
        auth: UserAPIKeyAuth,
        input_file_id: str,
        endpoint: str,
        metadata: dict[str, Any] | None,
        completion_window: str | None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return await self.create_batch(
            auth=auth,
            input_file_id=input_file_id,
            endpoint=endpoint,
            metadata=metadata,
            completion_window=completion_window,
            idempotency_key=idempotency_key,
        )

    async def create_batch(
        self,
        *,
        auth: UserAPIKeyAuth,
        input_file_id: str,
        endpoint: str,
        metadata: dict[str, Any] | None,
        completion_window: str | None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        result = await self.create_batch_result(
            auth=auth,
            input_file_id=input_file_id,
            endpoint=endpoint,
            metadata=metadata,
            completion_window=completion_window,
            idempotency_key=idempotency_key,
        )
        return result.response

    async def create_embeddings_batch_result(
        self,
        *,
        auth: UserAPIKeyAuth,
        input_file_id: str,
        endpoint: str,
        metadata: dict[str, Any] | None,
        completion_window: str | None,
        idempotency_key: str | None = None,
    ) -> BatchCreateResponseResult:
        return await self.create_batch_result(
            auth=auth,
            input_file_id=input_file_id,
            endpoint=endpoint,
            metadata=metadata,
            completion_window=completion_window,
            idempotency_key=idempotency_key,
        )

    async def create_batch_result(
        self,
        *,
        auth: UserAPIKeyAuth,
        input_file_id: str,
        endpoint: str,
        metadata: dict[str, Any] | None,
        completion_window: str | None,
        idempotency_key: str | None = None,
    ) -> BatchCreateResponseResult:
        if self.create_session_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Batch create-session service unavailable",
            )
        if hasattr(self.create_session_service, "create_batch"):
            result = await self.create_session_service.create_batch(
                auth=auth,
                input_file_id=input_file_id,
                endpoint=endpoint,
                metadata=metadata,
                completion_window=completion_window,
                idempotency_key=idempotency_key,
            )
        else:
            result = await self.create_session_service.create_embeddings_batch(
                auth=auth,
                input_file_id=input_file_id,
                endpoint=endpoint,
                metadata=metadata,
                completion_window=completion_window,
                idempotency_key=idempotency_key,
            )
        return BatchCreateResponseResult(
            response=self.job_to_response(result.job),
            audit_metadata=result.audit_metadata,
        )

    async def get_batch(self, *, batch_id: str, auth: UserAPIKeyAuth) -> dict[str, Any]:
        job = await self.repository.get_job(batch_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")
        if not can_access_owned_resource(
            owner_api_key=job.created_by_api_key,
            owner_team_id=job.created_by_team_id,
            owner_organization_id=job.created_by_organization_id,
            auth=auth,
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Batch access denied")
        return self.job_to_response(job)

    async def list_batches(self, *, auth: UserAPIKeyAuth, limit: int = 20) -> dict[str, Any]:
        jobs = await self.repository.list_jobs(
            limit=limit,
            created_by_api_key=auth.api_key,
            created_by_team_id=auth.team_id,
            created_by_organization_id=auth.organization_id if auth.team_id is None else None,
        )
        return {"object": "list", "data": [self.job_to_response(job) for job in jobs]}

    async def cancel_batch(self, *, batch_id: str, auth: UserAPIKeyAuth) -> dict[str, Any]:
        job = await self.repository.get_job(batch_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")
        if not can_access_owned_resource(
            owner_api_key=job.created_by_api_key,
            owner_team_id=job.created_by_team_id,
            owner_organization_id=job.created_by_organization_id,
            auth=auth,
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Batch access denied")
        job = await self.repository.request_cancel(batch_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")
        return self.job_to_response(job)

    def file_to_response(self, file_record) -> dict[str, Any]:
        return {
            "id": file_record.file_id,
            "object": "file",
            "bytes": file_record.bytes,
            "created_at": int(file_record.created_at.timestamp()),
            "filename": file_record.filename,
            "purpose": file_record.purpose,
            "status": file_record.status,
        }

    def job_to_response(self, job) -> dict[str, Any]:
        return {
            "id": job.batch_id,
            "object": "batch",
            "endpoint": job.endpoint,
            "completion_window": OPENAI_BATCH_COMPLETION_WINDOW,
            "status": self._public_batch_status(job),
            "input_file_id": job.input_file_id,
            "output_file_id": job.output_file_id,
            "error_file_id": job.error_file_id,
            "created_at": int(job.created_at.timestamp()),
            "expires_at": self._timestamp_or_none(getattr(job, "expires_at", None)),
            "in_progress_at": int(job.started_at.timestamp()) if job.started_at else None,
            "completed_at": int(job.completed_at.timestamp()) if job.completed_at else None,
            "failed_at": self._terminal_status_timestamp(job, BatchJobStatus.FAILED),
            "expired_at": self._terminal_status_timestamp(job, BatchJobStatus.EXPIRED),
            "errors": None,
            "request_counts": {
                "total": job.total_items,
                "completed": job.completed_items,
                "failed": job.failed_items,
                "cancelled": job.cancelled_items,
                "in_progress": job.in_progress_items,
            },
            "metadata": job.metadata or {},
        }

    def _public_batch_status(self, job) -> str:
        status_value = str(getattr(job, "status", "") or "")
        if status_value == BatchJobStatus.QUEUED.value:
            return "validating"
        if (
            status_value == BatchJobStatus.IN_PROGRESS.value
            and getattr(job, "cancel_requested_at", None) is not None
        ):
            return "cancelling"
        return status_value

    def _terminal_status_timestamp(self, job, terminal_status: BatchJobStatus) -> int | None:
        if str(getattr(job, "status", "") or "") != terminal_status.value:
            return None
        return self._timestamp_or_none(getattr(job, "status_last_updated_at", None))

    def _timestamp_or_none(self, value: datetime | None) -> int | None:
        return int(value.timestamp()) if value else None

    def _parse_input_jsonl(
        self,
        payload: bytes,
        *,
        endpoint: str,
        auth: UserAPIKeyAuth,
    ) -> tuple[list[BatchItemCreate], str | None]:
        text = payload.decode("utf-8")
        items: list[BatchItemCreate] = []
        seen_custom_ids: set[str] = set()
        inferred_model: str | None = None
        for line_number, raw_line in enumerate(text.splitlines(), start=1):
            item, model = self._parse_input_line(
                raw_line,
                line_number=line_number,
                endpoint=endpoint,
                auth=auth,
                seen_custom_ids=seen_custom_ids,
            )
            if item is None:
                continue
            items.append(item)
            inferred_model = inferred_model or model
        return items, inferred_model
