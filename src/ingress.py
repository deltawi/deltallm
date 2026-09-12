"""Per-process admission and retained request-body ownership, before dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.concurrency import BoundedCapacityGate


@dataclass(frozen=True)
class IngressLimits:
    enabled: bool = False
    max_active: int = 100
    max_waiters: int = 0
    queue_timeout_ms: int = 10
    max_body_bytes: int = 32 * 1024 * 1024
    max_buffered_bytes: int = 64 * 1024 * 1024
    body_timeout_seconds: float = 10.0
    health_max_active: int = 4

    @classmethod
    def from_settings(cls, general: Any, environment: Any) -> IngressLimits:
        values: dict[str, Any] = {}
        explicit = getattr(general, "model_fields_set", None)
        defaults = cls()
        for name in cls.__dataclass_fields__:
            field = f"gateway_ingress_{name}"
            values[name] = (
                getattr(general, field)
                if hasattr(general, field) and (explicit is None or field in explicit)
                else getattr(environment, field, getattr(defaults, name))
            )
        return cls(**values)


class IngressBodyLimit(RuntimeError):
    pass


class IngressBufferFull(RuntimeError):
    pass


class IngressRuntime:
    def __init__(self, limits: IngressLimits) -> None:
        self.limits = limits
        self.requests = BoundedCapacityGate(
            concurrency=limits.max_active, max_waiters=limits.max_waiters
        )
        self.health = BoundedCapacityGate(concurrency=limits.health_max_active, max_waiters=0)
        self.buffered_bytes = 0

    def reserve_bytes(self, size: int) -> None:
        # This runtime is owned by one ASGI event loop. No await separates
        # checking, charging, and releasing its byte budget.
        if self.buffered_bytes + size > self.limits.max_buffered_bytes:
            raise IngressBufferFull
        self.buffered_bytes += size

    def release_bytes(self, size: int) -> None:
        self.buffered_bytes -= size


def initialize_ingress(app: Any, general: Any, environment: Any) -> None:
    app.state.ingress_runtime = IngressRuntime(IngressLimits.from_settings(general, environment))
