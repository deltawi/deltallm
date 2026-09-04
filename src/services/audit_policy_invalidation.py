from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
import json
import logging
import random
from typing import Protocol, runtime_checkable
from uuid import uuid4

from src.metrics import increment_audit_write_failure
from src.telemetry.lifecycle import (
    WorkerHealth,
    WorkerState,
    stop_tasks_before_deadline,
    task_failure_detail,
    wait_for_startup,
)


logger = logging.getLogger(__name__)


class AuditPolicyPubSub(Protocol):
    async def subscribe(self, channel: str) -> object: ...

    async def unsubscribe(self, channel: str) -> object: ...

    def listen(self) -> AsyncIterator[Mapping[str, object]]: ...

    async def aclose(self) -> object: ...


@runtime_checkable
class AuditPolicyRedisPublisher(Protocol):
    async def publish(self, channel: str, payload: str) -> object: ...


@runtime_checkable
class AuditPolicyRedisListener(AuditPolicyRedisPublisher, Protocol):
    def pubsub(self) -> AuditPolicyPubSub: ...


class AuditPolicyInvalidation:
    """Best-effort cross-replica acceleration for authoritative DB policy."""

    def __init__(
        self,
        *,
        redis_client: object | None,
        channel: str,
        invalidate_one: Callable[[str], None],
        invalidate_all: Callable[[], None],
    ) -> None:
        self.redis = redis_client
        self.channel = channel
        self.invalidate_one = invalidate_one
        self.invalidate_all = invalidate_all
        self.instance_id = uuid4().hex
        self._task: asyncio.Task[None] | None = None
        self._started = asyncio.Event()
        self._closed = False
        self._state = WorkerState.DISABLED
        self._detail: str | None = None

    @property
    def health(self) -> WorkerHealth:
        if not isinstance(self.redis, AuditPolicyRedisListener):
            return WorkerHealth(WorkerState.DISABLED)
        detail = task_failure_detail(self._task)
        if detail is not None:
            return WorkerHealth(WorkerState.DEGRADED, detail)
        if self._task is None:
            return WorkerHealth(WorkerState.DEGRADED, "expected listener task is missing")
        return WorkerHealth(self._state, self._detail)

    async def start(self, *, timeout_seconds: float) -> None:
        self._closed = False
        if not isinstance(self.redis, AuditPolicyRedisListener):
            self._state = WorkerState.DISABLED
            self._detail = None
            return
        if self._task is not None and not self._task.done():
            return
        self._started.clear()
        self._state = WorkerState.STARTING
        self._detail = None
        self._task = asyncio.create_task(self._listen())
        try:
            await wait_for_startup(
                started=self._started,
                task=self._task,
                timeout_seconds=timeout_seconds,
                worker_name="audit content-policy invalidation listener",
            )
        except Exception as exc:
            # PostgreSQL policy checks remain authoritative, so a listener
            # startup failure is observable degradation rather than startup
            # failure for the gateway.
            self._state = WorkerState.DEGRADED
            self._detail = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "audit content-policy invalidation listener did not become ready: %s",
                exc,
            )

    async def shutdown(self, *, deadline: float) -> bool:
        self._closed = True
        self._state = WorkerState.STOPPING
        stopped = await stop_tasks_before_deadline(
            [self._task],
            deadline=deadline,
            cancel_first=True,
        )
        self._task = None
        self._state = WorkerState.DISABLED
        self._detail = None
        return stopped

    async def publish(self, *, organization_id: str, enabled: bool, version: int) -> bool:
        if not isinstance(self.redis, AuditPolicyRedisPublisher):
            return False
        try:
            await self.redis.publish(
                self.channel,
                json.dumps(
                    {
                        "organization_id": organization_id,
                        "enabled": enabled,
                        "version": version,
                        "source_instance": self.instance_id,
                    }
                ),
            )
        except Exception:
            increment_audit_write_failure(path="policy_invalidation_publish")
            logger.exception(
                "failed publishing audit content-policy invalidation",
                extra={"organization_id": organization_id},
            )
            return False
        return True

    async def _listen(self) -> None:
        retry_delay = 0.1
        while not self._closed:
            try:
                await self._consume_once()
                if not self._closed:
                    raise RuntimeError("audit content-policy invalidation listener stopped")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._closed:
                    return
                self.invalidate_all()
                self._state = WorkerState.DEGRADED
                self._detail = f"{type(exc).__name__}: {exc}"
                increment_audit_write_failure(path="policy_listener")
                logger.exception("audit content-policy invalidation listener failed; reconnecting")
                await asyncio.sleep(retry_delay * random.uniform(0.8, 1.2))
                retry_delay = min(5.0, retry_delay * 2)

    async def _consume_once(self) -> None:
        if not isinstance(self.redis, AuditPolicyRedisListener):
            return
        pubsub = self.redis.pubsub()
        try:
            await pubsub.subscribe(self.channel)
            async for message in pubsub.listen():
                message_type = message.get("type")
                if message_type == "subscribe":
                    # Sending SUBSCRIBE does not wait for Redis to acknowledge
                    # it. Only expose readiness after the acknowledgement so a
                    # publisher cannot race ahead of the active subscription.
                    self.invalidate_all()
                    self._state = WorkerState.READY
                    self._detail = None
                    self._started.set()
                    continue
                if message_type != "message":
                    continue
                data = _decode_message(message.get("data"))
                if data.get("source_instance") == self.instance_id:
                    continue
                organization_id = _optional_string(data.get("organization_id"))
                if organization_id:
                    self.invalidate_one(organization_id)
        finally:
            try:
                await pubsub.unsubscribe(self.channel)
            except Exception:
                pass
            await pubsub.aclose()


def _decode_message(raw: object) -> dict[str, object]:
    if isinstance(raw, bytes | bytearray):
        raw = raw.decode("utf-8")
    if not isinstance(raw, str):
        return {}
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _optional_string(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None
