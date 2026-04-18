from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
import json
import logging
from time import perf_counter
from typing import Any, AsyncIterator, Awaitable, Callable

from src.batch.models import BatchJobStatus, is_operator_failed_reason
from src.batch.repository import BatchRepository
from src.batch.storage import BatchArtifactStorage
from src.metrics import (
    increment_batch_artifact_failure,
    increment_batch_finalization_retry,
    observe_batch_finalize_latency,
)

from src.batch.worker_types import BatchArtifactValidationError, BatchWorkerConfig

logger = logging.getLogger(__name__)


class BatchArtifactFinalizer:
    def __init__(
        self,
        *,
        repository: BatchRepository,
        storage: BatchArtifactStorage,
        config: BatchWorkerConfig,
    ) -> None:
        self.repository = repository
        self.storage = storage
        self.config = config

    def _public_batch_row_id(self, item) -> str:  # noqa: ANN001
        return f"batch_req_{item.item_id}"

    def _public_batch_request_id(self, item) -> str:  # noqa: ANN001
        return f"req_batch_{item.item_id}"

    def normalize_persisted_embedding_response_body(
        self,
        *,
        response_body: dict[str, Any],
        usage: dict[str, Any],
        api_provider: str,
        model_fallback: str | None,
    ) -> dict[str, Any]:
        normalized = dict(response_body)
        if normalized.get("object") is None:
            normalized["object"] = "list"

        data_rows = normalized.get("data")
        if isinstance(data_rows, list):
            normalized_rows: list[Any] = []
            for row_number, row in enumerate(data_rows):
                if not isinstance(row, dict):
                    normalized_rows.append(row)
                    continue
                normalized_row = dict(row)
                normalized_row.setdefault("object", "embedding")
                normalized_row["index"] = row_number
                normalized_rows.append(normalized_row)
            normalized["data"] = normalized_rows

        normalized["usage"] = dict(usage)
        normalized["_provider"] = api_provider

        model_name = normalized.get("model")
        if not isinstance(model_name, str) or not model_name.strip():
            fallback_model = str(model_fallback or "").strip()
            if fallback_model:
                normalized["model"] = fallback_model

        return normalized

    def _normalized_embedding_usage_or_none(self, value: Any) -> dict[str, int] | None:
        if not isinstance(value, dict):
            return None

        normalized_usage = dict(value)
        for token_field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            token_value = normalized_usage.get(token_field)
            if type(token_value) is not int or token_value < 0:
                return None

        return normalized_usage

    def _validate_public_embedding_usage(self, value: Any) -> dict[str, int]:
        if not isinstance(value, dict):
            raise BatchArtifactValidationError("completed batch item embedding response usage is not an object")

        normalized_usage = dict(value)
        for token_field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            token_value = normalized_usage.get(token_field)
            if type(token_value) is not int:
                raise BatchArtifactValidationError(
                    f"completed batch item embedding response usage has invalid {token_field}={token_value!r}"
                )
            if token_value < 0:
                raise BatchArtifactValidationError(
                    f"completed batch item embedding response usage has negative {token_field}={token_value!r}"
                )

        return normalized_usage

    def _public_embedding_model_fallback(self, item) -> str | None:  # noqa: ANN001
        request_body = item.request_body if isinstance(item.request_body, dict) else {}
        request_model = request_body.get("model")
        if not isinstance(request_model, str):
            return None
        normalized_model = request_model.strip()
        return normalized_model or None

    def _sanitize_public_embedding_body(self, item) -> dict[str, Any]:  # noqa: ANN001
        response_body = item.response_body
        if not isinstance(response_body, dict):
            raise BatchArtifactValidationError("completed batch item is missing an embedding response body")

        sanitized = dict(response_body)
        sanitized.pop("_provider", None)
        object_type = sanitized.get("object")
        if object_type is None:
            sanitized["object"] = "list"
        elif object_type != "list":
            raise BatchArtifactValidationError(
                f"completed batch item has invalid embedding response object={object_type!r}"
            )

        data_rows = sanitized.get("data")
        if not isinstance(data_rows, list):
            raise BatchArtifactValidationError("completed batch item embedding response data is not a list")
        if not data_rows:
            raise BatchArtifactValidationError("completed batch item embedding response data is empty")

        normalized_rows: list[Any] = []
        for row_number, row in enumerate(data_rows):
            if not isinstance(row, dict):
                raise BatchArtifactValidationError(
                    f"completed batch item embedding response row {row_number} is not an object"
                )
            normalized_row = dict(row)
            if "embedding" not in normalized_row:
                raise BatchArtifactValidationError(
                    f"completed batch item embedding response row {row_number} is missing embedding"
                )

            row_object = normalized_row.get("object")
            if row_object is None:
                normalized_row["object"] = "embedding"
            elif row_object != "embedding":
                raise BatchArtifactValidationError(
                    f"completed batch item embedding response row {row_number} has invalid object={row_object!r}"
                )

            response_index = normalized_row.get("index")
            if response_index is None:
                normalized_row["index"] = row_number
            elif type(response_index) is not int:
                raise BatchArtifactValidationError(
                    f"completed batch item embedding response row {row_number} has invalid index={response_index!r}"
                )
            normalized_rows.append(normalized_row)

        sanitized["data"] = normalized_rows

        model_name = sanitized.get("model")
        if not isinstance(model_name, str) or not model_name.strip():
            fallback_model = self._public_embedding_model_fallback(item)
            if fallback_model is not None:
                sanitized["model"] = fallback_model
        model_name = sanitized.get("model")
        if not isinstance(model_name, str) or not model_name.strip():
            raise BatchArtifactValidationError("completed batch item embedding response is missing a valid model")

        try:
            normalized_usage = self._validate_public_embedding_usage(sanitized.get("usage"))
        except BatchArtifactValidationError:
            normalized_usage = self._normalized_embedding_usage_or_none(item.usage)
            if normalized_usage is None:
                raise
        sanitized["usage"] = normalized_usage

        return sanitized

    def _serialize_completed_artifact_row(self, item) -> dict[str, Any]:  # noqa: ANN001
        return {
            "id": self._public_batch_row_id(item),
            "custom_id": item.custom_id,
            "response": {
                "status_code": 200,
                "request_id": self._public_batch_request_id(item),
                "body": self._sanitize_public_embedding_body(item),
            },
            "error": None,
        }

    def _serialize_failed_artifact_row(self, item) -> dict[str, Any]:  # noqa: ANN001
        error_body = dict(item.error_body) if isinstance(item.error_body, dict) else {}
        if not error_body.get("message"):
            error_body["message"] = item.last_error or (
                "Batch request cancelled" if item.status == "cancelled" else "Batch request failed"
            )
        if not error_body.get("type"):
            error_body["type"] = "BatchItemCancelled" if item.status == "cancelled" else "BatchItemError"

        return {
            "id": self._public_batch_row_id(item),
            "custom_id": item.custom_id,
            "response": None,
            "error": error_body,
        }

    def resolve_final_status(self, job) -> str:  # noqa: ANN001
        if job.cancel_requested_at is not None:
            return BatchJobStatus.CANCELLED
        if is_operator_failed_reason(job.provider_error):
            return BatchJobStatus.FAILED
        if job.completed_items == 0 and job.failed_items > 0:
            return BatchJobStatus.FAILED
        return BatchJobStatus.COMPLETED

    async def _persist_permanent_finalization_failure(self, job, *, reason: str) -> bool:
        finalized = await self.repository.attach_artifacts_and_finalize(
            batch_id=job.batch_id,
            output_file_id=None,
            error_file_id=None,
            final_status=BatchJobStatus.FAILED,
            worker_id=self.config.worker_id,
        )
        if finalized is None:
            return False
        try:
            await self.repository.set_provider_error(
                batch_id=job.batch_id,
                provider_error=f"artifact_validation_failed: {reason}",
            )
        except Exception:
            logger.warning(
                "batch finalization failed to persist permanent failure reason batch_id=%s",
                job.batch_id,
                exc_info=True,
            )
        return True

    async def _schedule_finalization_retry(self, job) -> None:
        rescheduled = await self.repository.reschedule_finalization(
            batch_id=job.batch_id,
            worker_id=self.config.worker_id,
            retry_delay_seconds=self.config.finalization_retry_delay_seconds,
        )
        if not rescheduled:
            logger.warning("batch finalization retry skipped after lease loss batch_id=%s", job.batch_id)
            increment_batch_finalization_retry(result="lease_lost")
            return
        logger.info(
            "batch finalization retry scheduled batch_id=%s worker_id=%s delay_seconds=%s",
            job.batch_id,
            self.config.worker_id,
            self.config.finalization_retry_delay_seconds,
        )
        increment_batch_finalization_retry(result="scheduled")

    async def finalize_with_retry(
        self,
        job,
        *,
        finalize_artifacts: Callable[[Any], Awaitable[None]] | None = None,
    ) -> None:  # noqa: ANN001
        started = perf_counter()
        finalize = finalize_artifacts or self.finalize_artifacts
        try:
            await finalize(job)
            observe_batch_finalize_latency(status="success", latency_seconds=perf_counter() - started)
            return
        except BatchArtifactValidationError as exc:
            logger.warning(
                "batch finalization permanently failed batch_id=%s error=%s",
                job.batch_id,
                exc,
                exc_info=True,
            )
            try:
                persisted = await self._persist_permanent_finalization_failure(job, reason=str(exc))
            except Exception:
                logger.warning(
                    "batch finalization permanent-failure persistence failed batch_id=%s",
                    job.batch_id,
                    exc_info=True,
                )
            else:
                if persisted:
                    observe_batch_finalize_latency(status="error", latency_seconds=perf_counter() - started)
                    return
                logger.warning(
                    "batch finalization permanent-failure persistence skipped after lease loss batch_id=%s",
                    job.batch_id,
                )
        except Exception as exc:
            logger.warning("batch finalization failed batch_id=%s error=%s", job.batch_id, exc, exc_info=True)
            await self._schedule_finalization_retry(job)
            observe_batch_finalize_latency(status="error", latency_seconds=perf_counter() - started)
            return
        await self._schedule_finalization_retry(job)
        observe_batch_finalize_latency(status="error", latency_seconds=perf_counter() - started)

    async def _cleanup_unattached_artifacts(self, artifacts: list[tuple[str, str]]) -> None:
        for file_id, storage_key in artifacts:
            with contextlib.suppress(Exception):
                await self.storage.delete(storage_key)
            with contextlib.suppress(Exception):
                await self.repository.delete_file(file_id)

    async def _iter_batch_items(self, batch_id: str) -> AsyncIterator[Any]:
        if hasattr(self.repository, "list_items_page"):
            after_line_number: int | None = None
            while True:
                page = await self.repository.list_items_page(
                    batch_id=batch_id,
                    limit=self.config.finalization_page_size,
                    after_line_number=after_line_number,
                )
                if not page:
                    break
                for item in page:
                    yield item
                after_line_number = page[-1].line_number
            return

        for item in await self.repository.list_items(batch_id):
            yield item

    async def iter_output_lines(self, batch_id: str) -> AsyncIterator[str]:
        async for item in self._iter_batch_items(batch_id):
            if item.status == "completed":
                yield json.dumps(self._serialize_completed_artifact_row(item))

    async def iter_error_lines(self, batch_id: str) -> AsyncIterator[str]:
        async for item in self._iter_batch_items(batch_id):
            if item.status in {"failed", "cancelled"}:
                yield json.dumps(self._serialize_failed_artifact_row(item))

    async def finalize_artifacts(self, job) -> None:  # noqa: ANN001
        storage_backend = getattr(self.storage, "backend_name", "local")
        created_artifacts: list[tuple[str, str]] = []
        output_file_id: str | None = None
        error_file_id: str | None = None
        final_status = self.resolve_final_status(job)
        try:
            if job.completed_items > 0:
                key, size, checksum = await self.storage.write_lines_stream(
                    purpose="batch_output",
                    filename=f"{job.batch_id}-output.jsonl",
                    lines=self.iter_output_lines(job.batch_id),
                )
                file_record = await self.repository.create_file(
                    purpose="batch_output",
                    filename=f"{job.batch_id}-output.jsonl",
                    bytes_size=size,
                    storage_backend=storage_backend,
                    storage_key=key,
                    checksum=checksum,
                    created_by_api_key=job.created_by_api_key,
                    created_by_user_id=job.created_by_user_id,
                    created_by_team_id=job.created_by_team_id,
                    created_by_organization_id=job.created_by_organization_id,
                    expires_at=datetime.now(tz=UTC) + timedelta(days=self.config.completed_artifact_retention_days),
                )
                if file_record is None:
                    increment_batch_artifact_failure(operation="create_record", backend=storage_backend)
                    raise RuntimeError(f"Failed to create output artifact record for batch {job.batch_id}")
                created_artifacts.append((file_record.file_id, key))
                output_file_id = file_record.file_id

            if job.failed_items > 0 or job.cancelled_items > 0:
                key, size, checksum = await self.storage.write_lines_stream(
                    purpose="batch_error",
                    filename=f"{job.batch_id}-error.jsonl",
                    lines=self.iter_error_lines(job.batch_id),
                )
                retention_days = (
                    self.config.failed_artifact_retention_days
                    if final_status in {BatchJobStatus.FAILED, BatchJobStatus.CANCELLED}
                    else self.config.completed_artifact_retention_days
                )
                file_record = await self.repository.create_file(
                    purpose="batch_error",
                    filename=f"{job.batch_id}-error.jsonl",
                    bytes_size=size,
                    storage_backend=storage_backend,
                    storage_key=key,
                    checksum=checksum,
                    created_by_api_key=job.created_by_api_key,
                    created_by_user_id=job.created_by_user_id,
                    created_by_team_id=job.created_by_team_id,
                    created_by_organization_id=job.created_by_organization_id,
                    expires_at=datetime.now(tz=UTC) + timedelta(days=retention_days),
                )
                if file_record is None:
                    increment_batch_artifact_failure(operation="create_record", backend=storage_backend)
                    raise RuntimeError(f"Failed to create error artifact record for batch {job.batch_id}")
                created_artifacts.append((file_record.file_id, key))
                error_file_id = file_record.file_id

            finalized = await self.repository.attach_artifacts_and_finalize(
                batch_id=job.batch_id,
                output_file_id=output_file_id,
                error_file_id=error_file_id,
                final_status=final_status,
                worker_id=self.config.worker_id,
            )
            if finalized is None:
                raise RuntimeError(f"Failed to finalize batch {job.batch_id}")
        except Exception:
            increment_batch_artifact_failure(operation="write_or_finalize", backend=storage_backend)
            await self._cleanup_unattached_artifacts(created_artifacts)
            raise
        logger.info(
            "batch finalized id=%s status=%s completed=%s failed=%s",
            job.batch_id,
            final_status,
            job.completed_items,
            job.failed_items,
        )
