from typing import Literal

from prometheus_client import Counter

from src.metrics.prometheus import get_prometheus_registry

_events = Counter(
    "deltallm_output_tpm_events_total",
    "Output TPM decisions with fixed result labels",
    ["result"],
    registry=get_prometheus_registry(),
)
OutputResult = Literal[
    "admitted",
    "denied",
    "unavailable",
    "accounted",
    "unknown_usage",
    "accounting_failed",
    "saturated",
]


def record_output_tpm(result: OutputResult) -> None:
    _events.labels(result=result).inc()
