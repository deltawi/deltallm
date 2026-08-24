from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Iterable, Protocol
from uuid import uuid4

from src.metrics import increment_config_reload

logger = logging.getLogger(__name__)

GOVERNANCE_INVALIDATION_CHANNEL = "governance_invalidation"
_ALLOWED_TARGETS = frozenset({"callable_target", "mcp", "prompt", "route_groups", "tier_policy"})


class RouteGroupRevisionSource(Protocol):
    async def get_runtime_revision(self) -> int: ...


class RoutingAppliedState(Protocol):
    revision: int
    requires_reconciliation: bool


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
        prompt_registry_service: Any | None = None,
        route_group_reload: Any | None = None,
        route_group_revision_source: RouteGroupRevisionSource | None = None,
        route_group_applied_revision: Callable[[], int] | None = None,
        routing_applied_state: Callable[[], RoutingAppliedState] | None = None,
        channel_name: str = GOVERNANCE_INVALIDATION_CHANNEL,
        remote_apply_delay_seconds: float = 0.05,
        remote_retry_delay_seconds: float = 1.0,
        route_group_poll_interval_seconds: float = 30.0,
    ) -> None:
        self.redis = redis_client
        self.callable_target_grant_service = callable_target_grant_service
        self.tier_policy_service = tier_policy_service
        self.mcp_registry_service = mcp_registry_service
        self.mcp_governance_service = mcp_governance_service
        self.prompt_registry_service = prompt_registry_service
        self.route_group_reload = route_group_reload
        self.route_group_revision_source = route_group_revision_source
        self.route_group_applied_revision = route_group_applied_revision
        self.routing_applied_state = routing_applied_state
        self.channel_name = channel_name
        self.remote_apply_delay_seconds = max(float(remote_apply_delay_seconds), 0.0)
        self.remote_retry_delay_seconds = max(
            float(remote_retry_delay_seconds),
            self.remote_apply_delay_seconds,
        )
        self.route_group_poll_interval_seconds = max(float(route_group_poll_interval_seconds), 0.0)
        self.instance_id = uuid4().hex
        self._pubsub_task: asyncio.Task[None] | None = None
        self._remote_apply_task: asyncio.Task[None] | None = None
        self._route_group_poll_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._apply_lock = asyncio.Lock()
        self._remote_targets_lock = asyncio.Lock()
        self._ready = asyncio.Event()
        self._remote_targets: set[str] = set()

    async def start(self) -> None:
        if (
            self.route_group_revision_source is not None
            and callable(self.route_group_reload)
            and self.route_group_poll_interval_seconds > 0
            and self._route_group_poll_task is None
        ):
            self._route_group_poll_task = asyncio.create_task(self._poll_route_group_revision())
        if self.redis is not None and self._pubsub_task is None:
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
        if self._route_group_poll_task is not None:
            self._route_group_poll_task.cancel()
            try:
                await self._route_group_poll_task
            except asyncio.CancelledError:
                pass
            self._route_group_poll_task = None

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

        retry_seconds = 0.25
        while not self._stopping:
            pubsub = None
            try:
                pubsub = self.redis.pubsub()
                await pubsub.subscribe(self.channel_name)
                self._ready.set()
                retry_seconds = 0.25
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    await self._handle_pubsub_message(message)
                if not self._stopping:
                    raise RuntimeError("governance invalidation listener stopped")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not self._stopping:
                    logger.error("governance invalidation pub/sub error: %s", exc)
            finally:
                self._ready.set()
                if pubsub is not None:
                    try:
                        await pubsub.unsubscribe(self.channel_name)
                    except Exception:
                        pass
                    try:
                        await pubsub.close()
                    except Exception:
                        if not self._stopping:
                            logger.debug(
                                "failed closing governance invalidation pub/sub",
                                exc_info=True,
                            )
            if not self._stopping:
                await asyncio.sleep(retry_seconds)
                retry_seconds = min(retry_seconds * 2, 10.0)

    async def _handle_pubsub_message(self, message: dict[str, Any]) -> None:
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
            return
        if data.get("source_instance") == self.instance_id:
            return
        targets = self._normalize_targets(data.get("targets") or [])
        if targets:
            await self._queue_remote_targets(targets)

    async def _poll_route_group_revision(self) -> None:
        while not self._stopping:
            await asyncio.sleep(self.route_group_poll_interval_seconds)
            source = self.route_group_revision_source
            if source is None or not callable(self.route_group_reload):
                continue
            try:
                durable_revision = await source.get_runtime_revision()
                applied_state = (
                    self.routing_applied_state() if self.routing_applied_state is not None else None
                )
                applied_revision = (
                    int(applied_state.revision)
                    if applied_state is not None
                    else (
                        self.route_group_applied_revision()
                        if self.route_group_applied_revision is not None
                        else 0
                    )
                )
                requires_reconciliation = bool(
                    applied_state is not None and applied_state.requires_reconciliation
                )
                if durable_revision <= applied_revision and not requires_reconciliation:
                    increment_config_reload(source="route_group_poll", result="unchanged")
                    continue
                await self.route_group_reload()
                increment_config_reload(source="route_group_poll", result="applied")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                increment_config_reload(source="route_group_poll", result="failed")
                logger.warning("route-group revision reconciliation failed: %s", exc)

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
        routing_targets = tuple(
            target for target in ("callable_target", "route_groups") if target in targets
        )
        if routing_targets and callable(self.route_group_reload):
            try:
                await self.route_group_reload()
            except Exception as exc:
                failed_targets.extend(routing_targets)
                logger.warning(
                    "failed applying routing-generation invalidation for %s: %s",
                    ", ".join(routing_targets),
                    exc,
                )
        elif "callable_target" in targets:
            # Compatibility for governance services embedded without the main
            # routing runtime. Production always rebuilds the complete routing
            # generation so authorization and routing publish atomically.
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
        if "prompt" in targets:
            service = self.prompt_registry_service
            if service is not None and callable(getattr(service, "refresh_namespace_epoch", None)):
                try:
                    await service.refresh_namespace_epoch()
                except Exception as exc:
                    failed_targets.append("prompt")
                    logger.warning("failed applying prompt invalidation: %s", exc)
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
