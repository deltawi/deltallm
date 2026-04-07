from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, UploadFile, status

from src.batch.access import can_access_owned_resource
from src.batch.models import BatchItemCreate
from src.batch.repository import BatchRepository
from src.batch.storage import BatchArtifactStorage
from src.models.requests import EmbeddingRequest
from src.models.responses import UserAPIKeyAuth
from src.services.model_visibility import CallableTargetPolicyMode, ensure_batch_model_allowed
from src.services.callable_target_grants import CallableTargetGrantService


class BatchService:
    def __init__(
        self,
        *,
        repository: BatchRepository,
        storage: BatchArtifactStorage,
        storage_registry: dict[str, BatchArtifactStorage] | None = None,
        metadata_retention_days: int = 30,
        callable_target_grant_service: CallableTargetGrantService | None = None,
        callable_target_scope_policy_mode: CallableTargetPolicyMode | str = "enforce",
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
        self.callable_target_grant_service = callable_target_grant_service
        self.callable_target_scope_policy_mode = callable_target_scope_policy_mode

    def _storage_for_backend(self, backend: str | None) -> BatchArtifactStorage:
        normalized = str(backend or getattr(self.storage, "backend_name", "local") or "local").strip().lower()
        storage = self.storage_registry.get(normalized)
        if storage is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Storage backend '{normalized}' is unavailable; keep legacy batch storage configured until referenced files expire",
            )
        return storage

    async def create_file(self, *, auth: UserAPIKeyAuth, upload: UploadFile, purpose: str) -> dict[str, Any]:
        body = await upload.read()
        if not body:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")
        filename = upload.filename or "batch.jsonl"
        storage_key, bytes_size, checksum = await self.storage.write_bytes(
            purpose=purpose,
            filename=filename,
            content=body,
        )
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
            auth=auth,
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="File access denied")
        return await self._storage_for_backend(file_record.storage_backend).read_bytes(file_record.storage_key)

    async def create_embeddings_batch(
        self,
        *,
        auth: UserAPIKeyAuth,
        input_file_id: str,
        endpoint: str,
        metadata: dict[str, Any] | None,
        completion_window: str | None,
    ) -> dict[str, Any]:
        del completion_window
        if endpoint != "/v1/embeddings":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only /v1/embeddings is supported")
        file_record = await self.repository.get_file(input_file_id)
        if file_record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Input file not found")
        if not can_access_owned_resource(
            owner_api_key=file_record.created_by_api_key,
            owner_team_id=file_record.created_by_team_id,
            auth=auth,
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Input file access denied")

        raw = await self._storage_for_backend(file_record.storage_backend).read_bytes(file_record.storage_key)
        items, inferred_model = self._parse_input_jsonl(raw, endpoint=endpoint, auth=auth)
        if not items:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No valid batch items found in file")

        job = await self.repository.create_job(
            endpoint=endpoint,
            input_file_id=input_file_id,
            model=inferred_model,
            metadata=metadata,
            created_by_api_key=auth.api_key,
            created_by_user_id=auth.user_id,
            created_by_team_id=auth.team_id,
            expires_at=datetime.now(tz=UTC) + timedelta(days=self.metadata_retention_days),
        )
        if job is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable")
        inserted = await self.repository.create_items(job.batch_id, items)
        job = await self.repository.set_job_queued(job.batch_id, inserted)
        if job is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to queue batch")
        return self.job_to_response(job)

    async def get_batch(self, *, batch_id: str, auth: UserAPIKeyAuth) -> dict[str, Any]:
        job = await self.repository.get_job(batch_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")
        if not can_access_owned_resource(
            owner_api_key=job.created_by_api_key,
            owner_team_id=job.created_by_team_id,
            auth=auth,
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Batch access denied")
        return self.job_to_response(job)

    async def list_batches(self, *, auth: UserAPIKeyAuth, limit: int = 20) -> dict[str, Any]:
        jobs = await self.repository.list_jobs(
            limit=limit,
            created_by_api_key=auth.api_key,
            created_by_team_id=auth.team_id,
        )
        return {"object": "list", "data": [self.job_to_response(job) for job in jobs]}

    async def cancel_batch(self, *, batch_id: str, auth: UserAPIKeyAuth) -> dict[str, Any]:
        job = await self.repository.get_job(batch_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")
        if not can_access_owned_resource(
            owner_api_key=job.created_by_api_key,
            owner_team_id=job.created_by_team_id,
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
            line = raw_line.strip()
            if not line:
                continue
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
            inferred_model = inferred_model or validated.model
            items.append(
                BatchItemCreate(
                    line_number=line_number,
                    custom_id=custom_id,
                    request_body=validated.model_dump(exclude_none=True),
                )
            )
        return items, inferred_model
