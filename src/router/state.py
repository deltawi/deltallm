from __future__ import annotations

import json
import logging
import secrets
import time
from datetime import UTC, datetime
from typing import Any, Literal, Mapping, Protocol

from src.models.errors import ServiceUnavailableError
from src.router.candidates import (
    DEFAULT_ATTEMPT_PERMIT_TTL_SECONDS,
    AttemptCapacity,
    AttemptPermit,
    AttemptRejectionReason,
    UsageCounterName,
)

logger = logging.getLogger(__name__)

USAGE_COUNTER_NAMES = ("rpm", "tpm", "image_pm", "audio_seconds_pm", "char_pm", "rerank_units_pm")
_ATTEMPT_LEASE_CLEANUP_GRACE_MS = 1_000
_ATTEMPT_LEGACY_COMPAT_TTL_SECONDS = 86_400

_ATTEMPT_ADMISSION_SCRIPT = """
-- router_attempt_admission_v2
local redis_time = redis.call('TIME')
local now_ms = (tonumber(redis_time[1]) * 1000) + math.floor(tonumber(redis_time[2]) / 1000)
local capacity_count = tonumber(ARGV[1]) or 0
local lease_ttl_ms = tonumber(ARGV[2]) or 1000
local legacy_compat_ttl_ms = tonumber(ARGV[3]) or lease_ttl_ms
local owner_token = ARGV[4]

local expired = redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now_ms)
local current = tonumber(redis.call('GET', KEYS[1]) or '0') or 0
current = math.max(0, current - expired)
local owner_count = redis.call('ZCARD', KEYS[2])
current = math.max(current, owner_count)

local active_ttl_ms = redis.call('PTTL', KEYS[1])
if current <= 0 then
  redis.call('DEL', KEYS[1])
elseif expired > 0 then
  redis.call('SET', KEYS[1], current)
  if active_ttl_ms > 0 then
    redis.call('PEXPIRE', KEYS[1], active_ttl_ms)
  end
end

if redis.call('EXISTS', KEYS[3]) == 1 then
  return {0, 'cooldown', 0}
end

local healthy = redis.call('HGET', KEYS[4], 'healthy')
if healthy == 'false' then
  return {0, 'unhealthy', 0}
end

for index = 1, capacity_count do
  local usage = tonumber(redis.call('GET', KEYS[4 + index]) or '0')
  local limit = tonumber(ARGV[4 + index])
  if usage >= limit then
    return {0, 'capacity', 0}
  end
end

local expires_at_ms = now_ms + lease_ttl_ms
redis.call('ZADD', KEYS[2], expires_at_ms, owner_token)
local active = current + 1
redis.call('SET', KEYS[1], active)

local latest = redis.call('ZREVRANGE', KEYS[2], 0, 0, 'WITHSCORES')
local key_expires_at_ms = expires_at_ms
if #latest >= 2 then
  key_expires_at_ms = math.max(key_expires_at_ms, tonumber(latest[2]))
end
if current > owner_count then
  key_expires_at_ms = math.max(key_expires_at_ms, now_ms + legacy_compat_ttl_ms)
end
key_expires_at_ms = key_expires_at_ms + tonumber(ARGV[5 + capacity_count])
redis.call('PEXPIREAT', KEYS[1], key_expires_at_ms)
redis.call('PEXPIREAT', KEYS[2], key_expires_at_ms)
return {1, 'acquired', active, expires_at_ms}
"""

_ATTEMPT_RELEASE_SCRIPT = """
-- router_attempt_release_v2
local removed = redis.call('ZREM', KEYS[2], ARGV[1])
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local owner_count = redis.call('ZCARD', KEYS[2])
if removed == 1 then
  current = math.max(0, current - 1)
end
current = math.max(current, owner_count)

if current <= 0 then
  redis.call('DEL', KEYS[1])
else
  local active_ttl_ms = redis.call('PTTL', KEYS[1])
  redis.call('SET', KEYS[1], current)
  if active_ttl_ms > 0 then
    redis.call('PEXPIRE', KEYS[1], active_ttl_ms)
  end
end
if owner_count == 0 then
  redis.call('DEL', KEYS[2])
end
return current
"""

