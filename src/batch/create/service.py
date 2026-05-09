from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator
from uuid import uuid4

from fastapi import HTTPException, status

from src.batch.access import can_access_owned_resource
from src.batch.endpoints import SUPPORTED_BATCH_ENDPOINT_SET, supported_batch_endpoints_display
from src.batch.create.models import (
    BatchCreateSessionCreate,
    BatchCreateSessionRecord,
    BatchCreateSessionStatus,
    BatchCreateStagedRequest,
)
from src.batch.create.promoter import BatchCreatePromotionError, BatchCreateSessionPromoter
from src.batch.create.session_repository import BatchCreateSessionRepository
from src.batch.create.session_stager import BatchCreateSessionStager
from src.batch.models import BatchJobRecord, normalize_batch_completion_window
from src.batch.repository import BatchRepository
from src.batch.request_validation import parse_batch_input_line
from src.batch.scheduling import estimate_request_work_units, resolve_model_group
from src.batch.scopes import (
    batch_idempotency_scope_key,
    batch_pending_scope_key_for_auth,
    batch_pending_scope_target_for_auth,
)
from src.batch.storage import BatchArtifactLineTooLongError, BatchArtifactStorage
from src.models.responses import UserAPIKeyAuth
from src.services.callable_target_grants import CallableTargetGrantService
from src.services.model_visibility import CallableTargetPolicyMode, ensure_batch_model_allowed
from src.metrics import increment_batch_artifact_failure, increment_batch_mixed_model_job

logger = logging.getLogger(__name__)


@dataclass
class BatchCreateSessionServiceResult:
    job: BatchJobRecord
    audit_metadata: dict[str, Any]


