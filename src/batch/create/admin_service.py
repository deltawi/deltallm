from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException, status

from src.batch.create.models import (
    BatchCreateSessionRecord,
    BatchCreateSessionStatus,
)
from src.batch.create.promoter import BatchCreatePromotionError, BatchCreatePromotionResult, BatchCreateSessionPromoter
from src.batch.create.session_repository import BatchCreateSessionRepository
from src.batch.create.staging import BatchCreateArtifactStorageBackend, staged_artifact_from_session
from src.metrics import increment_batch_create_session_action, publish_batch_create_session_summary

logger = logging.getLogger(__name__)

_RETRYABLE_ADMIN_STATUS_SEQUENCE = (
    BatchCreateSessionStatus.STAGED,
    BatchCreateSessionStatus.FAILED_RETRYABLE,
)
_RETRYABLE_ADMIN_STATUSES = frozenset(_RETRYABLE_ADMIN_STATUS_SEQUENCE)
_EXPIRABLE_ADMIN_STATUS_SEQUENCE = (
    BatchCreateSessionStatus.STAGED,
    BatchCreateSessionStatus.FAILED_RETRYABLE,
    BatchCreateSessionStatus.FAILED_PERMANENT,
)
_EXPIRABLE_ADMIN_STATUSES = frozenset(_EXPIRABLE_ADMIN_STATUS_SEQUENCE)


@dataclass
class BatchCreateSessionRetryResult:
    session: BatchCreateSessionRecord
    promotion: BatchCreatePromotionResult


@dataclass
class BatchCreateSessionExpireResult:
    session: BatchCreateSessionRecord
    artifact_deleted: bool


class BatchCreateSessionAdminService:
    def __init__(
        self,
        *,
        repository: BatchCreateSessionRepository,
        promoter: BatchCreateSessionPromoter,
        staging: BatchCreateArtifactStorageBackend,
    ) -> None:
        self.repository = repository
        self.promoter = promoter
        self.staging = staging

    async def retry_session(self, session_id: str) -> BatchCreateSessionRetryResult:
        session = await self._get_session_or_404(session_id)
        if session.status not in _RETRYABLE_ADMIN_STATUSES:
            increment_batch_create_session_action(action="admin_retry", status="rejected")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Batch create session cannot be retried from "
                    f"'{session.status}' status"
                ),
            )

        try:
            promotion = await self.promoter.promote_session(session_id)
        except BatchCreatePromotionError as exc:
            increment_batch_create_session_action(action="admin_retry", status="error")
            await self._refresh_summary_metrics()
            logger.warning(
                "batch create-session admin retry failed session_id=%s batch_id=%s code=%s retryable=%s",
                session.session_id,
                session.target_batch_id,
                exc.code,
                exc.retryable,
            )
            raise self._http_exception_for_promotion_error(exc) from exc

        refreshed = await self.repository.get_session(session_id)
        if refreshed is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Batch create session '{session_id}' disappeared after retry",
            )
        increment_batch_create_session_action(action="admin_retry", status="success")
        await self._refresh_summary_metrics()
        logger.info(
            "batch create-session admin retry session_id=%s batch_id=%s result=%s",
            refreshed.session_id,
            refreshed.target_batch_id,
            "promoted" if promotion.promoted else "existing_batch",
        )
        return BatchCreateSessionRetryResult(session=refreshed, promotion=promotion)

    async def expire_session(self, session_id: str) -> BatchCreateSessionExpireResult:
        session = await self._get_session_or_404(session_id)
        if session.status not in _EXPIRABLE_ADMIN_STATUSES:
            increment_batch_create_session_action(action="admin_expire", status="rejected")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Batch create session cannot be expired from "
                    f"'{session.status}' status"
                ),
            )

        expired_at = datetime.now(tz=UTC)
        updated = await self.repository.mark_session_expired(
            session_id,
            expired_at=expired_at,
            from_statuses=_EXPIRABLE_ADMIN_STATUS_SEQUENCE,
        )
        if updated is None:
            refreshed = await self.repository.get_session(session_id)
            if refreshed is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch create session not found")
            increment_batch_create_session_action(action="admin_expire", status="rejected")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Batch create session changed while expiring and is now in "
                    f"'{refreshed.status}' status"
                ),
            )

        artifact_deleted = True
        try:
            await self.staging.delete(staged_artifact_from_session(updated))
        except Exception:
            artifact_deleted = False
            increment_batch_create_session_action(action="admin_expire_artifact_delete", status="error")
            logger.warning(
                "batch create-session admin expire artifact delete failed session_id=%s batch_id=%s",
                updated.session_id,
                updated.target_batch_id,
                exc_info=True,
            )
        else:
            increment_batch_create_session_action(action="admin_expire_artifact_delete", status="success")

        increment_batch_create_session_action(action="admin_expire", status="success")
        await self._refresh_summary_metrics()
        logger.info(
            "batch create-session admin expire session_id=%s batch_id=%s artifact_deleted=%s",
            updated.session_id,
            updated.target_batch_id,
            artifact_deleted,
        )
        return BatchCreateSessionExpireResult(session=updated, artifact_deleted=artifact_deleted)

    async def _get_session_or_404(self, session_id: str) -> BatchCreateSessionRecord:
        session = await self.repository.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch create session not found")
        return session

    async def _refresh_summary_metrics(self) -> None:
        try:
            summary = await self.repository.summarize_statuses()
            publish_batch_create_session_summary(summary)
        except Exception:
            logger.debug("batch create-session admin summary metrics refresh failed", exc_info=True)

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
