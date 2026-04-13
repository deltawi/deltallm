from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from src.batch.create.defaults import (
    DEFAULT_CREATE_SESSION_CLEANUP_INTERVAL_SECONDS,
    DEFAULT_CREATE_SESSION_CLEANUP_SCAN_LIMIT,
    DEFAULT_CREATE_SESSION_COMPLETED_RETENTION_SECONDS,
    DEFAULT_CREATE_SESSION_FAILED_RETENTION_SECONDS,
    DEFAULT_CREATE_SESSION_ORPHAN_GRACE_SECONDS,
    DEFAULT_CREATE_SESSION_RETRYABLE_RETENTION_SECONDS,
)
from src.batch.create.session_repository import BatchCreateSessionRepository
from src.batch.create.staging import BatchCreateStagingBackend, staged_artifact_from_session
from src.metrics import increment_batch_create_session_action, publish_batch_create_session_summary

logger = logging.getLogger(__name__)


@dataclass
class BatchCreateSessionCleanupConfig:
    interval_seconds: float = DEFAULT_CREATE_SESSION_CLEANUP_INTERVAL_SECONDS
    scan_limit: int = DEFAULT_CREATE_SESSION_CLEANUP_SCAN_LIMIT
    orphan_grace_seconds: int = DEFAULT_CREATE_SESSION_ORPHAN_GRACE_SECONDS
    completed_retention_seconds: int = DEFAULT_CREATE_SESSION_COMPLETED_RETENTION_SECONDS
    retryable_retention_seconds: int = DEFAULT_CREATE_SESSION_RETRYABLE_RETENTION_SECONDS
    failed_retention_seconds: int = DEFAULT_CREATE_SESSION_FAILED_RETENTION_SECONDS


class BatchCreateSessionCleanupWorker:
    def __init__(
        self,
        *,
        repository: BatchCreateSessionRepository,
        staging: BatchCreateStagingBackend,
        config: BatchCreateSessionCleanupConfig | None = None,
    ) -> None:
        self.repository = repository
        self.staging = staging
        self.config = config or BatchCreateSessionCleanupConfig()
        self._stop_event = asyncio.Event()

    async def _refresh_create_session_metrics(self) -> None:
        try:
            summary = await self.repository.summarize_statuses()
            publish_batch_create_session_summary(summary)
        except Exception:
            logger.debug("batch create-session cleanup metrics refresh failed", exc_info=True)

    async def process_once(self) -> tuple[int, int]:
        now = datetime.now(tz=UTC)
        deleted_sessions = 0
        deleted_artifacts = 0
        completed_before = now - timedelta(seconds=self.config.completed_retention_seconds)
        retryable_before = now - timedelta(seconds=self.config.retryable_retention_seconds)
        failed_before = now - timedelta(seconds=self.config.failed_retention_seconds)
        cleanup_candidates = await self.repository.list_cleanup_candidates(
            now=now,
            completed_before=completed_before,
            retryable_before=retryable_before,
            failed_before=failed_before,
            limit=self.config.scan_limit,
        )

        for session in cleanup_candidates:
            deleted_session = await self.repository.delete_cleanup_candidate(session)
            if deleted_session is None:
                increment_batch_create_session_action(action="cleanup_row_delete", status="skipped")
                logger.debug(
                    "batch create-session cleanup row changed before delete session_id=%s",
                    session.session_id,
                )
                continue

            deleted_sessions += 1
            artifact = staged_artifact_from_session(deleted_session)
            try:
                await self.staging.delete(artifact)
            except Exception as exc:
                increment_batch_create_session_action(action="cleanup_delete", status="error")
                logger.warning(
                    "batch create-session cleanup artifact delete failed after row removal session_id=%s backend=%s storage_key=%s error=%s",
                    deleted_session.session_id,
                    artifact.storage_backend,
                    artifact.storage_key,
                    exc,
                )
                continue

            deleted_artifacts += 1
            increment_batch_create_session_action(action=f"cleanup_delete_{deleted_session.status}", status="success")

        orphan_cutoff = now - timedelta(seconds=self.config.orphan_grace_seconds)
        orphan_candidates = await self.staging.list_orphan_candidates(
            older_than=orphan_cutoff,
            limit=self.config.scan_limit,
        )
        for artifact in orphan_candidates:
            if await self.repository.is_stage_artifact_referenced(
                storage_backend=artifact.storage_backend,
                storage_key=artifact.storage_key,
            ):
                continue
            try:
                await self.staging.delete(artifact)
            except Exception as exc:
                increment_batch_create_session_action(action="cleanup_orphan_delete", status="error")
                logger.warning(
                    "batch create-session cleanup orphan artifact delete failed backend=%s storage_key=%s error=%s",
                    artifact.storage_backend,
                    artifact.storage_key,
                    exc,
                )
                continue
            deleted_artifacts += 1
            increment_batch_create_session_action(action="cleanup_orphan_delete", status="success")

        if deleted_sessions or deleted_artifacts:
            logger.info(
                "batch create-session cleanup deleted_sessions=%s deleted_artifacts=%s",
                deleted_sessions,
                deleted_artifacts,
            )

        await self._refresh_create_session_metrics()
        return deleted_sessions, deleted_artifacts

    async def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.process_once()
            except Exception:
                logger.exception("batch create-session cleanup iteration failed")
            if self._stop_event.is_set():
                break
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=max(0.1, float(self.config.interval_seconds)),
                )
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        self._stop_event.set()
