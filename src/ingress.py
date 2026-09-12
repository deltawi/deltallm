"""Per-process admission and retained request-body ownership, before dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from src.concurrency import BoundedCapacityGate
from src.config_startup import startup_field_values
from src.metrics.admission import ingress_bytes


class IngressClass(StrEnum):
    INFERENCE = "inference"
    CONTROL = "control"
    HEALTH = "health"


INFERENCE_PATHS = frozenset(
    prefix + suffix
    for prefix in ("", "/v1")
    for suffix in (
        "/chat/completions",
        "/completions",
        "/responses",
        "/messages",
        "/embeddings",
        "/images/generations",
        "/audio/speech",
        "/audio/transcriptions",
        "/rerank",
    )
)
HEALTH_PATHS = frozenset({"/health", "/health/liveliness", "/health/readiness", "/metrics"})


def ingress_class(path: str, method: str) -> IngressClass:
    path = path.rstrip("/")
    if method in {"GET", "HEAD"} and path in HEALTH_PATHS:
        return IngressClass.HEALTH
    if method == "POST" and path in INFERENCE_PATHS:
        return IngressClass.INFERENCE
    return IngressClass.CONTROL


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
    control_max_active: int = 16
    control_max_buffered_bytes: int = 64 * 1024 * 1024

    @classmethod
    def from_settings(cls, general: Any, environment: Any) -> IngressLimits:
        return cls(**startup_field_values(cls(), general, environment, prefix="gateway_ingress_"))


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
        self.control = BoundedCapacityGate(concurrency=limits.control_max_active, max_waiters=0)
        self._buffered_bytes = {allocation: 0 for allocation in IngressClass}

    @property
    def buffered_bytes(self) -> int:
        return self._buffered_bytes[IngressClass.INFERENCE]

    def gate(self, allocation: IngressClass) -> BoundedCapacityGate:
        return {
            IngressClass.INFERENCE: self.requests,
            IngressClass.CONTROL: self.control,
            IngressClass.HEALTH: self.health,
        }[allocation]

    def reserve_bytes(self, size: int, allocation: IngressClass = IngressClass.INFERENCE) -> None:
        # This runtime is owned by one ASGI event loop. No await separates
        # checking, charging, and releasing its byte budget.
        limit = (
            self.limits.control_max_buffered_bytes
            if allocation == IngressClass.CONTROL
            else self.limits.max_buffered_bytes
        )
        if self._buffered_bytes[allocation] + size > limit:
            raise IngressBufferFull
        self._buffered_bytes[allocation] += size
        ingress_bytes.labels(allocation.value).inc(size)

    def release_bytes(self, size: int, allocation: IngressClass = IngressClass.INFERENCE) -> None:
        self._buffered_bytes[allocation] -= size
        ingress_bytes.labels(allocation.value).dec(size)


def initialize_ingress(app: Any, general: Any, environment: Any) -> None:
    app.state.ingress_runtime = IngressRuntime(IngressLimits.from_settings(general, environment))
