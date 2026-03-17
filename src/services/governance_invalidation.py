from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Iterable
from uuid import uuid4

logger = logging.getLogger(__name__)

GOVERNANCE_INVALIDATION_CHANNEL = "governance_invalidation"
_ALLOWED_TARGETS = frozenset({"callable_target", "mcp"})


class GovernanceInvalidationService:
    def __init__(
        self,
        *,
        redis_client: Any | None,
        callable_target_grant_service: Any | None = None,
        mcp_registry_service: Any | None = None,
        mcp_governance_service: Any | None = None,
        channel_name: str = GOVERNANCE_INVALIDATION_CHANNEL,
        remote_apply_delay_seconds: float = 0.05,
    ) -> None:
        self.redis = redis_client
        self.callable_target_grant_service = callable_target_grant_service
        self.mcp_registry_service = mcp_registry_service
        self.mcp_governance_service = mcp_governance_service
        self.channel_name = channel_name
        self.remote_apply_delay_seconds = max(float(remote_apply_delay_seconds), 0.0)
        self.instance_id = uuid4().hex
        self._pubsub_task: asyncio.Task[None] | None = None
        self._remote_apply_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._apply_lock = asyncio.Lock()
        self._ready = asyncio.Event()
        self._remote_targets: set[str] = set()

    async def start(self) -> None:
        if self.redis is None or self._pubsub_task is not None:
            return
        self._pubsub_task = asyncio.create_task(self._listen())
        await self._ready.wait()

    async def close(self) -> None:
        self._stopping = True
        if self._pubsub_task is None:
            return
        self._pubsub_task.cancel()
        try:
            await self._pubsub_task
        except asyncio.CancelledError:
            pass
        self._pubsub_task = None
        if self._remote_apply_task is not None:
            self._remote_apply_task.cancel()
            try:
                await self._remote_apply_task
            except asyncio.CancelledError:
                pass
            self._remote_apply_task = None

    async def invalidate_local(self, *targets: str) -> None:
        normalized_targets = self._normalize_targets(targets)
        if not normalized_targets:
            return
        async with self._apply_lock:
            await self._apply_targets(normalized_targets)

    async def notify(self, *targets: str) -> None:
        normalized_targets = self._normalize_targets(targets)
        if not normalized_targets or self.redis is None:
            return
        payload = json.dumps(
            {
                "type": "governance_invalidation",
                "targets": list(normalized_targets),
                "source_instance": self.instance_id,
                "timestamp": time.time(),
            }
        )
        try:
            await self.redis.publish(self.channel_name, payload)
        except Exception as exc:
            logger.warning("failed publishing governance invalidation: %s", exc)

    async def _listen(self) -> None:
        if self.redis is None:
            return

        pubsub = self.redis.pubsub()
        try:
            await pubsub.subscribe(self.channel_name)
            self._ready.set()
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue

                payload = message.get("data")
                if isinstance(payload, (bytes, bytearray)):
                    payload = payload.decode("utf-8")

                data: dict[str, Any] = {}
                if isinstance(payload, str):
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        data = {}
                if data.get("type") != "governance_invalidation":
                    continue
                if data.get("source_instance") == self.instance_id:
                    continue
                targets = self._normalize_targets(data.get("targets") or [])
                if not targets:
                    continue
                self._queue_remote_targets(targets)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._stopping:
                logger.error("governance invalidation pub/sub error: %s", exc)
        finally:
            self._ready.set()
            try:
                await pubsub.unsubscribe(self.channel_name)
            except Exception:
                pass
            await pubsub.close()

    def _queue_remote_targets(self, targets: tuple[str, ...]) -> None:
        self._remote_targets.update(targets)
        if self._remote_apply_task is None or self._remote_apply_task.done():
            self._remote_apply_task = asyncio.create_task(self._flush_remote_targets())

    async def _flush_remote_targets(self) -> None:
        if self.remote_apply_delay_seconds > 0:
            await asyncio.sleep(self.remote_apply_delay_seconds)
        targets = tuple(sorted(self._remote_targets))
        self._remote_targets.clear()
        if not targets:
            return
        async with self._apply_lock:
            await self._apply_targets(targets)

    async def _apply_targets(self, targets: tuple[str, ...]) -> None:
        if "callable_target" in targets:
            service = self.callable_target_grant_service
            if service is not None and callable(getattr(service, "reload", None)):
                await service.reload()
        if "mcp" in targets:
            registry = self.mcp_registry_service
            if registry is not None and callable(getattr(registry, "invalidate_all", None)):
                await registry.invalidate_all()
            governance = self.mcp_governance_service
            if governance is not None and callable(getattr(governance, "reload", None)):
                await governance.reload()

    @staticmethod
    def _normalize_targets(targets: Iterable[str]) -> tuple[str, ...]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in targets:
            target = str(item or "").strip().lower()
            if target not in _ALLOWED_TARGETS or target in seen:
                continue
            seen.add(target)
            normalized.append(target)
        return tuple(normalized)
