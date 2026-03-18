from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from src.models.errors import ServiceUnavailableError

logger = logging.getLogger(__name__)


class DeploymentStateBackend(Protocol):
    async def increment_active(self, deployment_id: str) -> int: ...

    async def decrement_active(self, deployment_id: str) -> int: ...

    async def get_active_requests(self, deployment_id: str) -> int: ...

    async def get_active_requests_batch(self, deployment_ids: list[str]) -> dict[str, int]: ...

    async def record_latency(self, deployment_id: str, latency_ms: float) -> None: ...

    async def get_latency_window(self, deployment_id: str, window_ms: int) -> list[tuple[int, float]]: ...

    async def get_latency_windows_batch(
        self,
        deployment_ids: list[str],
        window_ms: int,
    ) -> dict[str, list[tuple[int, float]]]: ...

    async def increment_usage(self, deployment_id: str, tokens: int, window: str | None = None) -> None: ...

    async def get_usage(self, deployment_id: str) -> dict[str, int]: ...

    async def get_usage_batch(self, deployment_ids: list[str]) -> dict[str, dict[str, int]]: ...

    async def set_cooldown(self, deployment_id: str, duration_sec: int, reason: str) -> None: ...

    async def clear_cooldown(self, deployment_id: str) -> None: ...

    async def is_cooled_down(self, deployment_id: str) -> bool: ...

    async def record_success(self, deployment_id: str) -> None: ...

    async def record_failure(self, deployment_id: str, error: str) -> int: ...

    async def set_health(self, deployment_id: str, healthy: bool) -> None: ...

    async def get_health(self, deployment_id: str) -> dict[str, Any]: ...

    async def get_health_batch(self, deployment_ids: list[str]) -> dict[str, dict[str, Any]]: ...


