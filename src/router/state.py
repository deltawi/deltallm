from __future__ import annotations

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
from src.router.health_state import (
    COOLDOWN_KIND_FIELD,
    FAILURE_WINDOW_SECONDS,
    HEALTH_FAILURE_SCRIPT,
    HEALTH_PROBE_CLAIM_SCRIPT,
    HEALTH_PROBE_RELEASE_SCRIPT,
    HEALTH_RECOVERY_RELEASE_SCRIPT,
    HEALTH_SUCCESS_SCRIPT,
    MANUAL_COOLDOWN_SCRIPT,
    RECOVERY_REQUIRED_FIELD,
    DeploymentHealthRef,
    DeploymentHealthState,
    HealthRefInput,
    HealthProbeClaim,
    HealthTransitionResult,
    coerce_health_ref,
    health_state_ttl_seconds,
)
from src.router.redis_keys import RouterHealthProbeScope, RouterRedisKeyspace

logger = logging.getLogger(__name__)

USAGE_COUNTER_NAMES = ("rpm", "tpm", "image_pm", "audio_seconds_pm", "char_pm", "rerank_units_pm")
_ATTEMPT_LEASE_CLEANUP_GRACE_MS = 1_000
_ATTEMPT_LEGACY_COMPAT_TTL_SECONDS = 86_400
_HEALTH_INVALIDATION_CHUNK_REFS = 100
_MAX_HEALTH_INVALIDATION_REFS = 1_000

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
for index = 1, capacity_count do
  local usage = tonumber(redis.call('GET', KEYS[5 + index]) or '0')
  local limit = tonumber(ARGV[4 + index])
  if usage >= limit then
    return {0, 'capacity', 0}
  end
end

