from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Callable, Iterable

from src.batch.create.models import BatchCreateSessionCreate, BatchCreateSessionRecord, BatchCreateStagedRequest
from src.batch.create.session_repository import BatchCreateSessionRepository
from src.batch.create.staging import (
    BatchCreateStagingBackend,
    StagedBatchCreateArtifact,
)
from src.metrics import increment_batch_create_session_action

logger = logging.getLogger(__name__)
_COMPENSATION_DELETE_RETRY_DELAYS_SECONDS = (0.0, 0.1, 0.25)


class BatchCreateSessionStager:
    def __init__(
        self,
        *,
        repository: BatchCreateSessionRepository,
        staging: BatchCreateStagingBackend,
    ) -> None:
        self.repository = repository
        self.staging = staging

    async def stage_session(
        self,
        *,
        records: Iterable[BatchCreateStagedRequest] | AsyncIterator[BatchCreateStagedRequest],
        filename: str,
        build_session: Callable[[StagedBatchCreateArtifact], BatchCreateSessionCreate],
    ) -> BatchCreateSessionRecord:
        artifact = await self.staging.write_records(records, filename=filename)

        try:
            session = build_session(artifact)
            created = await self.repository.create_session(session)
            if created is None:
                raise RuntimeError("batch create session insert returned no row")
        except Exception:
            increment_batch_create_session_action(action="stage_session_create", status="error")
            await self._compensate_orphaned_artifact(artifact)
            raise

        increment_batch_create_session_action(action="stage_session_create", status="success")
        return created

    async def _compensate_orphaned_artifact(self, artifact: StagedBatchCreateArtifact) -> None:
        for attempt, delay_seconds in enumerate(_COMPENSATION_DELETE_RETRY_DELAYS_SECONDS, start=1):
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
            try:
                await self.staging.delete(artifact)
            except Exception:
                if attempt < len(_COMPENSATION_DELETE_RETRY_DELAYS_SECONDS):
                    logger.warning(
                        "batch create-session stage compensation delete retrying attempt=%s backend=%s storage_key=%s",
                        attempt,
                        artifact.storage_backend,
                        artifact.storage_key,
                        exc_info=True,
                    )
                    continue
                increment_batch_create_session_action(action="stage_compensate_delete", status="error")
                logger.warning(
                    "batch create-session stage compensation delete failed backend=%s storage_key=%s",
                    artifact.storage_backend,
                    artifact.storage_key,
                    exc_info=True,
                )
                return

            increment_batch_create_session_action(action="stage_compensate_delete", status="success")
            return
