from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import TYPE_CHECKING, Any, AsyncIterator

from fastapi import HTTPException, UploadFile, status

from src.batch.access import can_access_owned_resource
from src.batch.models import BatchItemCreate, BatchJobRecord
from src.batch.repository import BatchRepository
from src.batch.scopes import batch_pending_scope_target_for_auth
from src.batch.storage import BatchArtifactLineTooLongError, BatchArtifactStorage
from src.metrics import (
    increment_batch_artifact_failure,
    observe_batch_create_latency,
    publish_batch_runtime_summary,
)
from src.models.requests import EmbeddingRequest
from src.models.responses import UserAPIKeyAuth
from src.services.model_visibility import CallableTargetPolicyMode, ensure_batch_model_allowed
from src.services.callable_target_grants import CallableTargetGrantService

if TYPE_CHECKING:
    from src.batch.create import BatchCreateSessionService

logger = logging.getLogger(__name__)


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
        create_buffer_size: int = 200,
        max_file_bytes: int = 52_428_800,
        max_items_per_batch: int = 10_000,
        max_line_bytes: int = 1_048_576,
        max_pending_batches_per_scope: int = 20,
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
        self.create_buffer_size = create_buffer_size
        self.max_file_bytes = max_file_bytes
        self.max_items_per_batch = max_items_per_batch
        self.max_line_bytes = max_line_bytes
        self.max_pending_batches_per_scope = max_pending_batches_per_scope
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

    def _scope_limit_target(self, auth: UserAPIKeyAuth) -> tuple[str, str, str | None, str | None] | None:
        return batch_pending_scope_target_for_auth(auth)

    async def _precheck_pending_batch_capacity(self, *, auth: UserAPIKeyAuth) -> None:
        if self.max_pending_batches_per_scope <= 0:
            return
        scope = self._scope_limit_target(auth)
        if scope is None:
            return
        _scope_type, _scope_id, created_by_api_key, created_by_team_id = scope
        active_batches = await self.repository.count_active_jobs_for_scope(
            created_by_api_key=created_by_api_key,
            created_by_team_id=created_by_team_id,
        )
        if active_batches >= self.max_pending_batches_per_scope:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    "Active batch count exceeds "
                    f"embeddings_batch_max_pending_batches_per_scope ({self.max_pending_batches_per_scope})"
                ),
            )

    async def _create_job_with_scope_limit(
        self,
        *,
        auth: UserAPIKeyAuth,
        endpoint: str,
        input_file_id: str,
        inferred_model: str | None,
        metadata: dict[str, Any] | None,
        repository: BatchRepository | None = None,
        within_transaction: bool = False,
    ) -> BatchJobRecord:
        target_repository = repository or self.repository
        scope = self._scope_limit_target(auth)
        db = getattr(target_repository, "prisma", None)
        if self.max_pending_batches_per_scope > 0 and scope is not None:
            scope_type, scope_id, created_by_api_key, created_by_team_id = scope
            if within_transaction:
                await target_repository.acquire_scope_advisory_lock(scope_type=scope_type, scope_id=scope_id)
            elif db is not None and not hasattr(db, "tx"):
                logger.debug(
                    "batch scope limit enforcement is best-effort without transaction support scope_type=%s",
                    scope_type,
                )
            active_batches = await target_repository.count_active_jobs_for_scope(
                created_by_api_key=created_by_api_key,
                created_by_team_id=created_by_team_id,
            )
            if active_batches >= self.max_pending_batches_per_scope:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(
                        "Active batch count exceeds "
                        f"embeddings_batch_max_pending_batches_per_scope ({self.max_pending_batches_per_scope})"
                    ),
                )

        job = await target_repository.create_job(
            endpoint=endpoint,
            input_file_id=input_file_id,
            model=inferred_model,
            metadata=metadata,
            created_by_api_key=auth.api_key,
            created_by_user_id=auth.user_id,
            created_by_team_id=auth.team_id,
            created_by_organization_id=auth.organization_id,
            expires_at=datetime.now(tz=UTC) + timedelta(days=self.metadata_retention_days),
        )
        if job is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable")
        return job

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
        line = raw_line.strip()
        if not line:
            return None, None
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid JSONL at line {line_number}: {exc.msg}",
            ) from exc
        if not isinstance(obj, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Line {line_number} must be an object")
        custom_id = str(obj.get("custom_id") or "").strip()
        if not custom_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Line {line_number} missing custom_id")
        if custom_id in seen_custom_ids:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Duplicate custom_id at line {line_number}")
        seen_custom_ids.add(custom_id)
        url = str(obj.get("url") or endpoint)
        if url != endpoint:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Line {line_number} must target {endpoint}")
        body = obj.get("body")
        if not isinstance(body, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Line {line_number} missing body")
        validated = EmbeddingRequest.model_validate(body)
        ensure_batch_model_allowed(
            auth,
            validated.model,
            callable_target_grant_service=self.callable_target_grant_service,
            policy_mode=self.callable_target_scope_policy_mode,
        )
        return (
            BatchItemCreate(
                line_number=line_number,
                custom_id=custom_id,
                request_body=validated.model_dump(exclude_none=True),
            ),
            validated.model,
        )

    async def _validate_input_to_spool(
        self,
        *,
        storage: BatchArtifactStorage,
        storage_key: str,
        endpoint: str,
        auth: UserAPIKeyAuth,
    ) -> tuple[Any, int, str | None]:
        seen_custom_ids: set[str] = set()
        inferred_model: str | None = None
        total_items = 0
        spool = tempfile.SpooledTemporaryFile(
            max_size=max(1_048_576, self.storage_chunk_size * 4),
            mode="w+t",
            encoding="utf-8",
            newline="\n",
        )
        try:
            async for line_number, raw_line in self._iter_storage_lines(storage=storage, storage_key=storage_key):
                item, model = self._parse_input_line(
                    raw_line,
                    line_number=line_number,
                    endpoint=endpoint,
                    auth=auth,
                    seen_custom_ids=seen_custom_ids,
                )
                if item is None:
                    continue
                total_items += 1
                if total_items > self.max_items_per_batch:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Batch exceeds embeddings_batch_max_items_per_batch ({self.max_items_per_batch})",
                    )
                inferred_model = inferred_model or model
                await asyncio.to_thread(
                    spool.write,
                    json.dumps(
                        {
                            "line_number": item.line_number,
                            "custom_id": item.custom_id,
                            "request_body": item.request_body,
                        }
                    )
                    + "\n",
                )
            await asyncio.to_thread(spool.flush)
            await asyncio.to_thread(spool.seek, 0)
            return spool, total_items, inferred_model
        except Exception:
            await asyncio.to_thread(spool.close)
            raise

    async def _insert_items_from_spool(
        self,
        *,
        batch_id: str,
        spool: Any,
        repository: BatchRepository | None = None,
    ) -> int:
        target_repository = repository or self.repository
        buffer: list[BatchItemCreate] = []
        inserted = 0
        while True:
            raw_line = await asyncio.to_thread(spool.readline)
            if not raw_line:
                break
            line = raw_line.strip()
            if not line:
                continue
            obj = json.loads(line)
            buffer.append(
                BatchItemCreate(
                    line_number=int(obj["line_number"]),
                    custom_id=str(obj["custom_id"]),
                    request_body=dict(obj["request_body"]),
                )
            )
            if len(buffer) >= self.create_buffer_size:
                inserted += await target_repository.create_items(batch_id, buffer)
                buffer.clear()
        if buffer:
            inserted += await target_repository.create_items(batch_id, buffer)
        return inserted

    async def create_file(self, *, auth: UserAPIKeyAuth, upload: UploadFile, purpose: str) -> dict[str, Any]:
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
            storage_backend=getattr(self.storage, "backend_name", "local"),
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
        result = await self.create_embeddings_batch_result(
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
        if self.create_session_service is not None:
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
        response = await self._create_embeddings_batch_legacy(
            auth=auth,
            input_file_id=input_file_id,
            endpoint=endpoint,
            metadata=metadata,
            completion_window=completion_window,
        )
        return BatchCreateResponseResult(
            response=response,
            audit_metadata={
                "create_path": "legacy",
                "idempotency_key_present": bool(str(idempotency_key or "").strip()),
                "idempotency_resolution": "disabled",
            },
        )

    async def _create_embeddings_batch_legacy(
        self,
        *,
        auth: UserAPIKeyAuth,
        input_file_id: str,
        endpoint: str,
        metadata: dict[str, Any] | None,
        completion_window: str | None,
    ) -> dict[str, Any]:
        started = perf_counter()
        del completion_window
        if endpoint != "/v1/embeddings":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only /v1/embeddings is supported")
        file_record = await self.repository.get_file(input_file_id)
        if file_record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Input file not found")
        if not can_access_owned_resource(
            owner_api_key=file_record.created_by_api_key,
            owner_team_id=file_record.created_by_team_id,
            owner_organization_id=file_record.created_by_organization_id,
            auth=auth,
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Input file access denied")
        if file_record.bytes > self.max_file_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"Input file exceeds embeddings_batch_max_file_bytes ({self.max_file_bytes})",
            )

        await self._precheck_pending_batch_capacity(auth=auth)
        file_storage = self._storage_for_backend(file_record.storage_backend)
        spool, item_count, inferred_model = await self._validate_input_to_spool(
            storage=file_storage,
            storage_key=file_record.storage_key,
            endpoint=endpoint,
            auth=auth,
        )
        db = getattr(self.repository, "prisma", None)
        try:
            if item_count <= 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No valid batch items found in file")

            job: BatchJobRecord | None = None
            if db is not None and hasattr(db, "tx") and hasattr(self.repository, "with_prisma"):
                async with db.tx() as tx:
                    tx_repository = self.repository.with_prisma(tx)
                    job = await self._create_job_with_scope_limit(
                        auth=auth,
                        endpoint=endpoint,
                        input_file_id=input_file_id,
                        metadata=metadata,
                        inferred_model=inferred_model,
                        repository=tx_repository,
                        within_transaction=True,
                    )
                    inserted = await self._insert_items_from_spool(
                        batch_id=job.batch_id,
                        spool=spool,
                        repository=tx_repository,
                    )
                    job = await tx_repository.set_job_queued(job.batch_id, inserted)
                    if job is None:
                        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to queue batch")
            else:
                job = await self._create_job_with_scope_limit(
                    auth=auth,
                    endpoint=endpoint,
                    input_file_id=input_file_id,
                    metadata=metadata,
                    inferred_model=inferred_model,
                )
                inserted = await self._insert_items_from_spool(
                    batch_id=job.batch_id,
                    spool=spool,
                )
                job = await self.repository.set_job_queued(job.batch_id, inserted)
                if job is None:
                    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to queue batch")

            assert job is not None
            await self._refresh_batch_runtime_metrics()
            observe_batch_create_latency(status="success", latency_seconds=perf_counter() - started)
            return self.job_to_response(job)
        except Exception:
            observe_batch_create_latency(status="error", latency_seconds=perf_counter() - started)
            raise
        finally:
            await asyncio.to_thread(spool.close)

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
            "status": job.status,
            "input_file_id": job.input_file_id,
            "output_file_id": job.output_file_id,
            "error_file_id": job.error_file_id,
            "created_at": int(job.created_at.timestamp()),
            "in_progress_at": int(job.started_at.timestamp()) if job.started_at else None,
            "completed_at": int(job.completed_at.timestamp()) if job.completed_at else None,
            "request_counts": {
                "total": job.total_items,
                "completed": job.completed_items,
                "failed": job.failed_items,
                "cancelled": job.cancelled_items,
                "in_progress": job.in_progress_items,
            },
            "metadata": job.metadata or {},
        }

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
