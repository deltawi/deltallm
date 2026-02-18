from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any, Protocol


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
    """Redis-backed runtime state for deployments with in-memory fallback."""

    def __init__(self, redis: Any | None, latency_window_ms: int = 300_000):
        self.redis = redis
        self.latency_window_ms = latency_window_ms
        self._active: dict[str, int] = {}
        self._latency: dict[str, list[tuple[int, float]]] = {}
        self._usage: dict[str, dict[str, int]] = {}
        self._cooldown_until: dict[str, float] = {}
        self._health: dict[str, dict[str, Any]] = {}
        self._failures: dict[str, int] = {}

    def _minute_window(self) -> str:
        return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M")

    async def _redis_call(self, method: str, *args, **kwargs):
        if self.redis is None:
            raise AttributeError("redis unavailable")
        fn = getattr(self.redis, method)
        return await fn(*args, **kwargs)

    async def increment_active(self, deployment_id: str) -> int:
        key = f"active_requests:{deployment_id}"
        try:
            return int(await self._redis_call("incr", key))
        except Exception:
            self._active[deployment_id] = self._active.get(deployment_id, 0) + 1
            return self._active[deployment_id]

    async def decrement_active(self, deployment_id: str) -> int:
        key = f"active_requests:{deployment_id}"
        try:
            value = int(await self._redis_call("decr", key))
            if value < 0:
                await self._redis_call("set", key, "0")
                return 0
            return value
        except Exception:
            self._active[deployment_id] = max(0, self._active.get(deployment_id, 0) - 1)
            return self._active[deployment_id]

    async def get_active_requests(self, deployment_id: str) -> int:
        key = f"active_requests:{deployment_id}"
        try:
            value = await self._redis_call("get", key)
            return int(value or 0)
        except Exception:
            return self._active.get(deployment_id, 0)

    async def get_active_requests_batch(self, deployment_ids: list[str]) -> dict[str, int]:
        if not deployment_ids:
            return {}

        keys = [f"active_requests:{deployment_id}" for deployment_id in deployment_ids]
        try:
            values = await self._redis_call("mget", keys)
            return {deployment_id: int(value or 0) for deployment_id, value in zip(deployment_ids, values, strict=False)}
        except Exception:
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
            return
        except Exception:
            window = self._latency.setdefault(deployment_id, [])
            window.append((timestamp_ms, float(latency_ms)))
            self._latency[deployment_id] = [(ts, lat) for ts, lat in window if ts >= cutoff]

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
        except Exception:
            return [(ts, lat) for ts, lat in self._latency.get(deployment_id, []) if ts >= min_score]

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
            return
        except Exception:
            usage = self._usage.setdefault(deployment_id, {"rpm": 0, "tpm": 0, "window": minute})
            if usage.get("window") != minute:
                usage["rpm"] = 0
                usage["tpm"] = 0
                usage["window"] = minute
            usage["rpm"] = int(usage.get("rpm", 0)) + 1
            usage["tpm"] = int(usage.get("tpm", 0)) + int(tokens)

    async def get_usage(self, deployment_id: str) -> dict[str, int]:
        minute = self._minute_window()
        rpm_key = f"usage_rpm:{deployment_id}:{minute}"
        tpm_key = f"usage_tpm:{deployment_id}:{minute}"
        try:
            values = await self._redis_call("mget", [rpm_key, tpm_key])
            return {"rpm": int(values[0] or 0), "tpm": int(values[1] or 0)}
        except Exception:
            usage = self._usage.get(deployment_id, {})
            if usage.get("window") != minute:
                return {"rpm": 0, "tpm": 0}
            return {"rpm": int(usage.get("rpm", 0)), "tpm": int(usage.get("tpm", 0))}

    async def get_usage_batch(self, deployment_ids: list[str]) -> dict[str, dict[str, int]]:
        return {deployment_id: await self.get_usage(deployment_id) for deployment_id in deployment_ids}

    async def set_cooldown(self, deployment_id: str, duration_sec: int, reason: str) -> None:
        key = f"cooldown:{deployment_id}"
        payload = json.dumps({"reason": reason, "at": int(time.time())})
        try:
            await self._redis_call("setex", key, max(1, int(duration_sec)), payload)
        except Exception:
            self._cooldown_until[deployment_id] = time.time() + max(1, int(duration_sec))

    async def clear_cooldown(self, deployment_id: str) -> None:
        key = f"cooldown:{deployment_id}"
        try:
            await self._redis_call("delete", key)
        except Exception:
            self._cooldown_until.pop(deployment_id, None)

    async def is_cooled_down(self, deployment_id: str) -> bool:
        key = f"cooldown:{deployment_id}"
        try:
            exists = await self._redis_call("exists", key)
            return bool(exists)
        except Exception:
            until = self._cooldown_until.get(deployment_id)
            if not until:
                return False
            if until <= time.time():
                self._cooldown_until.pop(deployment_id, None)
                return False
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
            await pipe.execute()
            return
        except Exception:
            self._failures[deployment_id] = 0
            entry = self._health.setdefault(deployment_id, {})
            entry.update({"healthy": "true", "consecutive_failures": "0", "last_success_at": now})

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
        except Exception:
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
            return failure_count

    async def set_health(self, deployment_id: str, healthy: bool) -> None:
        health_key = f"health:{deployment_id}"
        value = "true" if healthy else "false"
        try:
            await self._redis_call("hset", health_key, mapping={"healthy": value})
        except Exception:
            entry = self._health.setdefault(deployment_id, {})
            entry["healthy"] = value

    async def get_health(self, deployment_id: str) -> dict[str, Any]:
        health_key = f"health:{deployment_id}"
        try:
            raw = await self._redis_call("hgetall", health_key)
            return dict(raw or {})
        except Exception:
            return dict(self._health.get(deployment_id, {}))

    async def get_health_batch(self, deployment_ids: list[str]) -> dict[str, dict[str, Any]]:
        return {deployment_id: await self.get_health(deployment_id) for deployment_id in deployment_ids}