local recovery = 0
if healthy == 'false' then
  if redis.call('HGET', KEYS[4], 'recovery_required') ~= 'true' then
    return {0, 'unhealthy', 0}
  end
  local claimed = redis.call('SET', KEYS[5], owner_token, 'NX', 'PX', lease_ttl_ms)
  if not claimed then
    return {0, 'recovery_in_progress', 0}
  end
  recovery = 1
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
return {1, 'acquired', active, expires_at_ms, recovery}
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
if redis.call('GET', KEYS[3]) == ARGV[1] then
  redis.call('DEL', KEYS[3])
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
        health_ref: HealthRefInput,
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

    async def is_cooled_down(self, health_ref: HealthRefInput) -> bool: ...

    async def get_cooldown_batch(self, health_refs: list[HealthRefInput]) -> dict[str, bool]: ...

    async def apply_health_success(
        self,
        health_ref: HealthRefInput,
        *,
        recovery_token: str | None = None,
    ) -> HealthTransitionResult: ...

    async def apply_health_failure(
        self,
        health_ref: HealthRefInput,
        error: str,
        *,
        allowed_fails: int,
        cooldown_seconds: int,
        recovery_token: str | None = None,
    ) -> HealthTransitionResult: ...

    async def apply_manual_cooldown(
        self,
        health_ref: HealthRefInput,
        duration_seconds: int,
        reason: str,
    ) -> None: ...

    async def claim_health_probe(
        self,
        health_ref: HealthRefInput,
        ttl_seconds: int,
        *,
        scope: RouterHealthProbeScope = "background",
    ) -> HealthProbeClaim | None: ...

    async def release_health_probe(
        self,
        health_ref: HealthRefInput,
        claim: HealthProbeClaim,
    ) -> None: ...

    async def release_health_recovery(
        self,
        health_ref: HealthRefInput,
        owner_token: str,
    ) -> None: ...

    async def get_health(self, health_ref: HealthRefInput) -> dict[str, Any]: ...

    async def get_health_batch(
        self, health_refs: list[HealthRefInput]
    ) -> dict[str, dict[str, Any]]: ...

    async def invalidate_health_state(self, health_refs: list[HealthRefInput]) -> bool: ...


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
        keyspace: RouterRedisKeyspace | None = None,
    ):
        self.redis = redis
        self.latency_window_ms = latency_window_ms
        self.degraded_mode = (
            degraded_mode if degraded_mode in {"fail_open", "fail_closed"} else "fail_open"
        )
        self.local_state_ttl_sec = max(1, int(local_state_ttl_sec))
        self.max_local_latency_samples = max(1, int(max_local_latency_samples))
        self.keyspace = keyspace or RouterRedisKeyspace()
        self._active: dict[str, int] = {}
        self._active_permits: dict[str, dict[str, float]] = {}
        self._latency: dict[str, list[tuple[int, float]]] = {}
        self._usage: dict[str, dict[str, Any]] = {}
        self._cooldown_until: dict[DeploymentHealthRef, float] = {}
        self._recovery_permits: dict[DeploymentHealthRef, tuple[str, float]] = {}
        self._probe_claims: dict[
            tuple[RouterHealthProbeScope, DeploymentHealthRef], tuple[str, float]
        ] = {}
        self._health: dict[DeploymentHealthRef, dict[str, Any]] = {}
        self._failures: dict[DeploymentHealthRef, int] = {}
        self._failure_expires_at: dict[DeploymentHealthRef, float] = {}
        self._local_health_last_seen: dict[DeploymentHealthRef, float] = {}
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
            "local_health_entries": len(self._local_health_last_seen),
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
            # Redis is authoritative after reconnect. Replaying per-process
            # health evidence could overwrite newer cluster-wide outcomes, so
            # discard only degraded health/cooldown claims. Owner-scoped local
            # attempt permits remain until their normal release or expiry.
            self._cooldown_until.clear()
            self._health.clear()
            self._failures.clear()
            self._failure_expires_at.clear()
            self._recovery_permits.clear()
            self._probe_claims.clear()
            self._local_health_last_seen.clear()
        self._backend_mode = "redis"
        self._last_redis_error = None
        self._last_redis_error_at = None

    def _handle_backend_failure(self, exc: Exception) -> None:
        self._mark_backend_failure(exc)
        if self.degraded_mode == "fail_closed":
            raise ServiceUnavailableError(message="Router state backend unavailable") from exc

    def _touch_local_state(self, deployment_id: str, *, now: float | None = None) -> None:
        self._local_last_seen[deployment_id] = now or time.time()

    def _touch_local_health(
        self, health_ref: DeploymentHealthRef, *, now: float | None = None
    ) -> None:
        self._local_health_last_seen[health_ref] = now or time.time()

    def _drop_local_probe_claims(self, health_ref: DeploymentHealthRef) -> None:
        for key in [key for key in self._probe_claims if key[1] == health_ref]:
            self._probe_claims.pop(key, None)

    def _drop_local_state_if_unused(self, deployment_id: str) -> None:
        self._prune_local_attempt_permits(deployment_id)
        if self._active.get(deployment_id, 0) > 0:
            return
        if deployment_id in self._latency:
            return
        if deployment_id in self._usage:
            return
        self._active.pop(deployment_id, None)
        self._local_last_seen.pop(deployment_id, None)

    def _drop_local_health_state_if_unused(self, health_ref: DeploymentHealthRef) -> None:
        current_time = time.time()
        if self._cooldown_until.get(health_ref, 0.0) > current_time:
            return
        if self._health.get(health_ref):
            return
        if self._failures.get(health_ref, 0) > 0:
            return
        if self._recovery_permits.get(health_ref, ("", 0.0))[1] > current_time:
            return
        if any(
            claim_ref == health_ref and expires_at > current_time
            for (_scope, claim_ref), (_token, expires_at) in self._probe_claims.items()
        ):
            return
        self._drop_local_probe_claims(health_ref)
        self._cooldown_until.pop(health_ref, None)
        self._recovery_permits.pop(health_ref, None)
        self._failures.pop(health_ref, None)
        self._failure_expires_at.pop(health_ref, None)
        self._local_health_last_seen.pop(health_ref, None)

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

            self._active.pop(deployment_id, None)
            self._active_permits.pop(deployment_id, None)
            self._latency.pop(deployment_id, None)
            self._usage.pop(deployment_id, None)
            self._local_last_seen.pop(deployment_id, None)

        for health_ref, seen_at in list(self._local_health_last_seen.items()):
            if seen_at >= cutoff:
                continue
            if self._cooldown_until.get(health_ref, 0.0) > current_time:
                continue
            self._cooldown_until.pop(health_ref, None)
            self._recovery_permits.pop(health_ref, None)
            self._drop_local_probe_claims(health_ref)
            self._health.pop(health_ref, None)
            self._failures.pop(health_ref, None)
            self._failure_expires_at.pop(health_ref, None)
            self._local_health_last_seen.pop(health_ref, None)

    async def _redis_call(self, method: str, *args, **kwargs):
        if self.redis is None:
            raise AttributeError("redis unavailable")
        fn = getattr(self.redis, method)
        result = await fn(*args, **kwargs)
        self._mark_backend_healthy()
        return result

    async def acquire_attempt(
        self,
        health_ref: HealthRefInput,
        capacity: AttemptCapacity,
        *,
        lease_ttl_seconds: int = DEFAULT_ATTEMPT_PERMIT_TTL_SECONDS,
    ) -> AttemptPermit:
        resolved_ref = coerce_health_ref(health_ref)
        deployment_id = resolved_ref.deployment_id
        minute = self._minute_window()
        normalized_ttl_seconds = max(1, int(lease_ttl_seconds))
        owner_token = secrets.token_urlsafe(18)
        capacity_keys = [
            self._usage_key(deployment_id, item.counter, minute) for item in capacity.limits
        ]
        keys = [
            self.keyspace.active_requests(deployment_id),
            self._attempt_owners_key(deployment_id),
            self.keyspace.cooldown(deployment_id, resolved_ref.generation),
            self.keyspace.health(deployment_id, resolved_ref.generation),
            self._recovery_key(resolved_ref),
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
                    health_ref=resolved_ref,
                    acquired=True,
                    backend="redis",
                    owner_token=owner_token,
                    expires_at_ms=int(raw[3]),
                    active_requests=int(raw[2]),
                    recovery=len(raw) >= 5 and int(raw[4]) == 1,
                )
            return AttemptPermit(
                deployment_id=deployment_id,
                health_ref=resolved_ref,
                acquired=False,
                rejection_reason=AttemptRejectionReason(self._decode_redis_text(raw[1])),
            )
        except Exception as exc:
            self._handle_backend_failure(exc)
            return self._acquire_local_attempt(
                health_ref=resolved_ref,
                owner_token=owner_token,
                capacity=capacity,
                lease_ttl_seconds=normalized_ttl_seconds,
            )

    async def release_attempt(self, permit: AttemptPermit) -> int | None:
        if not permit.acquired or permit.backend is None:
            return 0
        if permit.backend == "local":
            return self._release_local_attempt(permit)
        if not permit.owner_token:
            return await self.get_active_requests(permit.deployment_id)

        keys = [
            self.keyspace.active_requests(permit.deployment_id),
            self._attempt_owners_key(permit.deployment_id),
            self._recovery_key(permit.health_ref),
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

    def _acquire_local_attempt(
        self,
        *,
        health_ref: DeploymentHealthRef,
        owner_token: str,
        capacity: AttemptCapacity,
        lease_ttl_seconds: int,
    ) -> AttemptPermit:
        deployment_id = health_ref.deployment_id
        now = time.time()
        cooldown_until = self._cooldown_until.get(health_ref)
        if cooldown_until is not None:
            if cooldown_until > now:
                self._touch_local_health(health_ref, now=now)
                return self._rejected_attempt(health_ref, AttemptRejectionReason.COOLDOWN)
            self._cooldown_until.pop(health_ref, None)

        usage = self._usage.get(deployment_id, {})
        if usage.get("window") != self._minute_window():
            usage = {}
        if any(int(usage.get(item.counter, 0) or 0) >= item.limit for item in capacity.limits):
            self._touch_local_state(deployment_id, now=now)
            return self._rejected_attempt(health_ref, AttemptRejectionReason.CAPACITY)

        recovery = False
        health = self._health.get(health_ref, {})
        if health.get("healthy", "true") == "false":
            if health.get(RECOVERY_REQUIRED_FIELD) != "true":
                self._touch_local_health(health_ref, now=now)
                return self._rejected_attempt(health_ref, AttemptRejectionReason.UNHEALTHY)
            current_recovery = self._recovery_permits.get(health_ref)
            if current_recovery is not None and current_recovery[1] > now:
                self._touch_local_health(health_ref, now=now)
                return self._rejected_attempt(
                    health_ref,
                    AttemptRejectionReason.RECOVERY_IN_PROGRESS,
                )
            recovery = True

        expires_at = now + lease_ttl_seconds
        if recovery:
            self._recovery_permits[health_ref] = (owner_token, expires_at)
        active_requests = self._prune_local_attempt_permits(deployment_id, now=now) + 1
        self._active_permits.setdefault(deployment_id, {})[owner_token] = expires_at
        self._active[deployment_id] = active_requests
        self._touch_local_state(deployment_id, now=now)
        self._touch_local_health(health_ref, now=now)
        return AttemptPermit(
            deployment_id=deployment_id,
            health_ref=health_ref,
            acquired=True,
            backend="local",
            owner_token=owner_token,
            expires_at_ms=int(expires_at * 1000),
            active_requests=active_requests,
            recovery=recovery,
        )

    @staticmethod
    def _rejected_attempt(
        health_ref: DeploymentHealthRef,
        reason: AttemptRejectionReason,
    ) -> AttemptPermit:
        return AttemptPermit(
            deployment_id=health_ref.deployment_id,
            health_ref=health_ref,
            acquired=False,
            rejection_reason=reason,
        )

    def _release_local_attempt(self, permit: AttemptPermit) -> int:
        permits = self._active_permits.get(permit.deployment_id)
        if permits is not None and permit.owner_token:
            permits.pop(permit.owner_token, None)
        recovery = self._recovery_permits.get(permit.health_ref)
        if recovery is not None and recovery[0] == permit.owner_token:
            self._recovery_permits.pop(permit.health_ref, None)
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
        current_time = time.time() if now is None else now
        for health_ref, recovery in list(self._recovery_permits.items()):
            if health_ref.deployment_id == deployment_id and recovery[1] <= current_time:
                self._recovery_permits.pop(health_ref, None)
        permits = self._active_permits.get(deployment_id)
        if not permits:
            self._active_permits.pop(deployment_id, None)
            self._active.pop(deployment_id, None)
            return 0

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

    def _attempt_owners_key(self, deployment_id: str) -> str:
        return self.keyspace.attempt_owners(deployment_id)

    def _recovery_key(self, health_ref: DeploymentHealthRef) -> str:
        return self.keyspace.health_recovery(
            health_ref.deployment_id,
            health_ref.generation,
        )

    def _usage_key(self, deployment_id: str, counter: UsageCounterName, minute: str) -> str:
        return self.keyspace.usage(deployment_id, counter, minute)

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
                self.keyspace.active_requests(deployment_id),
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
        key = self.keyspace.latency(deployment_id)
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
                pipe.zrangebyscore(self.keyspace.latency(deployment_id), min_score, "+inf")
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
            counter_name: self._usage_key(deployment_id, counter_name, minute)
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
            self._usage_key(deployment_id, counter_name, minute)
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

    async def is_cooled_down(self, health_ref: HealthRefInput) -> bool:
        resolved_ref = coerce_health_ref(health_ref)
        key = self.keyspace.cooldown(resolved_ref.deployment_id, resolved_ref.generation)
        try:
            exists = await self._redis_call("exists", key)
            return bool(exists)
        except Exception as exc:
            self._handle_backend_failure(exc)
            self._prune_local_state()
            until = self._cooldown_until.get(resolved_ref)
            if not until:
                return False
            if until <= time.time():
                self._cooldown_until.pop(resolved_ref, None)
                self._drop_local_health_state_if_unused(resolved_ref)
                return False
            self._touch_local_health(resolved_ref, now=time.time())
            return True

    async def get_cooldown_batch(self, health_refs: list[HealthRefInput]) -> dict[str, bool]:
        if not health_refs:
            return {}

        resolved_refs = [coerce_health_ref(item) for item in health_refs]
        keys = [
            self.keyspace.cooldown(item.deployment_id, item.generation) for item in resolved_refs
        ]
        try:
            values = await self._redis_call("mget", keys)
            return {
                item.deployment_id: value not in (None, "", b"")
                for item, value in zip(resolved_refs, values, strict=False)
            }
        except Exception as exc:
            self._handle_backend_failure(exc)
            self._prune_local_state()
            now = time.time()
            statuses: dict[str, bool] = {}
            for item in resolved_refs:
                until = self._cooldown_until.get(item)
                if not until:
                    statuses[item.deployment_id] = False
                    continue
                if until <= now:
                    self._cooldown_until.pop(item, None)
                    self._drop_local_health_state_if_unused(item)
                    statuses[item.deployment_id] = False
                    continue
                self._touch_local_health(item, now=now)
                statuses[item.deployment_id] = True
            return statuses

    async def apply_health_success(
        self,
        health_ref: HealthRefInput,
        *,
        recovery_token: str | None = None,
    ) -> HealthTransitionResult:
        resolved_ref = coerce_health_ref(health_ref)
        keys = self._health_transition_keys(resolved_ref)
        now = str(int(time.time()))
        try:
            raw = await self._redis_call(
                "eval",
                HEALTH_SUCCESS_SCRIPT,
                len(keys),
                *keys,
                now,
                recovery_token or "",
                health_state_ttl_seconds(),
            )
            return HealthTransitionResult(
                applied=int(raw[0]) == 1,
                state=DeploymentHealthState(self._decode_redis_text(raw[3])),
                recovered=int(raw[2]) == 1,
            )
        except Exception as exc:
            self._handle_backend_failure(exc)
            if not self._local_health_transition_is_owned(resolved_ref, recovery_token):
                return HealthTransitionResult(
                    applied=False,
                    state=DeploymentHealthState.RECOVERABLE,
                )
            entry = self._health.get(resolved_ref, {})
            if (
                entry.get(COOLDOWN_KIND_FIELD) == "manual"
                and self._cooldown_until.get(resolved_ref, 0.0) > time.time()
            ):
                return HealthTransitionResult(
                    applied=False,
                    state=DeploymentHealthState.COOLDOWN,
                )
            recovered = entry.get("healthy") == "false" or resolved_ref in self._cooldown_until
            self._failures.pop(resolved_ref, None)
            self._failure_expires_at.pop(resolved_ref, None)
            self._cooldown_until.pop(resolved_ref, None)
            self._recovery_permits.pop(resolved_ref, None)
            entry = self._health.setdefault(resolved_ref, {})
            entry.update(
                {
                    "healthy": "true",
                    RECOVERY_REQUIRED_FIELD: "false",
                    "consecutive_failures": "0",
                    "last_success_at": now,
                }
            )
            entry.pop("last_error", None)
            entry.pop("last_error_at", None)
            entry.pop(COOLDOWN_KIND_FIELD, None)
            self._touch_local_health(resolved_ref, now=time.time())
            return HealthTransitionResult(
                applied=True,
                state=DeploymentHealthState.HEALTHY,
                recovered=recovered,
            )

    async def apply_health_failure(
        self,
        health_ref: HealthRefInput,
        error: str,
        *,
        allowed_fails: int,
        cooldown_seconds: int,
        recovery_token: str | None = None,
    ) -> HealthTransitionResult:
        resolved_ref = coerce_health_ref(health_ref)
        keys = self._health_transition_keys(resolved_ref)
        now = str(int(time.time()))
        normalized_error = str(error)[:200]
        normalized_allowed_fails = max(0, int(allowed_fails))
        normalized_cooldown = max(1, int(cooldown_seconds))
        try:
            raw = await self._redis_call(
                "eval",
                HEALTH_FAILURE_SCRIPT,
                len(keys),
                *keys,
                normalized_error,
                normalized_allowed_fails,
                FAILURE_WINDOW_SECONDS,
                normalized_cooldown,
                now,
                recovery_token or "",
                health_state_ttl_seconds(normalized_cooldown),
            )
            return HealthTransitionResult(
                applied=int(raw[0]) == 1,
                state=DeploymentHealthState(self._decode_redis_text(raw[3])),
                failure_count=int(raw[1]),
                entered_cooldown=int(raw[2]) == 1,
            )
        except Exception as exc:
            self._handle_backend_failure(exc)
            if not self._local_health_transition_is_owned(resolved_ref, recovery_token):
                return HealthTransitionResult(
                    applied=False,
                    state=DeploymentHealthState.RECOVERABLE,
                )

            current_time = time.time()
            if self._failure_expires_at.get(resolved_ref, 0.0) <= current_time:
                self._failures.pop(resolved_ref, None)
            failure_count = self._failures.get(resolved_ref, 0) + 1
            self._failures[resolved_ref] = failure_count
            self._failure_expires_at[resolved_ref] = current_time + FAILURE_WINDOW_SECONDS
            entry = self._health.setdefault(resolved_ref, {})
            entry.update(
                {
                    "consecutive_failures": str(failure_count),
                    "last_error": normalized_error,
                    "last_error_at": now,
                }
            )
            entered_cooldown = False
            state = DeploymentHealthState.HEALTHY
            recovery_required = entry.get(RECOVERY_REQUIRED_FIELD) == "true"
            if recovery_required or failure_count > normalized_allowed_fails:
                state = DeploymentHealthState.COOLDOWN
                cooldown_until = self._cooldown_until.get(resolved_ref)
                if cooldown_until is None or cooldown_until <= time.time():
                    self._cooldown_until[resolved_ref] = time.time() + normalized_cooldown
                    entered_cooldown = True
                    entry[COOLDOWN_KIND_FIELD] = "automatic"
                entry.update(
                    {
                        "healthy": "false",
                        RECOVERY_REQUIRED_FIELD: "true",
                    }
                )
            if recovery_token is not None:
                self._recovery_permits.pop(resolved_ref, None)
            self._touch_local_health(resolved_ref, now=time.time())
            return HealthTransitionResult(
                applied=True,
                state=state,
                failure_count=failure_count,
                entered_cooldown=entered_cooldown,
            )

    async def apply_manual_cooldown(
        self,
        health_ref: HealthRefInput,
        duration_seconds: int,
        reason: str,
    ) -> None:
        resolved_ref = coerce_health_ref(health_ref)
        normalized_duration = max(1, int(duration_seconds))
        normalized_reason = str(reason)[:200]
        now = str(int(time.time()))
        keys = [
            self.keyspace.cooldown(resolved_ref.deployment_id, resolved_ref.generation),
            self.keyspace.health(resolved_ref.deployment_id, resolved_ref.generation),
            self._recovery_key(resolved_ref),
        ]
        try:
            await self._redis_call(
                "eval",
                MANUAL_COOLDOWN_SCRIPT,
                len(keys),
                *keys,
                normalized_duration,
                normalized_reason,
                now,
                health_state_ttl_seconds(normalized_duration),
            )
        except Exception as exc:
            self._handle_backend_failure(exc)
            self._cooldown_until[resolved_ref] = time.time() + normalized_duration
            self._recovery_permits.pop(resolved_ref, None)
            entry = self._health.setdefault(resolved_ref, {})
            entry.update(
                {
                    "healthy": "false",
                    RECOVERY_REQUIRED_FIELD: "true",
                    COOLDOWN_KIND_FIELD: "manual",
                    "last_error": normalized_reason,
                    "last_error_at": now,
                }
            )
            self._touch_local_health(resolved_ref, now=time.time())

    async def claim_health_probe(
        self,
        health_ref: HealthRefInput,
        ttl_seconds: int,
        *,
        scope: RouterHealthProbeScope = "background",
    ) -> HealthProbeClaim | None:
        resolved_ref = coerce_health_ref(health_ref)
        normalized_ttl = max(1, int(ttl_seconds))
        token = secrets.token_urlsafe(18)
        keys = [
            self.keyspace.health_probe(
                resolved_ref.deployment_id,
                scope,
                resolved_ref.generation,
            ),
            self.keyspace.cooldown(resolved_ref.deployment_id, resolved_ref.generation),
            self.keyspace.health(resolved_ref.deployment_id, resolved_ref.generation),
            self._recovery_key(resolved_ref),
        ]
        try:
            raw = await self._redis_call(
                "eval",
                HEALTH_PROBE_CLAIM_SCRIPT,
                len(keys),
                *keys,
                token,
                normalized_ttl * 1000,
            )
            if int(raw[0]) != 1:
                return None
            return HealthProbeClaim(
                health_ref=resolved_ref,
                owner_token=token,
                scope=scope,
                recovery=int(raw[1]) == 1,
            )
        except Exception as exc:
            self._handle_backend_failure(exc)
            now = time.time()
            probe_key = (scope, resolved_ref)
            probe_claim = self._probe_claims.get(probe_key)
            if probe_claim is not None and probe_claim[1] > now:
                return None

            cooldown_until = self._cooldown_until.get(resolved_ref, 0.0)
            recoverable = (
                cooldown_until <= now
                and self._health.get(resolved_ref, {}).get("healthy") == "false"
                and self._health.get(resolved_ref, {}).get(RECOVERY_REQUIRED_FIELD) == "true"
            )
            recovery = self._recovery_permits.get(resolved_ref)
            if recoverable and recovery is not None and recovery[1] > now:
                return None

            expires_at = now + normalized_ttl
            self._probe_claims[probe_key] = (token, expires_at)
            if recoverable:
                self._cooldown_until.pop(resolved_ref, None)
                self._recovery_permits[resolved_ref] = (token, expires_at)
            self._touch_local_health(resolved_ref, now=now)
            return HealthProbeClaim(
                health_ref=resolved_ref,
                owner_token=token,
                scope=scope,
                recovery=recoverable,
            )

    async def release_health_probe(
        self,
        health_ref: HealthRefInput,
        claim: HealthProbeClaim,
    ) -> None:
        resolved_ref = coerce_health_ref(health_ref)
        if claim.health_ref != resolved_ref:
            return
        try:
            await self._redis_call(
                "eval",
                HEALTH_PROBE_RELEASE_SCRIPT,
                1,
                self.keyspace.health_probe(
                    resolved_ref.deployment_id,
                    claim.scope,
                    resolved_ref.generation,
                ),
                claim.owner_token,
            )
        except Exception as exc:
            self._handle_backend_failure(exc)
            probe_key = (claim.scope, resolved_ref)
            current = self._probe_claims.get(probe_key)
            if current is not None and current[0] == claim.owner_token:
                self._probe_claims.pop(probe_key, None)

    async def release_health_recovery(
        self,
        health_ref: HealthRefInput,
        owner_token: str,
    ) -> None:
        resolved_ref = coerce_health_ref(health_ref)
        try:
            await self._redis_call(
                "eval",
                HEALTH_RECOVERY_RELEASE_SCRIPT,
                1,
                self._recovery_key(resolved_ref),
                owner_token,
            )
        except Exception as exc:
            self._handle_backend_failure(exc)
            recovery = self._recovery_permits.get(resolved_ref)
            if recovery is not None and recovery[0] == owner_token:
                self._recovery_permits.pop(resolved_ref, None)

    def _health_transition_keys(self, health_ref: DeploymentHealthRef) -> list[str]:
        return [
            self.keyspace.health_failures(health_ref.deployment_id, health_ref.generation),
            self.keyspace.health(health_ref.deployment_id, health_ref.generation),
            self.keyspace.cooldown(health_ref.deployment_id, health_ref.generation),
            self._recovery_key(health_ref),
        ]

    def _local_health_transition_is_owned(
        self,
        health_ref: DeploymentHealthRef,
        recovery_token: str | None,
    ) -> bool:
        current = self._recovery_permits.get(health_ref)
        now = time.time()
        if current is not None and current[1] <= now:
            self._recovery_permits.pop(health_ref, None)
            current = None
        if recovery_token is None:
            entry = self._health.get(health_ref, {})
            unhealthy = (
                entry.get("healthy") == "false" or entry.get(RECOVERY_REQUIRED_FIELD) == "true"
            )
            active_cooldown = self._cooldown_until.get(health_ref, 0.0) > now
            return not unhealthy and not active_cooldown and current is None
        if current is None:
            return False
        return current[0] == recovery_token

    async def get_health(self, health_ref: HealthRefInput) -> dict[str, Any]:
        resolved_ref = coerce_health_ref(health_ref)
        health = await self.get_health_batch([resolved_ref])
        return health.get(resolved_ref.deployment_id, {})

    async def get_health_batch(
        self, health_refs: list[HealthRefInput]
    ) -> dict[str, dict[str, Any]]:
        if not health_refs:
            return {}

        resolved_refs = [coerce_health_ref(item) for item in health_refs]
        try:
            pipe = self.redis.pipeline()
            for item in resolved_refs:
                pipe.hgetall(self.keyspace.health(item.deployment_id, item.generation))
            results = await pipe.execute()
            self._mark_backend_healthy()
            return {
                item.deployment_id: dict(raw or {})
                for item, raw in zip(resolved_refs, results, strict=False)
            }
        except Exception as exc:
            self._handle_backend_failure(exc)
            self._prune_local_state()
            return {item.deployment_id: dict(self._health.get(item, {})) for item in resolved_refs}

    async def invalidate_health_state(self, health_refs: list[HealthRefInput]) -> bool:
        normalized_refs: list[DeploymentHealthRef] = []
        seen: set[DeploymentHealthRef] = set()
        for item in health_refs:
            health_ref = coerce_health_ref(item)
            if health_ref in seen:
                continue
            seen.add(health_ref)
            normalized_refs.append(health_ref)
        if not normalized_refs:
            return True

        bounded_refs = normalized_refs[:_MAX_HEALTH_INVALIDATION_REFS]
        complete = len(bounded_refs) == len(normalized_refs)
        self._invalidate_local_health_state(bounded_refs)
        try:
            for offset in range(0, len(bounded_refs), _HEALTH_INVALIDATION_CHUNK_REFS):
                chunk = bounded_refs[offset : offset + _HEALTH_INVALIDATION_CHUNK_REFS]
                await self._redis_call("delete", *self._health_invalidation_keys(chunk))
            return complete
        except Exception as exc:
            self._handle_backend_failure(exc)
            return False

    def _health_invalidation_keys(
        self,
        health_refs: list[DeploymentHealthRef],
    ) -> list[str]:
        return [
            key
            for item in health_refs
            for key in (
                self.keyspace.health_failures(item.deployment_id, item.generation),
                self.keyspace.health(item.deployment_id, item.generation),
                self.keyspace.cooldown(item.deployment_id, item.generation),
                self._recovery_key(item),
                self.keyspace.health_probe(
                    item.deployment_id,
                    "background",
                    item.generation,
                ),
                self.keyspace.health_probe(
                    item.deployment_id,
                    "manual",
                    item.generation,
                ),
            )
        ]

    def _invalidate_local_health_state(self, health_refs: list[DeploymentHealthRef]) -> None:
        for item in health_refs:
            self._cooldown_until.pop(item, None)
            self._recovery_permits.pop(item, None)
            self._drop_local_probe_claims(item)
            self._health.pop(item, None)
            self._failures.pop(item, None)
            self._failure_expires_at.pop(item, None)
            self._local_health_last_seen.pop(item, None)
            self._drop_local_state_if_unused(item.deployment_id)
