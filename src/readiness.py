"""Bounded, shared readiness probes; process and worker failures are never cached."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from time import monotonic

from src.process_lifecycle import ProcessLifecycle
from src.metrics.lifecycle import readiness_probes, readiness_seconds


@dataclass(frozen=True)
class HealthCheck:
    ready: bool
    state: str


Checks = dict[str, HealthCheck]
Probe = Callable[[], Awaitable[object]]


class ReadinessRuntime:
    def __init__(
        self,
        *,
        lifecycle: ProcessLifecycle,
        probes: Mapping[str, Probe],
        workers: Callable[[], tuple[Checks, Checks]],
        max_readers: int = 4,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if len(probes) > 6 or not 1 <= max_readers <= 100:
            raise ValueError("readiness allocation exceeds process bounds")
        self.lifecycle = lifecycle
        self.probes = dict(probes)
        self.workers = workers
        self.max_readers = max_readers
        self._clock = clock
        self._readers = 0
        self._refresh: asyncio.Task[Checks] | None = None
        self._probes: dict[str, asyncio.Task[HealthCheck]] = {}
        self._cached: Checks | None = None
        self._expires = 0.0
        self._closed = False

    async def payload(self) -> dict[str, object]:
        if self._readers >= self.max_readers:
            return self._payload({name: HealthCheck(False, "busy") for name in self.probes})
        self._readers += 1
        try:
            checks = await self._dependencies()
            return self._payload(checks)
        finally:
            self._readers -= 1

    def _payload(self, dependencies: Checks) -> dict[str, object]:
        required, optional = self.workers()
        required.update(dependencies)
        required["process"] = HealthCheck(self.lifecycle.ready, self.lifecycle.state.value)
        return {
            "status": "ok" if all(check.ready for check in required.values()) else "degraded",
            "checks": {name: check.ready for name, check in required.items()},
            "details": {
                name: {"state": check.state} for name, check in (optional | required).items()
            },
        }

    async def _dependencies(self) -> Checks:
        unavailable = {name: HealthCheck(False, "unavailable") for name in self.probes}
        if self._closed or self.lifecycle.draining:
            # No dependency I/O is needed to prove that this pod is draining.
            return self._cached or unavailable
        if self._cached is not None and self._clock() < self._expires:
            return self._cached
        if self._refresh is not None and self._refresh.cancelling() and not self._refresh.done():
            return unavailable
        if self._refresh is None or self._refresh.done():
            # Timed-out native work retains its allocation until it really stops.
            if any(not task.done() for task in self._probes.values()):
                return {name: HealthCheck(False, "timeout") for name in self.probes}
            self._refresh = asyncio.create_task(self._run_refresh(), name="readiness-refresh")
            self._refresh.add_done_callback(_observe)
        try:
            return await asyncio.shield(self._refresh)
        except asyncio.CancelledError:
            if self._readers == 1:
                self._refresh.cancel()
                for task in self._probes.values():
                    task.cancel()
            raise

    async def _run_refresh(self) -> Checks:
        started = monotonic()
        self._probes = {
            name: asyncio.create_task(_probe(operation), name=f"readiness-{name}")
            for name, operation in self.probes.items()
        }
        for task in self._probes.values():
            task.add_done_callback(_observe)
        try:
            if self._probes:
                await asyncio.wait(
                    self._probes.values(),
                    timeout=self.lifecycle.settings.readiness_probe_timeout_seconds,
                )
            result: Checks = {}
            for name, task in self._probes.items():
                if task.done() and not task.cancelled():
                    result[name] = task.result()
                else:
                    result[name] = HealthCheck(False, "timeout")
                    task.cancel()
            for name, check in result.items():
                readiness_probes.labels(name, check.state).inc()
            self._cached = result
            self._expires = self._clock() + self.lifecycle.settings.readiness_cache_seconds
            return result
        finally:
            readiness_seconds.observe(monotonic() - started)
            for task in self._probes.values():
                if not task.done():
                    task.cancel()

    async def close(self, *, deadline: float) -> None:
        from src.telemetry.lifecycle import stop_tasks_before_deadline

        self._closed = True
        await stop_tasks_before_deadline(
            (self._refresh, *self._probes.values()), deadline=deadline, cancel_first=True
        )


async def _probe(operation: Probe) -> HealthCheck:
    try:
        result = await operation()
        return HealthCheck(result is not False, "unavailable" if result is False else "ready")
    except Exception:
        return HealthCheck(False, "unavailable")


def _observe(task: asyncio.Task[object]) -> None:
    if not task.cancelled():
        task.exception()
