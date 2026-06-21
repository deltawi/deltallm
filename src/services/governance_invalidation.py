from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Iterable
from uuid import uuid4

logger = logging.getLogger(__name__)

GOVERNANCE_INVALIDATION_CHANNEL = "governance_invalidation"
_ALLOWED_TARGETS = frozenset({"callable_target", "mcp", "tier_policy"})


class GovernanceInvalidationApplyError(RuntimeError):
    def __init__(self, failed_targets: tuple[str, ...]) -> None:
        self.failed_targets = failed_targets
        super().__init__(
            "failed applying governance invalidation for targets: " + ", ".join(failed_targets)
        )


class GovernanceInvalidationService:
    def __init__(
        self,
        *,
        redis_client: Any | None,
        callable_target_grant_service: Any | None = None,
        tier_policy_service: Any | None = None,
        mcp_registry_service: Any | None = None,
        mcp_governance_service: Any | None = None,
        channel_name: str = GOVERNANCE_INVALIDATION_CHANNEL,
        remote_apply_delay_seconds: float = 0.05,
        remote_retry_delay_seconds: float = 1.0,
    ) -> None:
        self.redis = redis_client
        self.callable_target_grant_service = callable_target_grant_service
        self.tier_policy_service = tier_policy_service
        self.mcp_registry_service = mcp_registry_service
        self.mcp_governance_service = mcp_governance_service
        self.channel_name = channel_name
        self.remote_apply_delay_seconds = max(float(remote_apply_delay_seconds), 0.0)
        self.remote_retry_delay_seconds = max(
            float(remote_retry_delay_seconds),
            self.remote_apply_delay_seconds,
        )
        self.instance_id = uuid4().hex
        self._pubsub_task: asyncio.Task[None] | None = None
        self._remote_apply_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._apply_lock = asyncio.Lock()
        self._remote_targets_lock = asyncio.Lock()
        self._ready = asyncio.Event()
        self._remote_targets: set[str] = set()

    async def start(self) -> None:
        if self.redis is None or self._pubsub_task is not None:
            return
        self._pubsub_task = asyncio.create_task(self._listen())
        await self._ready.wait()

    async def close(self) -> None:
        self._stopping = True
        if self._pubsub_task is not None:
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
            failed_targets = await self._apply_targets(normalized_targets)
        if failed_targets:
            await self._queue_remote_targets(failed_targets, retry=True)
            raise GovernanceInvalidationApplyError(failed_targets)

    async def notify(self, *targets: str) -> bool:
        normalized_targets = self._normalize_targets(targets)
        if not normalized_targets or self.redis is None:
            return False
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
            return False
        return True

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
                await self._queue_remote_targets(targets)
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

    async def _queue_remote_targets(self, targets: tuple[str, ...], *, retry: bool = False) -> None:
        async with self._remote_targets_lock:
            self._remote_targets.update(targets)
        if self._remote_apply_task is None or self._remote_apply_task.done():
            self._remote_apply_task = asyncio.create_task(self._flush_remote_targets(retry=retry))

    async def _flush_remote_targets(self, *, retry: bool = False) -> None:
        targets: tuple[str, ...] = ()
        failed_targets: tuple[str, ...] = ()
        delay_seconds = (
            self.remote_retry_delay_seconds if retry else self.remote_apply_delay_seconds
        )
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
        try:
            async with self._remote_targets_lock:
                targets = tuple(sorted(self._remote_targets))
                self._remote_targets.difference_update(targets)
            if not targets:
                return

            async with self._apply_lock:
                failed_targets = await self._apply_targets(targets)
            if failed_targets:
                async with self._remote_targets_lock:
                    self._remote_targets.update(failed_targets)
                logger.warning(
                    "remote governance invalidation targets failed and will be retried: %s",
                    ", ".join(failed_targets),
                )
        except asyncio.CancelledError:
            if targets:
                async with self._remote_targets_lock:
                    self._remote_targets.update(targets)
            raise
        finally:
            if not self._stopping:
                async with self._remote_targets_lock:
                    has_pending_targets = bool(self._remote_targets)
                if has_pending_targets:
                    self._remote_apply_task = asyncio.create_task(
                        self._flush_remote_targets(retry=bool(failed_targets))
                    )

    async def _apply_targets(self, targets: tuple[str, ...]) -> tuple[str, ...]:
        failed_targets: list[str] = []
        if "callable_target" in targets:
            service = self.callable_target_grant_service
            if service is not None and callable(getattr(service, "reload", None)):
                try:
                    await service.reload()
                except Exception as exc:
                    failed_targets.append("callable_target")
                    logger.warning("failed applying callable target invalidation: %s", exc)
        if "tier_policy" in targets:
            service = self.tier_policy_service
            if _service_mode(service) == "disabled":
                service = None
            if service is not None and callable(getattr(service, "reload", None)):
                try:
                    await service.reload()
                except Exception as exc:
                    failed_targets.append("tier_policy")
                    logger.warning("failed applying tier policy invalidation: %s", exc)
        if "mcp" in targets:
            target_failed = False
            registry = self.mcp_registry_service
            if registry is not None and callable(getattr(registry, "invalidate_all", None)):
                try:
                    await registry.invalidate_all()
                except Exception as exc:
                    target_failed = True
                    logger.warning("failed applying MCP registry invalidation: %s", exc)
            governance = self.mcp_governance_service
            if governance is not None and callable(getattr(governance, "reload", None)):
                try:
                    await governance.reload()
                except Exception as exc:
                    target_failed = True
                    logger.warning("failed applying MCP governance invalidation: %s", exc)
            if target_failed:
                failed_targets.append("mcp")
        return tuple(failed_targets)

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


def _service_mode(service: Any | None) -> str | None:
    if service is None:
        return None
    mode = str(getattr(service, "mode", "") or "").strip().lower()
    return mode or None
