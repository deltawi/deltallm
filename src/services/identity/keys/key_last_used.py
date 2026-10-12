"""Best-effort API-key last-used tracking off the request path.

The data plane only stores the token hash and time in a bounded process-local
map. A supervised background loop writes the map to PostgreSQL in batches, so
each replica writes a key at most once per flush interval. The value is
operator information, not an authorization, budget, or audit input. When the
map is full, or a write fails, the update is dropped and counted; a later
request records the key again.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from src.metrics.key_last_used import KeyLastUsedEvent, record_key_last_used_event

logger = logging.getLogger(__name__)


class KeyLastUsedWriter(Protocol):
    async def write(self, usages: Mapping[str, datetime]) -> int: ...


@dataclass(frozen=True)
class KeyLastUsedLimits:
    flush_interval_seconds: float = 60.0
    flush_timeout_seconds: float = 5.0
    max_pending_keys: int = 10_000


class KeyLastUsedRecorder:
    def __init__(
        self,
        writer: KeyLastUsedWriter,
        limits: KeyLastUsedLimits | None = None,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
    ) -> None:
        self.writer = writer
        self.limits = limits or KeyLastUsedLimits()
        self._now = now
        self._pending: dict[str, datetime] = {}
        self._stopped = asyncio.Event()

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def record(self, token_hash: str) -> None:
        """Remember a successful key authentication. No I/O; safe on the request path."""
        if token_hash not in self._pending and len(self._pending) >= self.limits.max_pending_keys:
            record_key_last_used_event(KeyLastUsedEvent.DROPPED)
            return
        self._pending[token_hash] = self._now()

    async def flush(self) -> None:
        if not self._pending:
            return
        batch, self._pending = self._pending, {}
        try:
            async with asyncio.timeout(self.limits.flush_timeout_seconds):
                await self.writer.write(batch)
        except Exception:
            # Best-effort telemetry: never retry in a loop or grow memory.
            record_key_last_used_event(KeyLastUsedEvent.FLUSH_FAILED, len(batch))
            logger.warning("api key last-used flush failed", extra={"keys": len(batch)})
            return
        record_key_last_used_event(KeyLastUsedEvent.FLUSHED, len(batch))

    def stop(self) -> None:
        self._stopped.set()

    async def run(self) -> None:
        while not self._stopped.is_set():
            try:
                await asyncio.wait_for(
                    self._stopped.wait(), timeout=self.limits.flush_interval_seconds
                )
            except TimeoutError:
                pass
            await self.flush()