class RedisStateBackend:
    """Redis-backed runtime state for deployments with explicit degraded-mode behavior."""

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
        self.degraded_mode = degraded_mode if degraded_mode in {"fail_open", "fail_closed"} else "fail_open"
        self.local_state_ttl_sec = max(1, int(local_state_ttl_sec))
        self.max_local_latency_samples = max(1, int(max_local_latency_samples))
        self._active: dict[str, int] = {}
        self._latency: dict[str, list[tuple[int, float]]] = {}
        self._usage: dict[str, dict[str, int]] = {}
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
            logger.warning("router state backend entered %s mode: %s", next_mode, self._last_redis_error)

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
            if seen_at >= cutoff:
                continue

            if self._active.get(deployment_id, 0) > 0:
                continue

            cooldown_until = self._cooldown_until.get(deployment_id)
            if cooldown_until is not None and cooldown_until > current_time:
                continue

            self._active.pop(deployment_id, None)
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

    async def increment_active(self, deployment_id: str) -> int:
        key = f"active_requests:{deployment_id}"
        try:
            return int(await self._redis_call("incr", key))
        except Exception as exc:
            self._handle_backend_failure(exc)
            self._active[deployment_id] = self._active.get(deployment_id, 0) + 1
            self._touch_local_state(deployment_id)
            return self._active[deployment_id]

    async def decrement_active(self, deployment_id: str) -> int:
        key = f"active_requests:{deployment_id}"
        try:
            value = int(await self._redis_call("decr", key))
            if value < 0:
                await self._redis_call("set", key, "0")
                return 0
            return value
        except Exception as exc:
            self._handle_backend_failure(exc)
            value = max(0, self._active.get(deployment_id, 0) - 1)
            if value == 0:
                self._active.pop(deployment_id, None)
                self._drop_local_state_if_unused(deployment_id)
            else:
                self._active[deployment_id] = value
                self._touch_local_state(deployment_id)
            return value

    async def get_active_requests(self, deployment_id: str) -> int:
        key = f"active_requests:{deployment_id}"
        try:
            value = await self._redis_call("get", key)
            return int(value or 0)
        except Exception as exc:
            self._handle_backend_failure(exc)
            self._prune_local_state()
            return self._active.get(deployment_id, 0)

    async def get_active_requests_batch(self, deployment_ids: list[str]) -> dict[str, int]:
        if not deployment_ids:
            return {}

        keys = [f"active_requests:{deployment_id}" for deployment_id in deployment_ids]
        try:
            values = await self._redis_call("mget", keys)
            return {deployment_id: int(value or 0) for deployment_id, value in zip(deployment_ids, values, strict=False)}
        except Exception as exc:
            self._handle_backend_failure(exc)
            self._prune_local_state()
            return {deployment_id: self._active.get(deployment_id, 0) for deployment_id in deployment_ids}

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

    async def get_latency_window(self, deployment_id: str, window_ms: int) -> list[tuple[int, float]]:
        now_ms = int(time.time() * 1000)
        min_score = now_ms - window_ms
        key = f"latency:{deployment_id}"
        try:
            items = await self._redis_call("zrangebyscore", key, min_score, "+inf")
            window: list[tuple[int, float]] = []
            for item in items:
                ts_str, latency_str = str(item).split(":", 1)
                window.append((int(ts_str), float(latency_str)))
            return window
        except Exception as exc:
            self._handle_backend_failure(exc)
            self._prune_local_state()
            window = [(ts, lat) for ts, lat in self._latency.get(deployment_id, []) if ts >= min_score]
            if window:
                self._latency[deployment_id] = window[-self.max_local_latency_samples :]
                self._touch_local_state(deployment_id, now=time.time())
            else:
                self._latency.pop(deployment_id, None)
                self._drop_local_state_if_unused(deployment_id)
            return window

    async def get_latency_windows_batch(
        self,
        deployment_ids: list[str],
        window_ms: int,
    ) -> dict[str, list[tuple[int, float]]]:
        windows: dict[str, list[tuple[int, float]]] = {}
        for deployment_id in deployment_ids:
            windows[deployment_id] = await self.get_latency_window(deployment_id, window_ms)
        return windows

    async def increment_usage(self, deployment_id: str, tokens: int, window: str | None = None) -> None:
        minute = window or self._minute_window()
        rpm_key = f"usage_rpm:{deployment_id}:{minute}"
        tpm_key = f"usage_tpm:{deployment_id}:{minute}"
        try:
            pipe = self.redis.pipeline()
            pipe.incr(rpm_key)
            pipe.incrby(tpm_key, int(tokens))
            pipe.expire(rpm_key, 120)
            pipe.expire(tpm_key, 120)
            await pipe.execute()
            self._mark_backend_healthy()
            return
        except Exception as exc:
            self._handle_backend_failure(exc)
            usage = self._usage.setdefault(deployment_id, {"rpm": 0, "tpm": 0, "window": minute, "updated_at": int(time.time())})
            if usage.get("window") != minute:
                usage["rpm"] = 0
                usage["tpm"] = 0
                usage["window"] = minute
            usage["rpm"] = int(usage.get("rpm", 0)) + 1
            usage["tpm"] = int(usage.get("tpm", 0)) + int(tokens)
            usage["updated_at"] = int(time.time())
            self._touch_local_state(deployment_id, now=time.time())

    async def get_usage(self, deployment_id: str) -> dict[str, int]:
        minute = self._minute_window()
        rpm_key = f"usage_rpm:{deployment_id}:{minute}"
        tpm_key = f"usage_tpm:{deployment_id}:{minute}"
        try:
            values = await self._redis_call("mget", [rpm_key, tpm_key])
            return {"rpm": int(values[0] or 0), "tpm": int(values[1] or 0)}
        except Exception as exc:
            self._handle_backend_failure(exc)
            self._prune_local_state()
            usage = self._usage.get(deployment_id, {})
            if usage.get("window") != minute:
                self._usage.pop(deployment_id, None)
                self._drop_local_state_if_unused(deployment_id)
                return {"rpm": 0, "tpm": 0}
            self._touch_local_state(deployment_id, now=time.time())
            return {"rpm": int(usage.get("rpm", 0)), "tpm": int(usage.get("tpm", 0))}

    async def get_usage_batch(self, deployment_ids: list[str]) -> dict[str, dict[str, int]]:
        return {deployment_id: await self.get_usage(deployment_id) for deployment_id in deployment_ids}

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
        health_key = f"health:{deployment_id}"
        try:
            raw = await self._redis_call("hgetall", health_key)
            return dict(raw or {})
        except Exception as exc:
            self._handle_backend_failure(exc)
            self._prune_local_state()
            return dict(self._health.get(deployment_id, {}))

    async def get_health_batch(self, deployment_ids: list[str]) -> dict[str, dict[str, Any]]:
        return {deployment_id: await self.get_health(deployment_id) for deployment_id in deployment_ids}
