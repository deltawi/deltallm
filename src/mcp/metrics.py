from __future__ import annotations

from typing import Any

from prometheus_client import Counter, Gauge, Histogram

from src.metrics.prometheus import get_prometheus_registry, sanitize_label

_LATENCY_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
_KNOWN_HEALTH_STATUSES = ("healthy", "unhealthy", "unknown")

deltallm_mcp_capability_refresh_metric = Counter(
    "deltallm_mcp_capability_refresh_total",
    "Total MCP capability refresh attempts",
    ["server_key", "success"],
    registry=get_prometheus_registry(),
)

deltallm_mcp_capability_refresh_latency_metric = Histogram(
    "deltallm_mcp_capability_refresh_latency_seconds",
    "MCP capability refresh latency",
    ["server_key", "success"],
    buckets=_LATENCY_BUCKETS,
    registry=get_prometheus_registry(),
)

deltallm_mcp_health_check_metric = Counter(
    "deltallm_mcp_health_check_total",
    "Total MCP health checks",
    ["server_key", "status"],
    registry=get_prometheus_registry(),
)

deltallm_mcp_health_check_latency_metric = Histogram(
    "deltallm_mcp_health_check_latency_seconds",
    "MCP health check latency",
    ["server_key", "status"],
    buckets=_LATENCY_BUCKETS,
    registry=get_prometheus_registry(),
)

deltallm_mcp_server_health_status_metric = Gauge(
    "deltallm_mcp_server_health_status",
    "Current MCP server health status as one-hot gauge",
    ["server_key", "status"],
    registry=get_prometheus_registry(),
)

deltallm_mcp_tools_list_metric = Counter(
    "deltallm_mcp_tools_list_total",
    "Total MCP tools/list requests",
    ["success"],
    registry=get_prometheus_registry(),
)

deltallm_mcp_tools_list_latency_metric = Histogram(
    "deltallm_mcp_tools_list_latency_seconds",
    "MCP tools/list latency",
    ["success"],
    buckets=_LATENCY_BUCKETS,
    registry=get_prometheus_registry(),
)

deltallm_mcp_tool_call_metric = Counter(
    "deltallm_mcp_tool_call_total",
    "Total MCP tool call attempts",
    ["server_key", "tool_name", "success"],
    registry=get_prometheus_registry(),
)

deltallm_mcp_tool_call_latency_metric = Histogram(
    "deltallm_mcp_tool_call_latency_seconds",
    "MCP tool call latency",
    ["server_key", "tool_name", "success"],
    buckets=_LATENCY_BUCKETS,
    registry=get_prometheus_registry(),
)

deltallm_mcp_approval_request_metric = Counter(
    "deltallm_mcp_approval_request_total",
    "Total MCP approval requests created or reused",
    ["server_key", "tool_name", "created"],
    registry=get_prometheus_registry(),
)

deltallm_mcp_approval_decision_metric = Counter(
    "deltallm_mcp_approval_decision_total",
    "Total MCP approval decisions",
    ["status"],
    registry=get_prometheus_registry(),
)


def record_mcp_capability_refresh(*, server_key: str, success: bool, latency_ms: int | None = None) -> None:
    labels = {
        "server_key": sanitize_label(server_key),
        "success": "true" if success else "false",
    }
    deltallm_mcp_capability_refresh_metric.labels(**labels).inc()
    if latency_ms is not None:
        deltallm_mcp_capability_refresh_latency_metric.labels(**labels).observe(max(0.0, float(latency_ms) / 1000.0))


def record_mcp_health_check(*, server_key: str, status: str, latency_ms: int | None = None) -> None:
    server_label = sanitize_label(server_key)
    status_label = sanitize_label(status, "unknown")
    deltallm_mcp_health_check_metric.labels(server_key=server_label, status=status_label).inc()
    if latency_ms is not None:
        deltallm_mcp_health_check_latency_metric.labels(server_key=server_label, status=status_label).observe(
            max(0.0, float(latency_ms) / 1000.0)
        )
    for known_status in _KNOWN_HEALTH_STATUSES:
        deltallm_mcp_server_health_status_metric.labels(server_key=server_label, status=known_status).set(
            1.0 if known_status == status_label else 0.0
        )


def record_mcp_tools_list(*, server_count: int, tool_count: int, success: bool, latency_ms: int | None = None) -> None:
    del server_count, tool_count
    labels = {"success": "true" if success else "false"}
    deltallm_mcp_tools_list_metric.labels(**labels).inc()
    if latency_ms is not None:
        deltallm_mcp_tools_list_latency_metric.labels(**labels).observe(max(0.0, float(latency_ms) / 1000.0))


def record_mcp_tool_call(
    *,
    server_key: str,
    tool_name: str,
    success: bool,
    latency_ms: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    del metadata
    labels = {
        "server_key": sanitize_label(server_key),
        "tool_name": sanitize_label(tool_name),
        "success": "true" if success else "false",
    }
    deltallm_mcp_tool_call_metric.labels(**labels).inc()
    if latency_ms is not None:
        deltallm_mcp_tool_call_latency_metric.labels(**labels).observe(max(0.0, float(latency_ms) / 1000.0))


def record_mcp_approval_request(*, server_key: str, tool_name: str, created: bool) -> None:
    deltallm_mcp_approval_request_metric.labels(
        server_key=sanitize_label(server_key),
        tool_name=sanitize_label(tool_name),
        created="true" if created else "false",
    ).inc()


def record_mcp_approval_decision(*, status: str) -> None:
    deltallm_mcp_approval_decision_metric.labels(status=sanitize_label(status)).inc()
