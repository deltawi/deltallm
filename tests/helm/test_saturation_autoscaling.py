import pytest
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from scripts.prepare_capacity_adapter import MONITORING, serving_tls

from tests.helm.test_batch_worker_split import HELM_CHART_DIR, _render, _render_error

pytestmark = pytest.mark.helm
PRODUCTION = ("-f", str(HELM_CHART_DIR / "values-production.yaml"))


def test_production_hpa_uses_pod_admission_cpu_and_stabilization():
    hpa = next(doc for doc in _render(*PRODUCTION) if doc["kind"] == "HorizontalPodAutoscaler")
    spec = hpa["spec"]
    assert spec["minReplicas"] == 3 and spec["maxReplicas"] == 12
    assert spec["behavior"]["scaleDown"]["stabilizationWindowSeconds"] == 300
    assert spec["metrics"][0] == {
        "type": "Pods",
        "pods": {
            "metric": {"name": "deltallm_admitted_inflight"},
            "target": {"type": "AverageValue", "averageValue": "70"},
        },
    }
    assert spec["metrics"][1]["resource"]["name"] == "cpu"


@pytest.mark.parametrize(
    "setting,message",
    [
        ("autoscaling.admittedInflight.enabled=false", "requires admitted"),
        ("autoscaling.admittedInflight.targetAverageValue=100", "below the per-process"),
        ("autoscaling.minReplicas=13", "minReplicas"),
        ("autoscaling.scaleDownStabilizationSeconds=89", "termination grace"),
        ("prometheus.customMetrics.enabled=false", "adapter integration"),
        ("config.general_settings.gateway_ingress_enabled=false", "ingress"),
        ("config.general_settings.gateway_preflight_capacity_enabled=false", "preflight"),
        ("config.general_settings.redis_degraded_mode=fail_open", "fail_closed"),
        ("config.general_settings.audit_ingestion_mode=legacy", "durable audit"),
        ("config.general_settings.audit_enabled=false", "audit_enabled"),
        ("config.general_settings.model_deployment_source=config_only", "DB-only"),
        (
            "config.general_settings.spend_operation_intents_enabled=false",
            "spend_operation_intents",
        ),
        ("dependencyCapacity.extended.enabled=false", "extended dependency"),
    ],
)
def test_production_rejects_missing_admission_adapter_or_durability(setting, message):
    assert message in _render_error(*PRODUCTION, "--set", setting)


def test_cross_namespace_monitor_targets_application_namespace_and_api_only():
    docs = _render(
        *PRODUCTION,
        "--set",
        "prometheus.serviceMonitor.namespace=monitoring",
        "--set",
        "batchWorker.enabled=true",
    )
    monitor = next(
        doc
        for doc in docs
        if doc["kind"] == "ServiceMonitor" and doc["metadata"]["name"] == "deltallm"
    )
    assert monitor["spec"]["namespaceSelector"]["matchNames"] == ["default"]
    selector = monitor["spec"]["selector"]["matchLabels"]
    services = [doc for doc in docs if doc["kind"] == "Service"]
    selected = [
        doc
        for doc in services
        if all(doc["metadata"]["labels"].get(k) == v for k, v in selector.items())
    ]
    assert [doc["metadata"]["name"] for doc in selected] == ["deltallm"]


def test_adapter_tls_and_fresh_per_pod_metric_rule():
    tls = serving_tls("monitoring", "deltallm-metrics")
    ca = x509.load_pem_x509_certificate(tls["ca"].encode())
    cert = x509.load_pem_x509_certificate(tls["certificate"].encode())
    key = serialization.load_pem_private_key(tls["key"].encode(), password=None)
    assert cert.issuer == ca.subject
    assert cert.public_key().public_numbers() == key.public_key().public_numbers()
    assert "deltallm-metrics.monitoring.svc" in cert.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value.get_values_for_type(x509.DNSName)
    values = yaml.safe_load((MONITORING / "prometheus-adapter-values.yaml").read_text())
    rule = values["rules"]["custom"][0]
    assert rule["resources"]["overrides"] == {
        "namespace": {"resource": "namespace"},
        "pod": {"resource": "pod"},
    }
    assert 'allocation="inference"' in rule["seriesQuery"]
    assert 'deltallm_role="api"' in rule["seriesQuery"]
    assert "max by (<<.GroupBy>>)" in rule["metricsQuery"]
    assert "timestamp(" in rule["metricsQuery"]
    assert "or vector(0)" not in rule["metricsQuery"]
