"""The shipped dashboard and alert rules only query metrics that DeltaLLM serves."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

import src.callbacks.integrations.otel_export  # noqa: F401 - registers span metrics
import src.metrics  # noqa: F401 - registers gateway metrics
from src.metrics.prometheus import PROMETHEUS_REGISTRY

pytestmark = pytest.mark.hermetic

MONITORING = Path(__file__).resolve().parents[1] / "deploy" / "kubernetes" / "monitoring"
_METRIC = re.compile(r"\bdeltallm_[a-z0-9_]+")
_SUFFIXES = ("_bucket", "_count", "_sum", "_total")


def _served_names() -> set[str]:
    names: set[str] = set()
    for metric in PROMETHEUS_REGISTRY.collect():
        names.add(metric.name)
        names.update(sample.name for sample in metric.samples)
    return names


def _base(name: str) -> str:
    for suffix in _SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _expressions() -> list[str]:
    rules = yaml.safe_load((MONITORING / "deltallm-alerts.yaml").read_text())
    dashboard = json.loads((MONITORING / "grafana-deltallm-overview.json").read_text())
    expressions = [rule["expr"] for group in rules["spec"]["groups"] for rule in group["rules"]]
    expressions += [
        target["expr"] for panel in dashboard["panels"] for target in panel.get("targets", [])
    ]
    return expressions


def test_monitoring_assets_reference_served_metrics():
    served = {_base(name) for name in _served_names()}
    referenced = {_base(name) for expr in _expressions() for name in _METRIC.findall(expr)}
    assert referenced
    assert referenced - served == set()
