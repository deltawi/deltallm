from __future__ import annotations

import asyncio
from collections import OrderedDict
import json
import logging
import time
from typing import Protocol

from src.metrics.selector import (
    SelectorSessionStoreFailure,
    observe_selector_session_store_failure,
)
from src.router.redis_keys import RouterRedisKeyspace
from src.router.selection.session import SelectorSessionPin

logger = logging.getLogger(__name__)

SESSION_ENTRY_VERSION = 1
MAX_SESSION_ENTRY_BYTES = 512
DEFAULT_LOOKUP_TIMEOUT_SECONDS = 0.05
DEFAULT_WRITE_TIMEOUT_SECONDS = 0.25
DEFAULT_MAX_PENDING_WRITES = 256
DEFAULT_MEMORY_ENTRIES = 10_000


class _SessionRedis(Protocol):
    async def get(self, name: str) -> str | bytes | None: ...

    async def set(self, name: str, value: str, *, ex: int) -> object: ...


def encode_session_pin(pin: SelectorSessionPin) -> str:
    return json.dumps(
        {"v": SESSION_ENTRY_VERSION, "r": pin.rank, "d": pin.deployment_id},
        separators=(",", ":"),
    )


def decode_session_pin(raw: str | bytes) -> SelectorSessionPin | None:
    """Entries are untrusted cache data: anything unexpected is a miss."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if len(raw) > MAX_SESSION_ENTRY_BYTES:
        return None
    try:
        document = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(document, dict) or document.get("v") != SESSION_ENTRY_VERSION:
        return None
    rank, deployment_id = document.get("r"), document.get("d")
    if type(rank) is not int or not 0 <= rank < 8:
        return None
    if not isinstance(deployment_id, str) or not deployment_id:
        return None
    return SelectorSessionPin(rank=rank, deployment_id=deployment_id)


class RedisSelectorSessionStore:
    """Shared session pins: one bounded GET per selector request, one deferred SET.

    Redis failure is a miss, so the request classifies again. Writes run after the
    answer starts, in a bounded set of owned tasks that shutdown drains or cancels.
    Concurrent writes for one session keep the last value; a pin is a routing hint,
    not an economic or security record.
    """

    def __init__(
        self,
        redis: _SessionRedis,
        *,
        keyspace: RouterRedisKeyspace,
        lookup_timeout_seconds: float = DEFAULT_LOOKUP_TIMEOUT_SECONDS,
        write_timeout_seconds: float = DEFAULT_WRITE_TIMEOUT_SECONDS,
        max_pending_writes: int = DEFAULT_MAX_PENDING_WRITES,
    ) -> None:
        self._redis = redis
        self._keyspace = keyspace
        self._lookup_timeout = lookup_timeout_seconds
        self._write_timeout = write_timeout_seconds
        self._max_pending = max_pending_writes
        self._pending: set[asyncio.Task[None]] = set()
        self._closed = False

    async def lookup(self, key: str) -> SelectorSessionPin | None:
        try:
            async with asyncio.timeout(self._lookup_timeout):
                raw = await self._redis.get(self._keyspace.selector_session(key))
        except Exception:  # Integration boundary: every Redis failure is a classified miss.
            observe_selector_session_store_failure(SelectorSessionStoreFailure.LOOKUP)
            return None
        if raw is None:
            return None
        pin = decode_session_pin(raw)
        if pin is None:
            observe_selector_session_store_failure(SelectorSessionStoreFailure.INVALID_ENTRY)
        return pin

    def remember(self, key: str, pin: SelectorSessionPin, *, ttl_seconds: int) -> None:
        if self._closed or len(self._pending) >= self._max_pending:
            observe_selector_session_store_failure(SelectorSessionStoreFailure.WRITE_DROPPED)
            return
        task = asyncio.get_running_loop().create_task(
            self._write(self._keyspace.selector_session(key), encode_session_pin(pin), ttl_seconds)
        )
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _write(self, name: str, value: str, ttl_seconds: int) -> None:
        try:
            async with asyncio.timeout(self._write_timeout):
                await self._redis.set(name, value, ex=ttl_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:  # Integration boundary: a lost pin only costs one classification.
            observe_selector_session_store_failure(SelectorSessionStoreFailure.WRITE)
            logger.debug("selector session pin write failed", exc_info=True)

    async def close(self, *, timeout_seconds: float = 1.0) -> None:
        self._closed = True
        pending = list(self._pending)
        if not pending:
            return
        _, still_running = await asyncio.wait(pending, timeout=timeout_seconds)
        for task in still_running:
            task.cancel()
        await asyncio.gather(*still_running, return_exceptions=True)


class MemorySelectorSessionStore:
    """Process-local pins for a gateway without Redis. Bounded LRU with expiry.

    With several replicas, a session that reaches another replica classifies again.
    """

    def __init__(self, *, max_entries: int = DEFAULT_MEMORY_ENTRIES) -> None:
        self._max_entries = max_entries
        self._entries: OrderedDict[str, tuple[float, SelectorSessionPin]] = OrderedDict()

    async def lookup(self, key: str) -> SelectorSessionPin | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, pin = entry
        if expires_at <= time.monotonic():
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return pin

    def remember(self, key: str, pin: SelectorSessionPin, *, ttl_seconds: int) -> None:
        self._entries[key] = (time.monotonic() + ttl_seconds, pin)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    async def close(self, *, timeout_seconds: float = 1.0) -> None:
        self._entries.clear()
