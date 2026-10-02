from typing import Literal

from prometheus_client import Counter, Gauge

from src.metrics.prometheus import get_prometheus_registry

active_sessions = Gauge(
    "deltallm_realtime_active_sessions",
    "Realtime sockets owned by this process, including admission",
    registry=get_prometheus_registry(),
)
session_outcomes = Counter(
    "deltallm_realtime_sessions_total",
    "Realtime session outcomes",
    ["outcome"],
    registry=get_prometheus_registry(),
)
usage_receipts = Counter(
    "deltallm_realtime_receipts_total",
    "Durably retained Realtime usage receipts",
    ["state"],
    registry=get_prometheus_registry(),
)


def record_session(outcome: Literal["closed", "denied", "error", "cancelled"]) -> None:
    session_outcomes.labels(outcome=outcome).inc()


def record_receipt(*, pending: bool) -> None:
    usage_receipts.labels(state="pending" if pending else "accepted").inc()
