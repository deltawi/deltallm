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
from src.batch.create.models import BatchCreateSessionRecord, BatchCreateSessionStatus
from src.batch.scopes import batch_pending_scope_target_for_session
from src.batch.create.staging import BatchCreateStagingBackend, staged_artifact_from_session
from src.batch.models import BatchItemCreate, BatchJobRecord, BatchJobStatus
from src.batch.storage import BatchArtifactLineTooLongError
from src.metrics import increment_batch_create_session_action

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
    ) -> None:
        self.repository = repository
        self.staging = staging
        self.metadata_retention_days = max(1, int(metadata_retention_days))
        self.max_pending_batches_per_scope = max(0, int(max_pending_batches_per_scope))
        self.insert_chunk_size = max(1, int(insert_chunk_size))
        self.soft_precheck_enabled = bool(soft_precheck_enabled)
        self.tx_max_wait = timedelta(seconds=max(float(tx_max_wait_seconds), 0.001))
        self.tx_timeout = timedelta(seconds=max(float(tx_timeout_seconds), 0.001))

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
            spool, item_count = await self._spool_staged_items(session)
        except BatchCreatePromotionError as exc:
            await self._record_failure(session=session, error=exc)
            increment_batch_create_session_action(action="promotion", status="error")
            raise

        try:
            result = await self._promote_spooled_session(
                session_id=session_id,
                spool=spool,
                item_count=item_count,
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

    async def _spool_staged_items(self, session: BatchCreateSessionRecord) -> tuple[Any, int]:
        artifact = staged_artifact_from_session(session)
        spool = tempfile.SpooledTemporaryFile(
            max_size=max(1_048_576, self.insert_chunk_size * 2_048),
            mode="w+t",
            encoding="utf-8",
            newline="\n",
        )
        count = 0
        try:
            async for record in self.staging.read_records(artifact):
                count += 1
                await asyncio.to_thread(
                    spool.write,
                    json.dumps(record.to_jsonable(), separators=(",", ":"), ensure_ascii=True) + "\n",
                )
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
        return spool, count

    async def _promote_spooled_session(
        self,
        *,
        session_id: str,
        spool: Any,
        item_count: int,
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

            await asyncio.to_thread(spool.seek, 0)
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
            buffer.append(
                BatchItemCreate(
                    line_number=int(payload["line_number"]),
                    custom_id=str(payload["custom_id"]),
                    request_body=dict(payload["request_body"]),
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
