from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from src.db.repositories import AuditRepository

logger = logging.getLogger(__name__)


@dataclass
class AuditRetentionConfig:
    interval_seconds: float = 86_400.0
    scan_limit: int = 500
    metadata_retention_days: int = 365
    payload_retention_days: int = 90


class AuditRetentionWorker:
    def __init__(self, *, repository: AuditRepository, config: AuditRetentionConfig) -> None:
        self.repository = repository
        self.config = config
        self._running = False

    async def run(self) -> None:
        self._running = True
        while self._running:
            await self.process_once()
            await asyncio.sleep(self.config.interval_seconds)

    def stop(self) -> None:
        self._running = False

    async def process_once(self) -> tuple[int, int]:
        payload_ids = await self.repository.list_expired_payload_ids(
            default_retention_days=max(1, int(self.config.payload_retention_days)),
            limit=max(1, int(self.config.scan_limit)),
        )
        deleted_payloads = await self.repository.delete_payloads_by_ids(payload_ids)

        event_ids = await self.repository.list_expired_event_ids(
            default_retention_days=max(1, int(self.config.metadata_retention_days)),
            limit=max(1, int(self.config.scan_limit)),
        )
        deleted_events = await self.repository.delete_events_by_ids(event_ids)

        if deleted_payloads or deleted_events:
            logger.info(
                "audit retention deleted payloads=%s events=%s",
                deleted_payloads,
                deleted_events,
            )
        return deleted_payloads, deleted_events
