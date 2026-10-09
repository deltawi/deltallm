"""Owned Redis allocations, bounded before the driver's connection lock."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, fields
from time import perf_counter
from typing import Literal

from prometheus_client import Counter, Gauge, Histogram
from pydantic import SecretStr
from redis.asyncio import ConnectionPool, Redis
from redis.asyncio.client import Pipeline, PubSub
from redis.asyncio.connection import parse_url
from redis.backoff import NoBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.asyncio.retry import Retry

from src.concurrency import BoundedCapacityGate, CapacityGateFull, CapacityGateTimedOut
from src.metrics.prometheus import get_prometheus_registry

Allocation = Literal["critical", "cache", "bulk"]
_registry = get_prometheus_registry()
_occupied = Gauge(
    "deltallm_redis_allocation_occupied",
    "Checked out or acquiring Redis slots",
    ["allocation"],
    registry=_registry,
)
_waiters = Gauge(
    "deltallm_redis_allocation_waiters",
    "Callers waiting for a Redis allocation slot",
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
_round_trips = Counter(
    "deltallm_redis_command_round_trips_total",
    "Redis client network round trips by bounded command family and logical owner",
    ["allocation", "owner", "family", "outcome"],
    registry=_registry,
)
_round_trip_seconds = Histogram(
    "deltallm_redis_command_round_trip_seconds",
    "Redis client command duration including pool acquisition and network time",
    ["allocation", "owner", "family", "outcome"],
    buckets=[0.00025, 0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 1],
    registry=_registry,
)
_pipeline_commands = Histogram(
    "deltallm_redis_pipeline_commands",
    "Commands carried by one Redis pipeline network round trip",
    ["allocation", "owner"],
    buckets=[1, 2, 4, 8, 16, 32, 64, 128, 256],
    registry=_registry,
)

_READ_COMMANDS = frozenset({"GET", "MGET", "HGET", "HMGET", "EXISTS", "TTL", "PTTL", "SCAN"})
_WRITE_COMMANDS = frozenset(
    {"SET", "SETEX", "PSETEX", "DEL", "UNLINK", "INCR", "INCRBY", "EXPIRE", "HSET", "ZADD"}
)
_CLEANUP_COMMANDS = frozenset({"DEL", "UNLINK", "HDEL", "ZREM", "ZREMRANGEBYSCORE"})
_MULTI_KEY_COMMANDS = frozenset({"DEL", "EXISTS", "MGET", "TOUCH", "UNLINK"})
_SYSTEM_COMMANDS = frozenset({"CLIENT", "COMMAND", "INFO", "PING", "PUBLISH", "SCRIPT", "TIME"})


def _command_family(command: object) -> str:
    name = str(command).split(" ", 1)[0].upper()
    if name in {"EVAL", "EVALSHA", "SCRIPT"}:
        return "lua"
    if name in _CLEANUP_COMMANDS:
        return "cleanup"
    if name in _READ_COMMANDS:
        return "read"
    if name in _WRITE_COMMANDS:
        return "write"
    return "other"


def _command_keys(args: tuple[object, ...]) -> tuple[str, ...]:
    if not args:
        return ()
    name = str(args[0]).split(" ", 1)[0].upper()
    if name in {"EVAL", "EVALSHA"} and len(args) >= 3:
        try:
            key_count = max(0, min(256, int(args[2])))
        except (TypeError, ValueError):
            return ()
        return tuple(str(value) for value in args[3 : 3 + key_count])
    if name in _SYSTEM_COMMANDS or len(args) < 2:
        return ()
    if name in _MULTI_KEY_COMMANDS:
        return tuple(str(value) for value in args[1:257])
    if name in {"MSET", "MSETNX"}:
        return tuple(str(value) for value in args[1:257:2])
    return (str(args[1]),)


def _key_owner(key: str) -> str:
    normalized = key.lower()
    if normalized.startswith(("key:v4:", "key:v5:", "key:v6:", "key:v7:")):
        return "authentication"
    if normalized.startswith(("parallel:", "parallel_lease:")):
        return "concurrency"
    if normalized.startswith(("ratelimit:", "tier_fair_share:")):
        return "rate_limit"
    if any(
        marker in normalized
        for marker in (
            ":router-active-requests:",
            ":router-attempt-owners:",
            ":router-cooldown:",
            ":router-health:",
            ":router-health-failures:",
            ":router-health-probe:",
            ":router-health-recovery:",
            ":router-latency:",
            ":router-usage:",
        )
    ):
        return "routing"
    if (
        normalized.startswith(
            (
                "cache:",
                "deltallm:prompt:",
                "deltallm:promptbinding:",
                "deltallm:promptgroupdefault:",
            )
        )
        or ":route-group-runtime:" in normalized
    ):
        return "cache"
    return "unknown"


def _command_owner(args: tuple[object, ...]) -> str:
    owners = {_key_owner(key) for key in _command_keys(args)}
    owners.discard("unknown")
    if len(owners) == 1:
        return owners.pop()
    if len(owners) > 1:
        return "mixed"
    return "system" if not _command_keys(args) else "unknown"


def _observe_round_trip(
    *, allocation: str, owner: str, family: str, outcome: str, started: float
) -> None:
    _round_trips.labels(allocation, owner, family, outcome).inc()
    _round_trip_seconds.labels(allocation, owner, family, outcome).observe(perf_counter() - started)


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
    critical_max_waiters: int = 64
    cache_max_waiters: int = 0
    bulk_max_waiters: int = 0
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
    def __init__(
        self,
        *,
        allocation: Allocation,
        acquisition_timeout: float,
        max_waiters: int = 0,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.allocation = allocation
        self.acquisition_timeout = acquisition_timeout
        self.gate = BoundedCapacityGate(
            concurrency=self.max_connections,
            max_waiters=max_waiters,
        )
        self._leases: set[object] = set()
        self.closed = False

    async def get_connection(self, command_name=None, *keys, **options):
        if self.closed:
            raise RedisConnectionError("Redis allocation is closed")
        started = perf_counter()
        deadline = asyncio.get_running_loop().time() + self.acquisition_timeout
        outcome = "unavailable"
        owns_slot = False
        waiting = self.gate.active >= self.max_connections
        if waiting:
            _waiters.labels(self.allocation).inc()
        try:
            try:
                await self.gate.acquire(timeout_seconds=self.acquisition_timeout)
            except CapacityGateFull:
                outcome = "full"
                raise RedisConnectionError("Redis allocation is full") from None
            except CapacityGateTimedOut:
                outcome = "deadline"
                raise RedisConnectionError("Redis acquisition deadline exceeded") from None
            finally:
                if waiting:
                    _waiters.labels(self.allocation).dec()

            owns_slot = True
            _occupied.labels(self.allocation).inc()
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError
            async with asyncio.timeout(remaining):
                # The gate bounds callers before this driver lock. Reserve the
                # connection under the lock. Check its socket outside the lock
                # so one slow connection cannot block all admitted callers.
                async with self._lock:
                    connection = self.get_available_connection()
                try:
                    await self.ensure_connection(connection)
                except BaseException:
                    await super().release(connection)
                    raise
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
            if owns_slot and outcome != "acquired":
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


class ObservedPipeline(Pipeline):
    async def execute(self, raise_on_error: bool = True):
        allocation = getattr(self.connection_pool, "allocation", "unknown")
        command_count = len(self.command_stack)
        owners = {
            _command_owner(tuple(command)) for command, _options in self.command_stack if command
        }
        owners.discard("system")
        owner = owners.pop() if len(owners) == 1 else "mixed" if owners else "system"
        started = perf_counter()
        outcome = "error"
        try:
            result = await super().execute(raise_on_error=raise_on_error)
            outcome = "success"
            return result
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
            _pipeline_commands.labels(allocation, owner).observe(command_count)
            _observe_round_trip(
                allocation=allocation,
                owner=owner,
                family="pipeline",
                outcome=outcome,
                started=started,
            )


class AllocatedRedis(Redis):
    async def execute_command(self, *args, **options):
        allocation = getattr(self.connection_pool, "allocation", "unknown")
        family = _command_family(args[0] if args else "unknown")
        owner = _command_owner(tuple(args))
        started = perf_counter()
        outcome = "error"
        try:
            result = await super().execute_command(*args, **options)
            outcome = "success"
            return result
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
            _observe_round_trip(
                allocation=allocation,
                owner=owner,
                family=family,
                outcome=outcome,
                started=started,
            )

    def pipeline(self, transaction: bool = True, shard_hint: str | None = None) -> Pipeline:
        return ObservedPipeline(
            self.connection_pool,
            self.response_callbacks,
            transaction,
            shard_hint,
        )

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
    options = redis_connection_options(settings, general, allocation, endpoint_settings)
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
        max_waiters=getattr(limits, allocation + "_max_waiters"),
        **options,
    )
    return AllocatedRedis.from_pool(pool)


def redis_connection_options(
    settings: object,
    general: object,
    allocation: Allocation,
    endpoint_settings: object | None = None,
) -> dict[str, object]:
    """One endpoint resolver for client construction and startup capacity checks."""
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
    return options
