from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter

from prometheus_client import Counter, Gauge, Histogram

from src.metrics.prometheus import get_prometheus_registry

ROUTES = frozenset(
    {
        "chat_completions",
        "responses",
        "embeddings",
        "completions",
        "messages",
        "audio",
        "images",
        "rerank",
        "mcp",
        "metrics",
        "health",
        "other",
    }
)
PHASES = frozenset(
    {
        "response_total",
        "response_first_body",
        "application_total",
        "after_response",
        "authentication",
        "authentication_recheck",
        "capacity_admission",
        "prompt",
        "hooks_guardrails",
        "authorization",
        "final_parallel_admission",
        "budget",
        "rate_admission",
        "upstream_stream",
        "upstream_transform",
        "router_usage",
        "upstream_http",
        "other",
    }
)
OUTCOMES = frozenset(
    {
        "success",
        "error",
        "cancelled",
        "cancelled_or_error",
        "rate_limited",
        "client_error",
        "server_error",
        "timeout",
        "other",
    }
)
RESPONSE_KINDS = frozenset({"stream", "nonstream", "unknown"})

REQUEST_PHASE_BUCKETS = [
    0.0005,
    0.001,
    0.0025,
    0.005,
    0.01,
    0.025,
    0.05,
    0.075,
    0.1,
    0.25,
    0.5,
    0.75,
    1.0,
    2.5,
    5.0,
    7.5,
    10.0,
    15.0,
    30.0,
    60.0,
]

deltallm_request_phase_latency_metric = Histogram(
    "deltallm_request_phase_latency_seconds",
    "Gateway request latency by bounded request phase",
    ["route", "phase", "outcome", "response_kind"],
    buckets=REQUEST_PHASE_BUCKETS,
    registry=get_prometheus_registry(),
)
request_in_flight = Gauge(
    "deltallm_http_requests_in_flight",
    "Local HTTP requests until the final response frame or disconnect; includes unadmitted work",
    ["route"],
    registry=get_prometheus_registry(),
)
request_bytes = Counter(
    "deltallm_http_request_body_bytes_total",
    "HTTP request body bytes received; not a retained-memory measurement",
    ["route"],
    registry=get_prometheus_registry(),
)
response_bytes = Counter(
    "deltallm_http_response_body_bytes_total",
    "HTTP response body bytes successfully sent",
    ["route"],
    registry=get_prometheus_registry(),
)


def observe_request_phase(
    *,
    route: str,
    phase: str,
    outcome: str,
    response_kind: str,
    latency_seconds: float,
) -> None:
    deltallm_request_phase_latency_metric.labels(
        route=route if route in ROUTES else "other",
        phase=phase if phase in PHASES else "other",
        outcome=outcome if outcome in OUTCOMES else "other",
        response_kind=response_kind if response_kind in RESPONSE_KINDS else "unknown",
    ).observe(max(0.0, float(latency_seconds)))


def request_route(path: str) -> str:
    aliases = {
        "/chat/completions": "chat_completions",
        "/responses": "responses",
        "/embeddings": "embeddings",
        "/completions": "completions",
        "/messages": "messages",
        "/rerank": "rerank",
        "/mcp": "mcp",
    }
    normalized = path.removeprefix("/v1") if path.startswith("/v1/") else path
    if normalized in aliases:
        return aliases[normalized]
    if normalized.startswith("/audio/"):
        return "audio"
    if normalized.startswith("/images/"):
        return "images"
    if path == "/metrics":
        return "metrics"
    if path == "/health" or path.startswith("/health/"):
        return "health"
    return "other"


@contextmanager
def measure_request_phase(
    *, route: str, phase: str, response_kind: str = "unknown"
) -> Iterator[None]:
    started = perf_counter()
    outcome = "success"
    try:
        yield
    except BaseException as exc:
        outcome = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
        raise
    finally:
        observe_request_phase(
            route=route,
            phase=phase,
            outcome=outcome,
            response_kind=response_kind,
            latency_seconds=perf_counter() - started,
        )
