from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from src.batch.create.defaults import (
    DEFAULT_CREATE_SESSION_PROMOTION_TX_MAX_WAIT_SECONDS,
    DEFAULT_CREATE_SESSION_PROMOTION_TX_TIMEOUT_SECONDS,
)
from src.batch.create.models import (
    BatchCreateSessionRecord,
    BatchCreateSessionStatus,
    BatchCreateStagedRequest,
)
from src.batch.scopes import batch_pending_scope_target_for_session
from src.batch.create.staging import BatchCreateStagingBackend, staged_artifact_from_session
from src.batch.models import BatchItemCreate, BatchJobRecord, BatchJobStatus
from src.batch.scheduling import (
    ESTIMATOR_VERSION,
    MIXED_MODEL_GROUP,
    build_scheduling_dimensions,
    estimate_request_work_units,
    parse_tenant_scope_preference,
    resolve_model_group,
    resolve_scheduler_version,
    size_class_for_work_units,
)
from src.batch.storage import BatchArtifactLineTooLongError
from src.metrics import (
    increment_batch_create_session_action,
    increment_batch_mixed_model_job,
    increment_batch_scheduler_shadow_record,
)

if TYPE_CHECKING:
    from src.batch.repository import BatchRepository

logger = logging.getLogger(__name__)

_PROMOTABLE_SESSION_STATUSES = frozenset(
    {
        BatchCreateSessionStatus.STAGED,
        BatchCreateSessionStatus.FAILED_RETRYABLE,
    }
)


@dataclass
class BatchCreatePromotionResult:
    session_id: str
    batch_id: str
    promoted: bool
    job: BatchJobRecord | None = None


@dataclass
class BatchCreatePromotionSchedulingSummary:
    scheduling_model: str | None
    scheduling_model_group: str | None
    estimated_work_units: int
    remaining_work_units: int
    size_class: str
    mixed_model: bool = False


class BatchCreatePromotionError(RuntimeError):
    def __init__(self, message: str, *, code: str, retryable: bool) -> None:
        super().__init__(message)
        self.code = str(code or "promotion_failed")
        self.retryable = bool(retryable)