class BatchCreateSessionService:
    def __init__(
        self,
        *,
        repository: BatchRepository,
        create_session_repository: BatchCreateSessionRepository,
        stager: BatchCreateSessionStager,
        promoter: BatchCreateSessionPromoter,
        storage_registry: dict[str, BatchArtifactStorage],
        max_file_bytes: int,
        max_items_per_batch: int,
        max_line_bytes: int,
        storage_chunk_size: int,
        max_pending_batches_per_scope: int = 20,
        callable_target_grant_service: CallableTargetGrantService | None = None,
        callable_target_scope_policy_mode: CallableTargetPolicyMode | str = "enforce",
        idempotency_enabled: bool = False,
        model_group_resolver: Any | None = None,
        scheduler_enabled: bool = False,
        scheduler_shadow_enabled: bool = False,
        strict_model_homogeneity_enabled: bool = False,
        default_service_tier: str = "standard",
    ) -> None:
        self.repository = repository
        self.create_sessions = create_session_repository
        self.stager = stager
        self.promoter = promoter
        self.storage_registry = {
            str(key).strip().lower(): value
            for key, value in (storage_registry or {}).items()
        }
        self.max_file_bytes = max(1, int(max_file_bytes))
        self.max_items_per_batch = max(1, int(max_items_per_batch))
        self.max_line_bytes = max(1, int(max_line_bytes))
        self.storage_chunk_size = max(1, int(storage_chunk_size))
        self.max_pending_batches_per_scope = max(0, int(max_pending_batches_per_scope))
        self.callable_target_grant_service = callable_target_grant_service
        self.callable_target_scope_policy_mode = callable_target_scope_policy_mode
        self.idempotency_enabled = bool(idempotency_enabled)
        self.model_group_resolver = model_group_resolver
        self.scheduler_enabled = bool(scheduler_enabled)
        self.scheduler_shadow_enabled = bool(scheduler_shadow_enabled)
        self.strict_model_homogeneity_enabled = bool(strict_model_homogeneity_enabled)
        self.default_service_tier = str(default_service_tier or "standard").strip() or "standard"

    async def create_embeddings_batch(
        self,
        *,
        auth: UserAPIKeyAuth,
        input_file_id: str,
        endpoint: str,
        metadata: dict[str, Any] | None,
        completion_window: str | None,
        idempotency_key: str | None = None,
    ) -> BatchCreateSessionServiceResult:
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
    ) -> BatchCreateSessionServiceResult:
        endpoint = str(endpoint or "").strip()
        if endpoint not in SUPPORTED_BATCH_ENDPOINT_SET:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported batch endpoint '{endpoint}'. Supported endpoints: {supported_batch_endpoints_display()}",
            )
        self._validate_completion_window(completion_window)

        normalized_metadata = self._normalize_metadata(metadata)
        idem_scope_key, idem_key = self._idempotency_pair(auth=auth, idempotency_key=idempotency_key)

        if idem_scope_key is not None and idem_key is not None:
            existing = await self.create_sessions.get_session_by_idempotency_key(
                idempotency_scope_key=idem_scope_key,
                idempotency_key=idem_key,
            )
            if existing is not None:
                return await self._resolve_existing_session(
                    existing,
                    auth=auth,
                    input_file_id=input_file_id,
                    endpoint=endpoint,
                    metadata=normalized_metadata,
                    resolution="existing",
                    idempotency_key_present=True,
                )

        file_record = await self._load_authorized_input_file(auth=auth, input_file_id=input_file_id)
        await self._precheck_pending_batch_capacity(auth=auth)

        try:
            created_session = await self._stage_new_session(
                auth=auth,
                file_record=file_record,
                endpoint=endpoint,
                metadata=normalized_metadata,
                idempotency_scope_key=idem_scope_key,
                idempotency_key=idem_key,
            )
        except Exception:
            if idem_scope_key is not None and idem_key is not None:
                raced = await self.create_sessions.get_session_by_idempotency_key(
                    idempotency_scope_key=idem_scope_key,
                    idempotency_key=idem_key,
                )
                if raced is not None:
                    return await self._resolve_existing_session(
                        raced,
                        auth=auth,
                        input_file_id=input_file_id,
                        endpoint=endpoint,
                        metadata=normalized_metadata,
                        resolution="race_resolved",
                        idempotency_key_present=True,
                    )
            raise

        return await self._promote_session_result(
            created_session,
            resolution="created",
            idempotency_key_present=idem_key is not None,
        )

    async def _stage_new_session(
        self,
        *,
        auth: UserAPIKeyAuth,
        file_record: Any,
        endpoint: str,
        metadata: dict[str, Any] | None,
        idempotency_scope_key: str | None,
        idempotency_key: str | None,
    ) -> BatchCreateSessionRecord:
        storage = self._storage_for_backend(file_record.storage_backend)
        seen_custom_ids: set[str] = set()
        inferred_model: str | None = None
        inferred_model_group: str | None = None
        expected_item_count = 0

        async def _records() -> AsyncIterator[BatchCreateStagedRequest]:
            nonlocal expected_item_count, inferred_model, inferred_model_group
            async for line_number, raw_line in self._iter_storage_lines(storage=storage, storage_key=file_record.storage_key):
                item, model = self._parse_input_line(
                    raw_line,
                    line_number=line_number,
                    endpoint=endpoint,
                    auth=auth,
                    seen_custom_ids=seen_custom_ids,
                )
                if item is None:
                    continue
                expected_item_count += 1
                if expected_item_count > self.max_items_per_batch:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Batch exceeds embeddings_batch_max_items_per_batch ({self.max_items_per_batch})",
                    )
                scheduling_model_group = resolve_model_group(model, self.model_group_resolver)
                if inferred_model is None:
                    inferred_model = model
                    inferred_model_group = scheduling_model_group
                elif model != inferred_model or scheduling_model_group != inferred_model_group:
                    if self.strict_model_homogeneity_enabled:
                        increment_batch_mixed_model_job(mode="reject")
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=(
                                "Batch items must target one model while "
                                "embeddings_batch_scheduler_strict_model_homogeneity_enabled=true"
                            ),
                        )
                item_work_units = estimate_request_work_units(endpoint, item.request_body)
                yield BatchCreateStagedRequest(
                    line_number=item.line_number,
                    custom_id=item.custom_id,
                    request_body=item.request_body,
                    scheduling_model=model,
                    scheduling_model_group=scheduling_model_group,
                    estimated_work_units=item_work_units,
                )

        def _build_session(artifact) -> BatchCreateSessionCreate:  # noqa: ANN001
            if expected_item_count <= 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No valid batch items found in file")
            return BatchCreateSessionCreate(
                target_batch_id=str(uuid4()),
                endpoint=endpoint,
                input_file_id=file_record.file_id,
                staged_storage_backend=artifact.storage_backend,
                staged_storage_key=artifact.storage_key,
                staged_checksum=artifact.checksum,
                staged_bytes=artifact.bytes_size,
                expected_item_count=expected_item_count,
                inferred_model=inferred_model,
                metadata=metadata,
                effective_service_tier=self.default_service_tier,
                scheduling_scope_key=self._session_scope_key(auth),
                priority_quota_scope_key=self._session_scope_key(auth),
                idempotency_scope_key=idempotency_scope_key,
                idempotency_key=idempotency_key,
                created_by_api_key=auth.api_key,
                created_by_user_id=auth.user_id,
                created_by_team_id=auth.team_id,
                created_by_organization_id=auth.organization_id,
            )

        return await self.stager.stage_session(
            records=_records(),
            filename=getattr(file_record, "filename", None) or "batch.jsonl",
            build_session=_build_session,
        )

    async def _resolve_existing_session(
        self,
        session: BatchCreateSessionRecord,
        *,
        auth: UserAPIKeyAuth,
        input_file_id: str,
        endpoint: str,
        metadata: dict[str, Any] | None,
        resolution: str,
        idempotency_key_present: bool,
    ) -> BatchCreateSessionServiceResult:
        if not can_access_owned_resource(
            owner_api_key=session.created_by_api_key,
            owner_team_id=session.created_by_team_id,
            owner_organization_id=session.created_by_organization_id,
            auth=auth,
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency-Key conflicts with an existing batch create request",
            )
        if not self._request_matches_session(
            session,
            input_file_id=input_file_id,
            endpoint=endpoint,
            metadata=metadata,
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency-Key conflicts with an existing batch create request",
            )

        if session.status == BatchCreateSessionStatus.FAILED_PERMANENT:
            raise self._http_exception_for_promotion_error(
                BatchCreatePromotionError(
                    session.last_error_message or "Batch create session failed permanently",
                    code=session.last_error_code or "promotion_failed",
                    retryable=False,
                )
            )
        if session.status == BatchCreateSessionStatus.EXPIRED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency-Key refers to an expired batch create session",
            )
        return await self._promote_session_result(
            session,
            resolution=resolution,
            idempotency_key_present=idempotency_key_present,
        )

    async def _promote_session_result(
        self,
        session: BatchCreateSessionRecord,
        *,
        resolution: str,
        idempotency_key_present: bool,
    ) -> BatchCreateSessionServiceResult:
        try:
            promotion = await self.promoter.promote_session(session.session_id)
        except BatchCreatePromotionError as exc:
            raise self._http_exception_for_promotion_error(exc) from exc

        job = promotion.job
        if job is None:
            job = await self.repository.get_job(promotion.batch_id)
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Batch '{promotion.batch_id}' was not available after promotion",
            )
        return BatchCreateSessionServiceResult(
            job=job,
            audit_metadata={
                "create_path": "create_session",
                "create_session_id": session.session_id,
                "idempotency_key_present": idempotency_key_present,
                "idempotency_resolution": resolution if idempotency_key_present else "not_requested",
                "promotion_result": "promoted" if promotion.promoted else "existing_batch",
            },
        )

    async def _load_authorized_input_file(
        self,
        *,
        auth: UserAPIKeyAuth,
        input_file_id: str,
    ) -> Any:
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
        return file_record

    async def _precheck_pending_batch_capacity(self, *, auth: UserAPIKeyAuth) -> None:
        if self.max_pending_batches_per_scope <= 0:
            return
        scope = batch_pending_scope_target_for_auth(auth)
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

    def _storage_for_backend(self, backend: str | None) -> BatchArtifactStorage:
        normalized = str(backend or "local").strip().lower()
        storage = self.storage_registry.get(normalized)
        if storage is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Storage backend '{normalized}' is unavailable; keep create-session artifact storage configured until sessions expire",
            )
        return storage

    def _validate_completion_window(self, completion_window: object) -> None:
        try:
            normalize_batch_completion_window(completion_window)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

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
                "batch create-session input artifact read failed backend=%s storage_key=%s error=%s",
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
    ):
        parsed = parse_batch_input_line(
            raw_line,
            line_number=line_number,
            endpoint=endpoint,
            auth=auth,
            seen_custom_ids=seen_custom_ids,
            callable_target_grant_service=self.callable_target_grant_service,
            callable_target_scope_policy_mode=self.callable_target_scope_policy_mode,
            model_access_validator=ensure_batch_model_allowed,
        )
        if parsed is None:
            return None, None
        item = BatchCreateStagedRequest(
            line_number=parsed.line_number,
            custom_id=parsed.custom_id,
            request_body=parsed.request_body,
        )
        return item, parsed.model

    def _idempotency_pair(
        self,
        *,
        auth: UserAPIKeyAuth,
        idempotency_key: str | None,
    ) -> tuple[str | None, str | None]:
        if not self.idempotency_enabled:
            return None, None
        normalized_key = str(idempotency_key or "").strip() or None
        if normalized_key is None:
            return None, None
        return self._idempotency_scope_key(auth), normalized_key

    def _idempotency_scope_key(self, auth: UserAPIKeyAuth) -> str | None:
        return batch_idempotency_scope_key(auth)

    def _session_scope_key(self, auth: UserAPIKeyAuth) -> str | None:
        return batch_pending_scope_key_for_auth(auth)

    def _normalize_metadata(self, metadata: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(metadata, dict):
            return None
        return dict(metadata) or None

    def _request_matches_session(
        self,
        session: BatchCreateSessionRecord,
        *,
        input_file_id: str,
        endpoint: str,
        metadata: dict[str, Any] | None,
    ) -> bool:
        return (
            session.input_file_id == input_file_id
            and session.endpoint == endpoint
            and self._normalize_metadata(session.metadata) == self._normalize_metadata(metadata)
        )

    def _http_exception_for_promotion_error(self, exc: BatchCreatePromotionError) -> HTTPException:
        code = str(exc.code or "promotion_failed")
        if code == "pending_limit_exceeded":
            return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc))
        if code in {"staged_artifact_invalid", "item_count_mismatch"}:
            return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
        if code == "session_not_promotable":
            return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
        if code == "session_not_found":
            return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