_ATTEMPT_ACTIVE_BATCH_SCRIPT = """
-- router_attempt_active_batch_v1
local redis_time = redis.call('TIME')
local now_ms = (tonumber(redis_time[1]) * 1000) + math.floor(tonumber(redis_time[2]) / 1000)
local deployment_count = math.floor(#KEYS / 2)
local results = {}

for index = 1, deployment_count do
  local active_key = KEYS[((index - 1) * 2) + 1]
  local owners_key = KEYS[((index - 1) * 2) + 2]
  local expired = redis.call('ZREMRANGEBYSCORE', owners_key, '-inf', now_ms)
  local current = tonumber(redis.call('GET', active_key) or '0') or 0
  current = math.max(0, current - expired)
  local owner_count = redis.call('ZCARD', owners_key)
  current = math.max(current, owner_count)

  if current <= 0 then
    redis.call('DEL', active_key)
  elseif expired > 0 then
    local active_ttl_ms = redis.call('PTTL', active_key)
    redis.call('SET', active_key, current)
    if active_ttl_ms > 0 then
      redis.call('PEXPIRE', active_key, active_ttl_ms)
    end
  end
  if owner_count == 0 then
    redis.call('DEL', owners_key)
  end
  results[index] = current
end

return results
"""


class DeploymentStateBackend(Protocol):
    async def acquire_attempt(
        self,
        deployment_id: str,
        capacity: AttemptCapacity,
        *,
        lease_ttl_seconds: int = DEFAULT_ATTEMPT_PERMIT_TTL_SECONDS,
    ) -> AttemptPermit: ...

    async def release_attempt(self, permit: AttemptPermit) -> int | None: ...

    async def get_active_requests(self, deployment_id: str) -> int: ...

    async def get_active_requests_batch(self, deployment_ids: list[str]) -> dict[str, int]: ...

    async def record_latency(self, deployment_id: str, latency_ms: float) -> None: ...

    async def get_latency_window(
        self, deployment_id: str, window_ms: int
    ) -> list[tuple[int, float]]: ...

    async def get_latency_windows_batch(
        self,
        deployment_ids: list[str],
        window_ms: int,
    ) -> dict[str, list[tuple[int, float]]]: ...

    async def increment_usage(
        self, deployment_id: str, tokens: int, window: str | None = None
    ) -> None: ...

    async def increment_usage_counters(
        self,
        deployment_id: str,
        counters: Mapping[str, int],
        window: str | None = None,
    ) -> None: ...

    async def get_usage(self, deployment_id: str) -> dict[str, int]: ...

    async def get_usage_batch(self, deployment_ids: list[str]) -> dict[str, dict[str, int]]: ...

    async def set_cooldown(self, deployment_id: str, duration_sec: int, reason: str) -> None: ...

    async def clear_cooldown(self, deployment_id: str) -> None: ...

    async def is_cooled_down(self, deployment_id: str) -> bool: ...

    async def get_cooldown_batch(self, deployment_ids: list[str]) -> dict[str, bool]: ...

    async def record_success(self, deployment_id: str) -> None: ...

    async def record_failure(self, deployment_id: str, error: str) -> int: ...

    async def set_health(self, deployment_id: str, healthy: bool) -> None: ...

    async def get_health(self, deployment_id: str) -> dict[str, Any]: ...

    async def get_health_batch(self, deployment_ids: list[str]) -> dict[str, dict[str, Any]]: ...


