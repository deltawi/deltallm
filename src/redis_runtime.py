"""Owned Redis allocations, bounded before the driver's connection lock."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, fields
from time import perf_counter
from typing import Literal

from prometheus_client import Counter, Gauge, Histogram
from pydantic import SecretStr
from redis.asyncio import ConnectionPool, Redis
from redis.asyncio.client import PubSub
from redis.asyncio.connection import parse_url
from redis.backoff import NoBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.asyncio.retry import Retry

from src.concurrency import BoundedCapacityGate, CapacityGateFull
from src.metrics.prometheus import get_prometheus_registry

Allocation = Literal["critical", "cache", "bulk"]
_registry = get_prometheus_registry()
_occupied = Gauge(
    "deltallm_redis_allocation_occupied",
    "Checked out or acquiring Redis slots",
    ["allocation"],
    registry=_registry,
)
_events = Counter(
    "deltallm_redis_allocation_events_total",
    "Redis connection acquisition outcomes",
    ["allocation", "outcome"],
    registry=_registry,
)
_acquisition = Histogram(
    "deltallm_redis_allocation_acquisition_seconds",
    "Redis connection acquisition duration",
    ["allocation"],
    registry=_registry,
)


def startup_setting(general: object, settings: object, field: str, default: object):
    explicit = getattr(general, "model_fields_set", None)
    if hasattr(general, field) and (explicit is None or field in explicit):
        return getattr(general, field)
    return getattr(settings, field, default)


@dataclass(frozen=True)
class RedisLimits:
    critical_max_connections: int = 64
    cache_max_connections: int = 16
    bulk_max_connections: int = 16
    acquisition_timeout_seconds: float = 0.2
    socket_timeout_seconds: float = 1.0
    connect_timeout_seconds: float = 1.0

    @classmethod
    def from_settings(cls, general: object, settings: object) -> RedisLimits:
        defaults = cls()
        return cls(
            **{
                field.name: startup_setting(
                    general, settings, "redis_" + field.name, getattr(defaults, field.name)
                )
                for field in fields(defaults)
            }
        )


class AllocatedRedisPool(ConnectionPool):
    def __init__(self, *, allocation: Allocation, acquisition_timeout: float, **kwargs) -> None:
        super().__init__(**kwargs)
        self.allocation = allocation
        self.acquisition_timeout = acquisition_timeout
        self.gate = BoundedCapacityGate(concurrency=self.max_connections, max_waiters=0)
        self._leases: set[object] = set()
        self.closed = False

    async def get_connection(self, command_name=None, *keys, **options):
        if self.closed:
            raise RedisConnectionError("Redis allocation is closed")
        try:
            await self.gate.acquire(timeout_seconds=self.acquisition_timeout)
        except CapacityGateFull:
            _events.labels(self.allocation, "full").inc()
            raise RedisConnectionError("Redis allocation is full") from None
        _occupied.labels(self.allocation).inc()
        started = perf_counter()
        outcome = "unavailable"
        try:
            async with asyncio.timeout(self.acquisition_timeout):
                connection = await super().get_connection()
            if self.closed:
                await super().release(connection)
                raise RedisConnectionError("Redis allocation is closed")
            self._leases.add(connection)
            outcome = "acquired"
            return connection
        except TimeoutError:
            outcome = "deadline"
            raise RedisConnectionError("Redis acquisition deadline exceeded") from None
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
            if outcome != "acquired":
                await self.gate.release()
                _occupied.labels(self.allocation).dec()
            _events.labels(self.allocation, outcome).inc()
            _acquisition.labels(self.allocation).observe(perf_counter() - started)

    async def release(self, connection) -> None:
        owned = connection in self._leases
        if owned:
            self._leases.remove(connection)
        try:
            await super().release(connection)
        finally:
            # The driver also calls release on failed connection setup, before
            # a lease exists. get_connection owns that failure's permit cleanup.
            if owned:
                await self.gate.release()
                _occupied.labels(self.allocation).dec()

    async def aclose(self) -> None:
        self.closed = True
        await super().aclose()


class AllocatedPubSub(PubSub):
    async def listen(self):
        # Idle subscriptions are healthy. An explicit polling timeout returns
        # None rather than turning the command socket deadline into an outage.
        timeout = min(1.0, self.connection_pool.connection_kwargs["socket_timeout"])
        while self.subscribed:
            message = await self.get_message(timeout=timeout)
            if message is not None:
                yield message


class AllocatedRedis(Redis):
    def pubsub(self, **kwargs) -> PubSub:
        return AllocatedPubSub(
            self.connection_pool, event_dispatcher=self._event_dispatcher, **kwargs
        )

    async def aclose(self, close_connection_pool=None) -> None:
        owns_pool = (
            self.auto_close_connection_pool
            if close_connection_pool is None
            else close_connection_pool
        )
        if owns_pool:
            self.connection_pool.closed = True
        await super().aclose(close_connection_pool=close_connection_pool)


def build_redis_client(
    settings: object,
    general: object,
    *,
    allocation: Allocation,
    endpoint_settings: object | None = None,
) -> Redis:
    limits = RedisLimits.from_settings(general, settings)
    endpoint = general if endpoint_settings is None else endpoint_settings
    url = getattr(settings, "redis_url", None) or getattr(endpoint, "redis_url", None)
    if allocation in {"cache", "bulk"}:
        bulk_url = startup_setting(general, settings, "redis_bulk_url", None)
        if bulk_url is not None:
            if not isinstance(bulk_url, SecretStr):
                raise TypeError("Redis bulk endpoint must use the typed secret setting")
            url = bulk_url.get_secret_value() or url
    options = (
        parse_url(url)
        if url
        else {
            "host": getattr(endpoint, "redis_host", None)
            or getattr(settings, "redis_host", "localhost"),
            "port": getattr(endpoint, "redis_port", None) or getattr(settings, "redis_port", 6379),
            "password": getattr(endpoint, "redis_password", None)
            or getattr(settings, "redis_password", None),
        }
    )
    # URL query parameters must not disable typed capacity/deadline settings.
    options.update(
        max_connections=getattr(limits, allocation + "_max_connections"),
        socket_timeout=limits.socket_timeout_seconds,
        socket_connect_timeout=limits.connect_timeout_seconds,
        decode_responses=True,
        retry=Retry(NoBackoff(), 0),
        retry_on_timeout=False,
        retry_on_error=[],
    )
    pool = AllocatedRedisPool(
        allocation=allocation,
        acquisition_timeout=limits.acquisition_timeout_seconds,
        **options,
    )
    return AllocatedRedis.from_pool(pool)
