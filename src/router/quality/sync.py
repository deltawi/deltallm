from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Mapping
from typing import Any, Final

from src.metrics import increment_router_quality_sync
from src.redis_namespace import build_redis_key
from src.router.quality.stats import (
    BUCKET_SECONDS,
    COUNT_FIELDS,
    WINDOW_BUCKETS,
    WINDOW_SECONDS,
    DeploymentQualityTracker,
    QualityCounts,
    snapshot_from_buckets,
)
from src.shutdown import cleanup_deadline
from src.telemetry.lifecycle import stop_tasks_before_deadline

logger = logging.getLogger(__name__)

SYNC_INTERVAL_SECONDS: Final = 15.0
# A bucket stays readable for the whole window plus one bucket of clock skew.
_BUCKET_TTL_SECONDS: Final = WINDOW_SECONDS + BUCKET_SECONDS
_FIELD_SEPARATOR: Final = ":"


class QualityStatsSync:
    """Owned loop that shares quality counters across gateway processes.

    Every interval it adds this process's new counters to one Redis hash per
    five-minute bucket and reads the window back in the same pipeline. When Redis
    is missing or fails, it publishes this process's own counters, labeled local.
    Counters are best-effort telemetry: a failed write drops that interval's
    cluster share and never retries, so no count is added twice.
    """

    def __init__(
        self,
        tracker: DeploymentQualityTracker,
        redis: Any | None,
        *,
        environment: str = "dev",
        interval_seconds: float = SYNC_INTERVAL_SECONDS,
    ) -> None:
        self._tracker = tracker
        self._redis = redis
        self._environment = environment
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping.clear()
            self._task = asyncio.create_task(self._loop(), name="router-quality-sync")

    async def close(self) -> None:
        self._stopping.set()
        await stop_tasks_before_deadline(
            (self._task,), deadline=cleanup_deadline(5), cancel_first=True
        )
        self._task = None

    async def run_once(self) -> None:
        pending = self._tracker.take_pending()
        if self._redis is None:
            self._tracker.publish(self._tracker.local_snapshot())
            increment_router_quality_sync(outcome="local_only")
            return
        try:
            buckets = await self._exchange(pending)
        except Exception:
            # Redis is fallible infrastructure here; routing keeps using local data.
            increment_router_quality_sync(outcome="failed")
            logger.warning("router quality sync failed; using local quality stats", exc_info=True)
            self._tracker.publish(self._tracker.local_snapshot())
            return
        self._tracker.publish(snapshot_from_buckets(buckets, source="cluster"))
        increment_router_quality_sync(outcome="cluster")

    def bucket_key(self, bucket: int) -> str:
        return build_redis_key(
            application="deltallm",
            environment=self._environment,
            schema_version=1,
            capability="router-quality",
            identifiers=(str(bucket),),
        )

    async def _exchange(
        self, pending: Mapping[int, Mapping[str, QualityCounts]]
    ) -> list[dict[str, QualityCounts]]:
        current = self._tracker.current_bucket()
        window = [current - offset for offset in range(WINDOW_BUCKETS)]
        pipeline = self._redis.pipeline(transaction=False)
        for bucket, by_deployment in pending.items():
            if bucket < window[-1]:
                continue
            key = self.bucket_key(bucket)
            for deployment_id, counts in by_deployment.items():
                for name, value in counts.nonzero_fields().items():
                    pipeline.hincrby(key, f"{name}{_FIELD_SEPARATOR}{deployment_id}", value)
            pipeline.expire(key, _BUCKET_TTL_SECONDS)
        for bucket in window:
            pipeline.hgetall(self.bucket_key(bucket))
        results = await pipeline.execute()
        return [_parse_bucket(raw) for raw in results[-len(window) :]]

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("router quality sync round failed")
            # Jitter keeps processes that started together from writing together.
            delay = self._interval * random.uniform(0.9, 1.1)
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=delay)
            except TimeoutError:
                continue


def _parse_bucket(raw: object) -> dict[str, QualityCounts]:
    by_deployment: dict[str, QualityCounts] = {}
    if not isinstance(raw, Mapping):
        return by_deployment
    for raw_field, raw_value in raw.items():
        field = raw_field.decode() if isinstance(raw_field, bytes) else str(raw_field)
        name, separator, deployment_id = field.partition(_FIELD_SEPARATOR)
        if not separator or not deployment_id or name not in COUNT_FIELDS:
            continue
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            continue
        counts = by_deployment.setdefault(deployment_id, QualityCounts())
        setattr(counts, name, getattr(counts, name) + value)
    return by_deployment