class BatchCreateSessionPromoter:
    def __init__(
        self,
        *,
        repository: BatchRepository,
        staging: BatchCreateStagingBackend,
        metadata_retention_days: int = 30,
        max_pending_batches_per_scope: int = 20,
        insert_chunk_size: int = 500,
        soft_precheck_enabled: bool = True,
        tx_max_wait_seconds: float = DEFAULT_CREATE_SESSION_PROMOTION_TX_MAX_WAIT_SECONDS,
        tx_timeout_seconds: float = DEFAULT_CREATE_SESSION_PROMOTION_TX_TIMEOUT_SECONDS,
        model_group_resolver: Any | None = None,
        scheduler_enabled: bool = False,
        scheduler_shadow_enabled: bool = False,
        scheduler_mode: str | None = None,
        scheduler_shadow_mode: str | None = None,
        strict_model_homogeneity_enabled: bool = False,
        default_service_tier: str = "standard",
        tenant_scope_preference: tuple[str, ...] | list[str] | str | None = None,
        tenant_max_queued_work_units: int = 0,
    ) -> None:
        self.repository = repository
        self.staging = staging
        self.metadata_retention_days = max(1, int(metadata_retention_days))
        self.max_pending_batches_per_scope = max(0, int(max_pending_batches_per_scope))
        self.insert_chunk_size = max(1, int(insert_chunk_size))
        self.soft_precheck_enabled = bool(soft_precheck_enabled)
        self.tx_max_wait = timedelta(seconds=max(float(tx_max_wait_seconds), 0.001))
        self.tx_timeout = timedelta(seconds=max(float(tx_timeout_seconds), 0.001))
        self.model_group_resolver = model_group_resolver
        self.scheduler_enabled = bool(scheduler_enabled)
        self.scheduler_shadow_enabled = bool(scheduler_shadow_enabled)
        self.scheduler_mode = scheduler_mode
        self.scheduler_shadow_mode = scheduler_shadow_mode
        self.strict_model_homogeneity_enabled = bool(strict_model_homogeneity_enabled)
        self.default_service_tier = str(default_service_tier or "standard").strip() or "standard"
        self.tenant_scope_preference = parse_tenant_scope_preference(tenant_scope_preference)
        self.tenant_max_queued_work_units = max(0, int(tenant_max_queued_work_units or 0))

    def configure_scheduler(
        self,
        *,
        scheduler_enabled: bool,
        scheduler_shadow_enabled: bool,
        scheduler_mode: str | None,
        scheduler_shadow_mode: str | None,
        strict_model_homogeneity_enabled: bool,
        default_service_tier: str,
        tenant_scope_preference: tuple[str, ...] | list[str] | str | None,
        tenant_max_queued_work_units: int,
    ) -> None:
        self.scheduler_enabled = bool(scheduler_enabled)
        self.scheduler_shadow_enabled = bool(scheduler_shadow_enabled)
        self.scheduler_mode = scheduler_mode
        self.scheduler_shadow_mode = scheduler_shadow_mode
        self.strict_model_homogeneity_enabled = bool(strict_model_homogeneity_enabled)
        self.default_service_tier = str(default_service_tier or "standard").strip() or "standard"
        self.tenant_scope_preference = parse_tenant_scope_preference(tenant_scope_preference)
        self.tenant_max_queued_work_units = max(0, int(tenant_max_queued_work_units or 0))

    async def promote_session(self, session_id: str) -> BatchCreatePromotionResult:
        session = await self.repository.create_sessions.get_session(session_id)
        if session is None:
            raise BatchCreatePromotionError(
                f"Batch create session '{session_id}' was not found",
                code="session_not_found",
                retryable=False,
            )

        if session.status == BatchCreateSessionStatus.COMPLETED:
            return await self._build_completed_result(session)
        if session.status not in _PROMOTABLE_SESSION_STATUSES:
            raise BatchCreatePromotionError(
                f"Batch create session '{session_id}' is not promotable from status '{session.status}'",
                code="session_not_promotable",
                retryable=False,
            )

        try:
            await self._precheck_pending_capacity(session)
        except BatchCreatePromotionError:
            increment_batch_create_session_action(action="promotion_precheck", status="rejected")
            raise

        try:
            spool, item_count, scheduling_summary = await self._spool_staged_items(session)
        except BatchCreatePromotionError as exc:
            await self._record_failure(session=session, error=exc)
            increment_batch_create_session_action(action="promotion", status="error")
            raise

        try:
            result = await self._promote_spooled_session(
                session_id=session_id,
                spool=spool,
                item_count=item_count,
                scheduling_summary=scheduling_summary,
            )
        except BatchCreatePromotionError as exc:
            await self._record_failure(session=session, error=exc)
            increment_batch_create_session_action(action="promotion", status="error")
            raise
        except Exception as exc:
            wrapped = BatchCreatePromotionError(
                f"Failed to promote batch create session '{session_id}': {exc}",
                code="promotion_failed",
                retryable=True,
            )
            await self._record_failure(session=session, error=wrapped)
            increment_batch_create_session_action(action="promotion", status="error")
            raise wrapped from exc
        finally:
            await asyncio.to_thread(spool.close)

        increment_batch_create_session_action(action="promotion", status="success")
        return result

    async def _build_completed_result(self, session: BatchCreateSessionRecord) -> BatchCreatePromotionResult:
        job = await self.repository.get_job(session.target_batch_id)
        if job is None:
            raise BatchCreatePromotionError(
                f"Completed batch create session '{session.session_id}' is missing job '{session.target_batch_id}'",
                code="completed_session_missing_batch",
                retryable=False,
            )
        return BatchCreatePromotionResult(
            session_id=session.session_id,
            batch_id=job.batch_id,
            promoted=False,
            job=job,
        )

    async def _spool_staged_items(
        self,
        session: BatchCreateSessionRecord,
    ) -> tuple[Any, int, BatchCreatePromotionSchedulingSummary]:
        artifact = staged_artifact_from_session(session)
        spool = tempfile.SpooledTemporaryFile(
            max_size=max(1_048_576, self.insert_chunk_size * 2_048),
            mode="w+t",
            encoding="utf-8",
            newline="\n",
        )
        count = 0
        first_model: str | None = None
        first_model_group: str | None = None
        estimated_work_units = 0
        mixed_model = False
        try:
            async for record in self.staging.read_records(artifact):
                count += 1
                scheduling_model = record.scheduling_model or str((record.request_body or {}).get("model") or "").strip() or None
                scheduling_model_group = record.scheduling_model_group or resolve_model_group(
                    scheduling_model,
                    self.model_group_resolver,
                )
                item_work_units = max(
                    1,
                    int(
                        record.estimated_work_units
                        if record.estimated_work_units is not None
                        else estimate_request_work_units(session.endpoint, record.request_body)
                    ),
                )
                if first_model is None:
                    first_model = scheduling_model
                    first_model_group = scheduling_model_group
                elif scheduling_model != first_model or scheduling_model_group != first_model_group:
                    mixed_model = True
                    if self.strict_model_homogeneity_enabled:
                        increment_batch_mixed_model_job(mode="reject")
                        raise BatchCreatePromotionError(
                            "Staged batch create artifact contains multiple scheduler model dimensions",
                            code="mixed_model_batch",
                            retryable=False,
                        )
                await asyncio.to_thread(
                    spool.write,
                    json.dumps(
                        {
                            **record.to_jsonable(),
                            "scheduling_model": scheduling_model,
                            "scheduling_model_group": scheduling_model_group,
                            "estimated_work_units": item_work_units,
                        },
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ) + "\n",
                )
                estimated_work_units += item_work_units
            await asyncio.to_thread(spool.flush)
            await asyncio.to_thread(spool.seek, 0)
        except BatchArtifactLineTooLongError as exc:
            await asyncio.to_thread(spool.close)
            raise BatchCreatePromotionError(
                f"Staged batch create artifact line {exc.line_number} exceeds the configured limit",
                code="staged_artifact_invalid",
                retryable=False,
            ) from exc
        except ValueError as exc:
            await asyncio.to_thread(spool.close)
            raise BatchCreatePromotionError(
                f"Staged batch create artifact is invalid: {exc}",
                code="staged_artifact_invalid",
                retryable=False,
            ) from exc
        except BatchCreatePromotionError:
            await asyncio.to_thread(spool.close)
            raise
        except Exception as exc:
            await asyncio.to_thread(spool.close)
            raise BatchCreatePromotionError(
                f"Failed to read staged batch create artifact: {exc}",
                code="staged_artifact_unavailable",
                retryable=True,
            ) from exc

        if count <= 0 or count != session.expected_item_count:
            await asyncio.to_thread(spool.close)
            raise BatchCreatePromotionError(
                (
                    "Staged batch create artifact item count does not match the session contract "
                    f"(expected {session.expected_item_count}, read {count})"
                ),
                code="item_count_mismatch",
                retryable=False,
            )
        if mixed_model:
            increment_batch_mixed_model_job(mode="warn")
        summary_model = MIXED_MODEL_GROUP if mixed_model else first_model or session.inferred_model
        summary_model_group = (
            MIXED_MODEL_GROUP
            if mixed_model
            else first_model_group or resolve_model_group(session.inferred_model, self.model_group_resolver)
        )
        return (
            spool,
            count,
            BatchCreatePromotionSchedulingSummary(
                scheduling_model=summary_model,
                scheduling_model_group=summary_model_group,
                estimated_work_units=max(1, estimated_work_units),
                remaining_work_units=max(1, estimated_work_units),
                size_class=size_class_for_work_units(estimated_work_units),
                mixed_model=mixed_model,
            ),
        )

    async def _promote_spooled_session(
        self,
        *,
        session_id: str,
        spool: Any,
        item_count: int,
        scheduling_summary: BatchCreatePromotionSchedulingSummary,
    ) -> BatchCreatePromotionResult:
        db = getattr(self.repository, "prisma", None)
        if db is None or not hasattr(db, "tx") or not hasattr(self.repository, "with_prisma"):
            raise BatchCreatePromotionError(
                "Batch create-session promotion requires transactional database support",
                code="database_unavailable",
                retryable=True,
            )

        async with db.tx(max_wait=self.tx_max_wait, timeout=self.tx_timeout) as tx:
            tx_repository = self.repository.with_prisma(tx)
            session = await tx_repository.create_sessions.get_session_for_update(session_id)
            if session is None:
                raise BatchCreatePromotionError(
                    f"Batch create session '{session_id}' was not found",
                    code="session_not_found",
                    retryable=False,
                )

            existing_job = await tx_repository.get_job(session.target_batch_id)
            if session.status == BatchCreateSessionStatus.COMPLETED:
                if existing_job is None:
                    raise BatchCreatePromotionError(
                        f"Completed batch create session '{session_id}' is missing job '{session.target_batch_id}'",
                        code="completed_session_missing_batch",
                        retryable=False,
                    )
                return BatchCreatePromotionResult(
                    session_id=session.session_id,
                    batch_id=existing_job.batch_id,
                    promoted=False,
                    job=existing_job,
                )

            if existing_job is not None:
                completed = await tx_repository.create_sessions.mark_session_completed(
                    session.session_id,
                    completed_at=datetime.now(tz=UTC),
                    expires_at=None,
                    increment_promotion_attempt_count=True,
                    from_statuses=tuple(_PROMOTABLE_SESSION_STATUSES),
                )
                if completed is None:
                    raise BatchCreatePromotionError(
                        f"Failed to reconcile existing batch '{existing_job.batch_id}' with session '{session.session_id}'",
                        code="session_update_failed",
                        retryable=True,
                    )
                return BatchCreatePromotionResult(
                    session_id=completed.session_id,
                    batch_id=existing_job.batch_id,
                    promoted=False,
                    job=existing_job,
                )

            if session.status not in _PROMOTABLE_SESSION_STATUSES:
                raise BatchCreatePromotionError(
                    f"Batch create session '{session_id}' is not promotable from status '{session.status}'",
                    code="session_not_promotable",
                    retryable=False,
                )

            await self._enforce_pending_capacity(session=session, repository=tx_repository)
            await self._enforce_tenant_queued_work_capacity(
                session=session,
                scheduling_summary=scheduling_summary,
                repository=tx_repository,
            )

            await asyncio.to_thread(spool.seek, 0)
            scheduler_version = resolve_scheduler_version(
                active_enabled=self.scheduler_enabled,
                shadow_enabled=self.scheduler_shadow_enabled,
                active_mode=self.scheduler_mode,
                shadow_mode=self.scheduler_shadow_mode,
            )
            job = await tx_repository.create_job(
                batch_id=session.target_batch_id,
                endpoint=session.endpoint,
                input_file_id=session.input_file_id,
                model=session.inferred_model,
                metadata=session.metadata,
                created_by_api_key=session.created_by_api_key,
                created_by_user_id=session.created_by_user_id,
                created_by_team_id=session.created_by_team_id,
                created_by_organization_id=session.created_by_organization_id,
                expires_at=datetime.now(tz=UTC) + timedelta(days=self.metadata_retention_days),
                execution_mode="managed_internal",
                status=BatchJobStatus.QUEUED,
                total_items=item_count,
                scheduler_version=scheduler_version,
                scheduling_model=scheduling_summary.scheduling_model,
                scheduling_model_group=scheduling_summary.scheduling_model_group,
                scheduling_endpoint=session.endpoint,
                service_tier=session.effective_service_tier or self.default_service_tier,
                estimated_work_units=scheduling_summary.estimated_work_units,
                remaining_work_units=scheduling_summary.remaining_work_units,
                size_class=scheduling_summary.size_class,
                scheduler_debug={
                    "estimator_version": ESTIMATOR_VERSION,
                    "mixed_model": scheduling_summary.mixed_model,
                    "strict_model_homogeneity_enabled": self.strict_model_homogeneity_enabled,
                },
                tenant_scope_preference=self.tenant_scope_preference,
            )
            if job is None:
                raise BatchCreatePromotionError(
                    f"Failed to create queued batch job for session '{session_id}'",
                    code="job_create_failed",
                    retryable=True,
                )

            inserted = await self._insert_items_from_spool(
                batch_id=job.batch_id,
                spool=spool,
                repository=tx_repository,
            )
            if inserted != item_count:
                raise BatchCreatePromotionError(
                    f"Inserted {inserted} batch items but expected {item_count} for session '{session_id}'",
                    code="item_count_mismatch",
                    retryable=False,
                )

            completed = await tx_repository.create_sessions.mark_session_completed(
                session.session_id,
                completed_at=datetime.now(tz=UTC),
                expires_at=None,
                increment_promotion_attempt_count=True,
                from_statuses=tuple(_PROMOTABLE_SESSION_STATUSES),
            )
            if completed is None:
                raise BatchCreatePromotionError(
                    f"Failed to mark batch create session '{session_id}' completed",
                    code="session_update_failed",
                    retryable=True,
                )
            if self.scheduler_shadow_enabled and scheduler_version.endswith("_shadow"):
                increment_batch_scheduler_shadow_record(result="recorded")

            return BatchCreatePromotionResult(
                session_id=completed.session_id,
                batch_id=job.batch_id,
                promoted=True,
                job=job,
            )

    def _scope_limit_target(
        self,
        session: BatchCreateSessionRecord,
    ) -> tuple[str, str, str | None, str | None] | None:
        return batch_pending_scope_target_for_session(session)

    async def _precheck_pending_capacity(self, session: BatchCreateSessionRecord) -> None:
        if not self.soft_precheck_enabled or self.max_pending_batches_per_scope <= 0:
            return
        scope = self._scope_limit_target(session)
        if scope is None:
            return
        _scope_type, _scope_id, created_by_api_key, created_by_team_id = scope
        active_jobs = await self.repository.count_active_jobs_for_scope(
            created_by_api_key=created_by_api_key,
            created_by_team_id=created_by_team_id,
        )
        self._raise_if_pending_capacity_exceeded(active_jobs)

    async def _enforce_pending_capacity(self, *, session: BatchCreateSessionRecord, repository: BatchRepository) -> None:
        if self.max_pending_batches_per_scope <= 0:
            return
        scope = self._scope_limit_target(session)
        if scope is None:
            return
        scope_type, scope_id, created_by_api_key, created_by_team_id = scope
        await repository.acquire_scope_advisory_lock(scope_type=scope_type, scope_id=scope_id)
        active_jobs = await repository.count_active_jobs_for_scope(
            created_by_api_key=created_by_api_key,
            created_by_team_id=created_by_team_id,
        )
        self._raise_if_pending_capacity_exceeded(active_jobs)

    async def _enforce_tenant_queued_work_capacity(
        self,
        *,
        session: BatchCreateSessionRecord,
        scheduling_summary: BatchCreatePromotionSchedulingSummary,
        repository: BatchRepository,
    ) -> None:
        if self.tenant_max_queued_work_units <= 0:
            return
        dimensions = build_scheduling_dimensions(
            endpoint=session.endpoint,
            model=session.inferred_model,
            model_group=scheduling_summary.scheduling_model_group,
            organization_id=session.created_by_organization_id,
            team_id=session.created_by_team_id,
            api_key=session.created_by_api_key,
            user_id=session.created_by_user_id,
            service_tier=session.effective_service_tier or self.default_service_tier,
            estimated_work_units=scheduling_summary.estimated_work_units,
            remaining_work_units=scheduling_summary.remaining_work_units,
            tenant_scope_preference=self.tenant_scope_preference,
        )
        await repository.acquire_scope_advisory_lock(
            scope_type=dimensions.tenant_scope_type,
            scope_id=dimensions.tenant_scope_id,
        )
        queued_work_units = await repository.get_tenant_queued_work_units(
            tenant_scope_type=dimensions.tenant_scope_type,
            tenant_scope_id=dimensions.tenant_scope_id,
            created_by_api_key=session.created_by_api_key,
            created_by_team_id=session.created_by_team_id,
            created_by_organization_id=session.created_by_organization_id,
            created_by_user_id=session.created_by_user_id,
        )
        if queued_work_units + max(0, int(scheduling_summary.remaining_work_units or 0)) <= self.tenant_max_queued_work_units:
            return
        raise BatchCreatePromotionError(
            (
                "Tenant queued batch work exceeds "
                f"embeddings_batch_tenant_max_queued_work_units ({self.tenant_max_queued_work_units})"
            ),
            code="tenant_queued_work_limit_exceeded",
            retryable=True,
        )

    def _raise_if_pending_capacity_exceeded(self, active_jobs: int) -> None:
        if int(active_jobs) < self.max_pending_batches_per_scope:
            return
        raise BatchCreatePromotionError(
            (
                "Active batch count exceeds "
                f"embeddings_batch_max_pending_batches_per_scope ({self.max_pending_batches_per_scope})"
            ),
            code="pending_limit_exceeded",
            retryable=True,
        )

    async def _insert_items_from_spool(
        self,
        *,
        batch_id: str,
        spool: Any,
        repository: BatchRepository,
    ) -> int:
        inserted = 0
        buffer: list[BatchItemCreate] = []
        while True:
            raw_line = await asyncio.to_thread(spool.readline)
            if not raw_line:
                break
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            record = BatchCreateStagedRequest.from_jsonable(payload)
            buffer.append(
                BatchItemCreate(
                    line_number=record.line_number,
                    custom_id=record.custom_id,
                    request_body=record.request_body,
                    scheduling_model=record.scheduling_model,
                    scheduling_model_group=record.scheduling_model_group,
                    estimated_work_units=int(record.estimated_work_units or 1),
                    not_before_at=record.not_before_at,
                )
            )
            if len(buffer) >= self.insert_chunk_size:
                inserted += await repository.create_items(batch_id, buffer)
                buffer.clear()
        if buffer:
            inserted += await repository.create_items(batch_id, buffer)
        return inserted

    async def _record_failure(
        self,
        *,
        session: BatchCreateSessionRecord,
        error: BatchCreatePromotionError,
    ) -> None:
        if session.status not in _PROMOTABLE_SESSION_STATUSES:
            return

        recorder = (
            self.repository.create_sessions.mark_session_failed_retryable
            if error.retryable
            else self.repository.create_sessions.mark_session_failed_permanent
        )
        try:
            await recorder(
                session.session_id,
                error_code=error.code,
                error_message=str(error),
                attempted_at=datetime.now(tz=UTC),
                expires_at=None,
                increment_promotion_attempt_count=True,
                from_statuses=tuple(_PROMOTABLE_SESSION_STATUSES),
            )
        except Exception:
            logger.warning(
                "batch create-session promotion failure state update failed session_id=%s code=%s",
                session.session_id,
                error.code,
                exc_info=True,
            )