class RedisStateBackend:
    """Runtime state for the standalone Redis topology constructed by bootstrap.

    Multi-key attempt admission intentionally uses one Lua call and is not compatible
    with Redis Cluster's cross-slot execution model.
    """

    def __init__(
        self,
        redis: Any | None,
        latency_window_ms: int = 300_000,
        *,
        degraded_mode: Literal["fail_open", "fail_closed"] = "fail_open",
        local_state_ttl_sec: int = 600,
        max_local_latency_samples: int = 256,
    ):
        self.redis = redis
        self.latency_window_ms = latency_window_ms
        self.degraded_mode = (
            degraded_mode if degraded_mode in {"fail_open", "fail_closed"} else "fail_open"
        )
        self.local_state_ttl_sec = max(1, int(local_state_ttl_sec))
        self.max_local_latency_samples = max(1, int(max_local_latency_samples))
        self._active: dict[str, int] = {}
        self._active_permits: dict[str, dict[str, float]] = {}
        self._latency: dict[str, list[tuple[int, float]]] = {}
        self._usage: dict[str, dict[str, Any]] = {}
        self._cooldown_until: dict[str, float] = {}
        self._health: dict[str, dict[str, Any]] = {}
        self._failures: dict[str, int] = {}
        self._local_last_seen: dict[str, float] = {}
        self._last_prune_at = 0.0
        self._prune_interval_sec = 30.0
        self._backend_mode: Literal["redis", "degraded", "unavailable"] = "redis"
        self._last_redis_error: str | None = None
        self._last_redis_error_at: int | None = None
        if self.redis is None:
            self._mark_backend_failure(AttributeError("redis unavailable"))

    def _minute_window(self) -> str:
        return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M")

    def get_backend_status(self) -> dict[str, Any]:
        return {
            "mode": self._backend_mode,
            "degraded_mode": self.degraded_mode,
            "local_fallback_entries": len(self._local_last_seen),
            "last_error": self._last_redis_error,
            "last_error_at": self._last_redis_error_at,
        }

    def _mark_backend_failure(self, exc: Exception) -> None:
        next_mode: Literal["degraded", "unavailable"] = (
            "degraded" if self.degraded_mode == "fail_open" else "unavailable"
        )
        previous_mode = self._backend_mode
        self._backend_mode = next_mode
        self._last_redis_error = str(exc) or "redis unavailable"
        self._last_redis_error_at = int(time.time())
        if previous_mode != next_mode:
            logger.warning(
                "router state backend entered %s mode: %s", next_mode, self._last_redis_error
            )

    def _mark_backend_healthy(self) -> None:
        if self.redis is None:
            return
        if self._backend_mode != "redis":
            logger.info("router state backend recovered to redis mode")
        self._backend_mode = "redis"
        self._last_redis_error = None
        self._last_redis_error_at = None

    def _handle_backend_failure(self, exc: Exception) -> None:
        self._mark_backend_failure(exc)
        if self.degraded_mode == "fail_closed":
            raise ServiceUnavailableError(message="Router state backend unavailable") from exc

    def _touch_local_state(self, deployment_id: str, *, now: float | None = None) -> None:
        self._local_last_seen[deployment_id] = now or time.time()

    def _drop_local_state_if_unused(self, deployment_id: str) -> None:
        self._prune_local_attempt_permits(deployment_id)
        if self._active.get(deployment_id, 0) > 0:
            return
        cooldown_until = self._cooldown_until.get(deployment_id)
        if cooldown_until is not None and cooldown_until > time.time():
            return
        if deployment_id in self._latency:
            return
        if deployment_id in self._usage:
            return
        if deployment_id in self._health:
            return
        if self._failures.get(deployment_id, 0) > 0:
            return
        self._active.pop(deployment_id, None)
        self._cooldown_until.pop(deployment_id, None)
        self._failures.pop(deployment_id, None)
        self._local_last_seen.pop(deployment_id, None)

    def _prune_local_state(self, *, force: bool = False, now: float | None = None) -> None:
        current_time = now or time.time()
        if not force and current_time - self._last_prune_at < self._prune_interval_sec:
            return
        self._last_prune_at = current_time
        cutoff = current_time - self.local_state_ttl_sec

        for deployment_id, seen_at in list(self._local_last_seen.items()):
            self._prune_local_attempt_permits(deployment_id, now=current_time)
            if seen_at >= cutoff:
                continue

            if self._active.get(deployment_id, 0) > 0:
                continue

            cooldown_until = self._cooldown_until.get(deployment_id)
            if cooldown_until is not None and cooldown_until > current_time:
                continue

            self._active.pop(deployment_id, None)
            self._active_permits.pop(deployment_id, None)
            self._latency.pop(deployment_id, None)
            self._usage.pop(deployment_id, None)
            self._cooldown_until.pop(deployment_id, None)
            self._health.pop(deployment_id, None)
            self._failures.pop(deployment_id, None)
            self._local_last_seen.pop(deployment_id, None)

    async def _redis_call(self, method: str, *args, **kwargs):
        if self.redis is None:
            raise AttributeError("redis unavailable")
        fn = getattr(self.redis, method)
        result = await fn(*args, **kwargs)
        self._mark_backend_healthy()
        return result

    async def acquire_attempt(
        self,
        deployment_id: str,
        capacity: AttemptCapacity,
        *,
        lease_ttl_seconds: int = DEFAULT_ATTEMPT_PERMIT_TTL_SECONDS,
    ) -> AttemptPermit:
        minute = self._minute_window()
        normalized_ttl_seconds = max(1, int(lease_ttl_seconds))
        owner_token = secrets.token_urlsafe(18)
        capacity_keys = [
            self._usage_key(deployment_id, item.counter, minute) for item in capacity.limits
        ]
        keys = [
            f"active_requests:{deployment_id}",
            self._attempt_owners_key(deployment_id),
            f"cooldown:{deployment_id}",
            f"health:{deployment_id}",
            *capacity_keys,
        ]
        args = [
            len(capacity.limits),
            normalized_ttl_seconds * 1000,
            _ATTEMPT_LEGACY_COMPAT_TTL_SECONDS * 1000,
            owner_token,
            *(item.limit for item in capacity.limits),
            _ATTEMPT_LEASE_CLEANUP_GRACE_MS,
        ]
        try:
            raw = await self._redis_call(
                "eval",
                _ATTEMPT_ADMISSION_SCRIPT,
                len(keys),
                *keys,
                *args,
            )
            if not isinstance(raw, (list, tuple)) or len(raw) < 3:
                raise RuntimeError("invalid router attempt admission response")
            acquired = int(raw[0]) == 1
            if acquired:
                if len(raw) < 4:
                    raise RuntimeError("invalid acquired router attempt response")
                return AttemptPermit(
                    deployment_id=deployment_id,
                    acquired=True,
                    backend="redis",
                    owner_token=owner_token,
                    expires_at_ms=int(raw[3]),
                    active_requests=int(raw[2]),
                )
            return AttemptPermit(
                deployment_id=deployment_id,
                acquired=False,
                rejection_reason=AttemptRejectionReason(self._decode_redis_text(raw[1])),
            )
        except Exception as exc:
            self._handle_backend_failure(exc)
            rejection_reason = self._local_attempt_rejection(deployment_id, capacity)
            if rejection_reason is not None:
                return AttemptPermit(
                    deployment_id=deployment_id,
                    acquired=False,
                    rejection_reason=rejection_reason,
                )
            now = time.time()
            active_requests = self._prune_local_attempt_permits(deployment_id, now=now) + 1
            expires_at = now + normalized_ttl_seconds
            self._active_permits.setdefault(deployment_id, {})[owner_token] = expires_at
            self._active[deployment_id] = active_requests
            self._touch_local_state(deployment_id, now=now)
            return AttemptPermit(
                deployment_id=deployment_id,
                acquired=True,
                backend="local",
                owner_token=owner_token,
                expires_at_ms=int(expires_at * 1000),
                active_requests=active_requests,
            )

    async def release_attempt(self, permit: AttemptPermit) -> int | None:
        if not permit.acquired or permit.backend is None:
            return 0
        if permit.backend == "local":
            return self._release_local_attempt(permit)
        if not permit.owner_token:
            return await self.get_active_requests(permit.deployment_id)

        keys = [
            f"active_requests:{permit.deployment_id}",
            self._attempt_owners_key(permit.deployment_id),
        ]
        try:
            return int(
                await self._redis_call(
                    "eval",
                    _ATTEMPT_RELEASE_SCRIPT,
                    len(keys),
                    *keys,
                    permit.owner_token,
                )
            )
        except Exception as exc:
            self._handle_backend_failure(exc)
            return None

    def _local_attempt_rejection(
        self,
        deployment_id: str,
        capacity: AttemptCapacity,
    ) -> AttemptRejectionReason | None:
        now = time.time()
        cooldown_until = self._cooldown_until.get(deployment_id)
        if cooldown_until is not None:
            if cooldown_until > now:
                self._touch_local_state(deployment_id, now=now)
                return AttemptRejectionReason.COOLDOWN
            self._cooldown_until.pop(deployment_id, None)

        if self._health.get(deployment_id, {}).get("healthy", "true") == "false":
            self._touch_local_state(deployment_id, now=now)
            return AttemptRejectionReason.UNHEALTHY

        usage = self._usage.get(deployment_id, {})
        if usage.get("window") != self._minute_window():
            usage = {}
        if any(int(usage.get(item.counter, 0) or 0) >= item.limit for item in capacity.limits):
            self._touch_local_state(deployment_id, now=now)
            return AttemptRejectionReason.CAPACITY
        return None

    def _release_local_attempt(self, permit: AttemptPermit) -> int:
        permits = self._active_permits.get(permit.deployment_id)
        if permits is not None and permit.owner_token:
            permits.pop(permit.owner_token, None)
        value = self._prune_local_attempt_permits(permit.deployment_id)
        if value == 0:
            self._drop_local_state_if_unused(permit.deployment_id)
        return value

    def _prune_local_attempt_permits(
        self,
        deployment_id: str,
        *,
        now: float | None = None,
    ) -> int:
        permits = self._active_permits.get(deployment_id)
        if not permits:
            self._active_permits.pop(deployment_id, None)
            self._active.pop(deployment_id, None)
            return 0

        current_time = time.time() if now is None else now
        for owner_token, expires_at in list(permits.items()):
            if expires_at <= current_time:
                permits.pop(owner_token, None)
        if not permits:
            self._active_permits.pop(deployment_id, None)
            self._active.pop(deployment_id, None)
            return 0

        value = len(permits)
        self._active[deployment_id] = value
        return value

    @staticmethod
    def _attempt_owners_key(deployment_id: str) -> str:
        return f"router_attempt_owners:v1:{deployment_id}"

    @staticmethod
    def _usage_key(deployment_id: str, counter: UsageCounterName, minute: str) -> str:
        return f"usage_{counter}:{deployment_id}:{minute}"

    @staticmethod
    def _decode_redis_text(value: Any) -> str:
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)

    async def get_active_requests(self, deployment_id: str) -> int:
        active = await self.get_active_requests_batch([deployment_id])
        return active.get(deployment_id, 0)

    async def get_active_requests_batch(self, deployment_ids: list[str]) -> dict[str, int]:
        if not deployment_ids:
            return {}

        keys = [
            key
            for deployment_id in deployment_ids
            for key in (
                f"active_requests:{deployment_id}",
                self._attempt_owners_key(deployment_id),
            )
        ]
        try:
            values = await self._redis_call(
                "eval",
                _ATTEMPT_ACTIVE_BATCH_SCRIPT,
                len(keys),
                *keys,
            )
            return {
                deployment_id: int(value or 0)
                for deployment_id, value in zip(deployment_ids, values, strict=False)
            }
        except Exception as exc:
            self._handle_backend_failure(exc)
            self._prune_local_state()
            return {
                deployment_id: self._prune_local_attempt_permits(deployment_id)
                for deployment_id in deployment_ids
            }

    async def record_latency(self, deployment_id: str, latency_ms: float) -> None:
        timestamp_ms = int(time.time() * 1000)
        cutoff = timestamp_ms - self.latency_window_ms
        key = f"latency:{deployment_id}"
        try:
            pipe = self.redis.pipeline()
            # timestamp is score, latency is member value
            pipe.zadd(key, {f"{timestamp_ms}:{float(latency_ms)}": timestamp_ms})
            pipe.zremrangebyscore(key, 0, cutoff)
            pipe.pexpire(key, self.latency_window_ms)
            await pipe.execute()
            self._mark_backend_healthy()
            return
        except Exception as exc:
            self._handle_backend_failure(exc)
            window = self._latency.setdefault(deployment_id, [])
            window.append((timestamp_ms, float(latency_ms)))
            trimmed = [(ts, lat) for ts, lat in window if ts >= cutoff]
            if len(trimmed) > self.max_local_latency_samples:
                trimmed = trimmed[-self.max_local_latency_samples :]
            self._latency[deployment_id] = trimmed
            self._touch_local_state(deployment_id, now=time.time())

    async def get_latency_window(
        self, deployment_id: str, window_ms: int
    ) -> list[tuple[int, float]]:
        windows = await self.get_latency_windows_batch([deployment_id], window_ms)
        return windows.get(deployment_id, [])

    async def get_latency_windows_batch(
        self,
        deployment_ids: list[str],
        window_ms: int,
    ) -> dict[str, list[tuple[int, float]]]:
        if not deployment_ids:
            return {}

        now_ms = int(time.time() * 1000)
        min_score = now_ms - window_ms
        try:
            pipe = self.redis.pipeline()
            for deployment_id in deployment_ids:
                pipe.zrangebyscore(f"latency:{deployment_id}", min_score, "+inf")
            results = await pipe.execute()
            self._mark_backend_healthy()
            windows: dict[str, list[tuple[int, float]]] = {}
            for deployment_id, items in zip(deployment_ids, results, strict=False):
                window: list[tuple[int, float]] = []
                for item in items:
                    text = item.decode("utf-8") if isinstance(item, bytes) else str(item)
                    timestamp, latency = text.split(":", 1)
                    window.append((int(timestamp), float(latency)))
                windows[deployment_id] = window
            return windows
        except Exception as exc:
            self._handle_backend_failure(exc)
            self._prune_local_state()
            windows = {}
            now = time.time()
            for deployment_id in deployment_ids:
                window = [
                    (timestamp, latency)
                    for timestamp, latency in self._latency.get(deployment_id, [])
                    if timestamp >= min_score
                ]
                if window:
                    self._latency[deployment_id] = window[-self.max_local_latency_samples :]
                    self._touch_local_state(deployment_id, now=now)
                else:
                    self._latency.pop(deployment_id, None)
                    self._drop_local_state_if_unused(deployment_id)
                windows[deployment_id] = window
            return windows

    async def increment_usage(
        self, deployment_id: str, tokens: int, window: str | None = None
    ) -> None:
        await self.increment_usage_counters(
            deployment_id,
            {"rpm": 1, "tpm": max(0, int(tokens))},
            window=window,
        )

    async def increment_usage_counters(
        self,
        deployment_id: str,
        counters: Mapping[str, int],
        window: str | None = None,
    ) -> None:
        minute = window or self._minute_window()
        normalized = self._normalize_usage_counters(counters)
        keys = {
            counter_name: f"usage_{counter_name}:{deployment_id}:{minute}"
            for counter_name in normalized
        }
        try:
            pipe = self.redis.pipeline()
            for counter_name, counter_value in normalized.items():
                key = keys[counter_name]
                if counter_name == "rpm":
                    pipe.incr(key)
                elif counter_value > 0:
                    pipe.incrby(key, int(counter_value))
                pipe.expire(key, 120)
            await pipe.execute()
            self._mark_backend_healthy()
            return
        except Exception as exc:
            self._handle_backend_failure(exc)
            usage = self._usage.setdefault(
                deployment_id,
                {"rpm": 0, "tpm": 0, "window": minute, "updated_at": int(time.time())},
            )
            if usage.get("window") != minute:
                usage = {"rpm": 0, "tpm": 0, "window": minute, "updated_at": int(time.time())}
                self._usage[deployment_id] = usage
            for counter_name, counter_value in normalized.items():
                if counter_name == "rpm":
                    usage["rpm"] = int(usage.get("rpm", 0)) + 1
                    continue
                if counter_value <= 0:
                    continue
                usage[counter_name] = int(usage.get(counter_name, 0)) + int(counter_value)
            usage["updated_at"] = int(time.time())
            self._touch_local_state(deployment_id, now=time.time())

    async def get_usage(self, deployment_id: str) -> dict[str, int]:
        usage = await self.get_usage_batch([deployment_id])
        return usage.get(deployment_id, {"rpm": 0, "tpm": 0})

    async def get_usage_batch(self, deployment_ids: list[str]) -> dict[str, dict[str, int]]:
        if not deployment_ids:
            return {}

        minute = self._minute_window()
        keys = [
            f"usage_{counter_name}:{deployment_id}:{minute}"
            for deployment_id in deployment_ids
            for counter_name in USAGE_COUNTER_NAMES
        ]
        try:
            values = await self._redis_call("mget", keys)
            width = len(USAGE_COUNTER_NAMES)
            snapshots: dict[str, dict[str, int]] = {}
            for index, deployment_id in enumerate(deployment_ids):
                offset = index * width
                deployment_values = list(values[offset : offset + width])
                deployment_values.extend([None] * (width - len(deployment_values)))
                usage = {
                    "rpm": int(deployment_values[0] or 0),
                    "tpm": int(deployment_values[1] or 0),
                }
                for counter_name, value in zip(
                    USAGE_COUNTER_NAMES[2:],
                    deployment_values[2:],
                    strict=False,
                ):
                    parsed = int(value or 0)
                    if parsed > 0:
                        usage[counter_name] = parsed
                snapshots[deployment_id] = usage
            return snapshots
        except Exception as exc:
            self._handle_backend_failure(exc)
            self._prune_local_state()
            snapshots = {}
            now = time.time()
            for deployment_id in deployment_ids:
                usage = self._usage.get(deployment_id, {})
                if usage.get("window") != minute:
                    self._usage.pop(deployment_id, None)
                    self._drop_local_state_if_unused(deployment_id)
                    snapshots[deployment_id] = {"rpm": 0, "tpm": 0}
                    continue
                self._touch_local_state(deployment_id, now=now)
                snapshots[deployment_id] = self._materialize_usage_snapshot(usage)
            return snapshots

    @staticmethod
    def _normalize_usage_counters(counters: Mapping[str, int]) -> dict[str, int]:
        normalized = {"rpm": 1, "tpm": 0}
        for counter_name in USAGE_COUNTER_NAMES:
            if counter_name == "rpm":
                continue
            value = counters.get(counter_name)
            if value is None:
                continue
            normalized[counter_name] = max(0, int(value))
        if counters.get("rpm") is not None:
            normalized["rpm"] = max(1, int(counters.get("rpm") or 1))
        return normalized

    @staticmethod
    def _materialize_usage_snapshot(usage: Mapping[str, Any]) -> dict[str, int]:
        snapshot = {
            "rpm": int(usage.get("rpm", 0) or 0),
            "tpm": int(usage.get("tpm", 0) or 0),
        }
        for counter_name in USAGE_COUNTER_NAMES[2:]:
            value = int(usage.get(counter_name, 0) or 0)
            if value > 0:
                snapshot[counter_name] = value
        return snapshot

    async def set_cooldown(self, deployment_id: str, duration_sec: int, reason: str) -> None:
        key = f"cooldown:{deployment_id}"
        payload = json.dumps({"reason": reason, "at": int(time.time())})
        try:
            await self._redis_call("setex", key, max(1, int(duration_sec)), payload)
        except Exception as exc:
            self._handle_backend_failure(exc)
            self._cooldown_until[deployment_id] = time.time() + max(1, int(duration_sec))
            self._touch_local_state(deployment_id, now=time.time())

    async def clear_cooldown(self, deployment_id: str) -> None:
        key = f"cooldown:{deployment_id}"
        try:
            await self._redis_call("delete", key)
        except Exception as exc:
            self._handle_backend_failure(exc)
            self._cooldown_until.pop(deployment_id, None)
            self._drop_local_state_if_unused(deployment_id)

    async def is_cooled_down(self, deployment_id: str) -> bool:
        key = f"cooldown:{deployment_id}"
        try:
            exists = await self._redis_call("exists", key)
            return bool(exists)
        except Exception as exc:
            self._handle_backend_failure(exc)
            self._prune_local_state()
            until = self._cooldown_until.get(deployment_id)
            if not until:
                return False
            if until <= time.time():
                self._cooldown_until.pop(deployment_id, None)
                self._drop_local_state_if_unused(deployment_id)
                return False
            self._touch_local_state(deployment_id, now=time.time())
            return True

    async def get_cooldown_batch(self, deployment_ids: list[str]) -> dict[str, bool]:
        if not deployment_ids:
            return {}

        keys = [f"cooldown:{deployment_id}" for deployment_id in deployment_ids]
        try:
            values = await self._redis_call("mget", keys)
            return {
                deployment_id: value not in (None, "", b"")
                for deployment_id, value in zip(deployment_ids, values, strict=False)
            }
        except Exception as exc:
            self._handle_backend_failure(exc)
            self._prune_local_state()
            now = time.time()
            statuses: dict[str, bool] = {}
            for deployment_id in deployment_ids:
                until = self._cooldown_until.get(deployment_id)
                if not until:
                    statuses[deployment_id] = False
                    continue
                if until <= now:
                    self._cooldown_until.pop(deployment_id, None)
                    self._drop_local_state_if_unused(deployment_id)
                    statuses[deployment_id] = False
                    continue
                self._touch_local_state(deployment_id, now=now)
                statuses[deployment_id] = True
            return statuses

    async def record_success(self, deployment_id: str) -> None:
        failures_key = f"failures:{deployment_id}"
        health_key = f"health:{deployment_id}"
        now = str(int(time.time()))
        try:
            pipe = self.redis.pipeline()
            pipe.delete(failures_key)
            pipe.hset(
                health_key,
                mapping={
                    "healthy": "true",
                    "consecutive_failures": "0",
                    "last_success_at": now,
                },
            )
            pipe.hdel(health_key, "last_error", "last_error_at")
            await pipe.execute()
            self._mark_backend_healthy()
            return
        except Exception as exc:
            self._handle_backend_failure(exc)
            self._failures.pop(deployment_id, None)
            entry = self._health.setdefault(deployment_id, {})
            entry.update({"healthy": "true", "consecutive_failures": "0", "last_success_at": now})
            entry.pop("last_error", None)
            entry.pop("last_error_at", None)
            self._touch_local_state(deployment_id, now=time.time())

    async def record_failure(self, deployment_id: str, error: str) -> int:
        failures_key = f"failures:{deployment_id}"
        health_key = f"health:{deployment_id}"
        now = str(int(time.time()))
        try:
            pipe = self.redis.pipeline()
            pipe.incr(failures_key)
            pipe.expire(failures_key, 300)
            results = await pipe.execute()
            failure_count = int(results[0])
            await self._redis_call(
                "hset",
                health_key,
                mapping={
                    "consecutive_failures": str(failure_count),
                    "last_error": str(error)[:200],
                    "last_error_at": now,
                },
            )
            return failure_count
        except Exception as exc:
            self._handle_backend_failure(exc)
            failure_count = self._failures.get(deployment_id, 0) + 1
            self._failures[deployment_id] = failure_count
            entry = self._health.setdefault(deployment_id, {})
            entry.update(
                {
                    "consecutive_failures": str(failure_count),
                    "last_error": str(error)[:200],
                    "last_error_at": now,
                }
            )
            self._touch_local_state(deployment_id, now=time.time())
            return failure_count

    async def set_health(self, deployment_id: str, healthy: bool) -> None:
        health_key = f"health:{deployment_id}"
        value = "true" if healthy else "false"
        try:
            await self._redis_call("hset", health_key, mapping={"healthy": value})
        except Exception as exc:
            self._handle_backend_failure(exc)
            entry = self._health.setdefault(deployment_id, {})
            entry["healthy"] = value
            self._touch_local_state(deployment_id, now=time.time())

    async def get_health(self, deployment_id: str) -> dict[str, Any]:
        health = await self.get_health_batch([deployment_id])
        return health.get(deployment_id, {})

    async def get_health_batch(self, deployment_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not deployment_ids:
            return {}

        try:
            pipe = self.redis.pipeline()
            for deployment_id in deployment_ids:
                pipe.hgetall(f"health:{deployment_id}")
            results = await pipe.execute()
            self._mark_backend_healthy()
            return {
                deployment_id: dict(raw or {})
                for deployment_id, raw in zip(deployment_ids, results, strict=False)
            }
        except Exception as exc:
            self._handle_backend_failure(exc)
            self._prune_local_state()
            return {
                deployment_id: dict(self._health.get(deployment_id, {}))
                for deployment_id in deployment_ids
            }
