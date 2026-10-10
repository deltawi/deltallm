"""Real Prometheus/adapter/resource metrics in the owned capacity test cluster."""

from pathlib import Path
import json

import yaml

from scripts.prepare_capacity_adapter import MonitoringDependencies, prepare_adapter
from tests.performance.lifecycle_cluster import LifecycleCluster, NAMESPACE
from tests.performance.lifecycle_fixtures import dependency


def install_monitoring(cluster: LifecycleCluster) -> None:
    pins = MonitoringDependencies.read()
    account = {"name": "capacity-prometheus"}
    scrape = {
        "global": {"scrape_interval": "5s", "scrape_timeout": "3s"},
        "scrape_configs": [
            {
                "job_name": "deltallm-api",
                "kubernetes_sd_configs": [{"role": "pod", "namespaces": {"names": [NAMESPACE]}}],
                "relabel_configs": [
                    {
                        "source_labels": ["__meta_kubernetes_pod_label_app_kubernetes_io_instance"],
                        "regex": "gateway",
                        "action": "keep",
                    },
                    {
                        "source_labels": [
                            "__meta_kubernetes_pod_label_app_kubernetes_io_component"
                        ],
                        "regex": "api",
                        "action": "keep",
                    },
                    {
                        "source_labels": [
                            "__meta_kubernetes_pod_annotation_capacity_test_drop_metrics"
                        ],
                        "regex": "true",
                        "action": "drop",
                    },
                    {
                        "source_labels": ["__meta_kubernetes_pod_container_port_name"],
                        "regex": "http",
                        "action": "keep",
                    },
                    {"source_labels": ["__meta_kubernetes_namespace"], "target_label": "namespace"},
                    {"source_labels": ["__meta_kubernetes_pod_name"], "target_label": "pod"},
                    {"target_label": "deltallm_role", "replacement": "api"},
                ],
            }
        ],
    }
    docs = [
        {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": account},
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "Role",
            "metadata": account,
            "rules": [
                {"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list", "watch"]}
            ],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "RoleBinding",
            "metadata": account,
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "Role",
                "name": "capacity-prometheus",
            },
            "subjects": [
                {"kind": "ServiceAccount", "name": "capacity-prometheus", "namespace": NAMESPACE}
            ],
        },
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "capacity-prometheus"},
            "data": {"prometheus.yml": yaml.safe_dump(scrape)},
        },
    ]
    prometheus = dependency(
        "capacity-prometheus",
        pins.prometheus_image,
        9090,
        args=[
            "--config.file=/etc/prometheus/prometheus.yml",
            "--storage.tsdb.path=/prometheus",
            "--storage.tsdb.retention.time=1h",
            "--storage.tsdb.retention.size=128MB",
        ],
        volumeMounts=[
            {"name": "config", "mountPath": "/etc/prometheus", "readOnly": True},
            {"name": "data", "mountPath": "/prometheus"},
        ],
    )
    spec = prometheus[1]["spec"]["template"]["spec"]
    spec.update(
        serviceAccountName="capacity-prometheus",
        automountServiceAccountToken=True,
        volumes=[
            {"name": "config", "configMap": {"name": "capacity-prometheus"}},
            {"name": "data", "emptyDir": {"sizeLimit": "256Mi"}},
        ],
    )
    spec["securityContext"] = {"runAsUser": 65534, "runAsNonRoot": True, "fsGroup": 65534}
    cluster.apply(docs + prometheus)
    install_resource_metrics(cluster, pins.metrics_server_image)
    existing = cluster.kubectl(
        "get", "apiservice", "v1beta1.custom.metrics.k8s.io", "--ignore-not-found", "-o", "name"
    ).stdout.strip()
    if existing:
        raise RuntimeError("Owned fixture unexpectedly contains a custom metrics APIService")
    files = prepare_adapter(
        Path(cluster.directory.name) / "adapter",
        namespace=NAMESPACE,
        release="capacity-adapter",
        prometheus_url=f"http://capacity-prometheus.{NAMESPACE}.svc",
    )
    cluster.helm(
        "upgrade",
        "--install",
        "capacity-adapter",
        str(files.chart),
        "-f",
        str(files.values),
        "--set",
        "replicas=1,podDisruptionBudget.enabled=false",
        "--wait",
        "--timeout=180s",
    )
    cluster.kubectl(
        "wait",
        "apiservice/v1beta1.custom.metrics.k8s.io",
        "--for=condition=Available",
        "--timeout=120s",
    )
    cluster.event("monitoring_ready", adapter_chart=pins.adapter_chart.version)


def install_resource_metrics(cluster: LifecycleCluster, image: str) -> None:
    """The kubelet TLS exception is restricted to this disposable kind fixture."""
    namespace = NAMESPACE
    name = "capacity-resource-metrics"
    docs = [
        {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"name": name}},
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRole",
            "metadata": {"name": name},
            "rules": [
                {"apiGroups": [""], "resources": ["nodes/metrics"], "verbs": ["get"]},
                {
                    "apiGroups": [""],
                    "resources": ["pods", "nodes"],
                    "verbs": ["get", "list", "watch"],
                },
            ],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRoleBinding",
            "metadata": {"name": name},
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "ClusterRole",
                "name": name,
            },
            "subjects": [{"kind": "ServiceAccount", "name": name, "namespace": namespace}],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRoleBinding",
            "metadata": {"name": name + "-auth"},
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "ClusterRole",
                "name": "system:auth-delegator",
            },
            "subjects": [{"kind": "ServiceAccount", "name": name, "namespace": namespace}],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "RoleBinding",
            "metadata": {"name": name, "namespace": "kube-system"},
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "Role",
                "name": "extension-apiserver-authentication-reader",
            },
            "subjects": [{"kind": "ServiceAccount", "name": name, "namespace": namespace}],
        },
        {
            "apiVersion": "apiregistration.k8s.io/v1",
            "kind": "APIService",
            "metadata": {"name": "v1beta1.metrics.k8s.io"},
            "spec": {
                "group": "metrics.k8s.io",
                "version": "v1beta1",
                "groupPriorityMinimum": 100,
                "versionPriority": 100,
                "insecureSkipTLSVerify": True,
                "service": {"name": name, "namespace": namespace, "port": 10250},
            },
        },
    ]
    metrics = dependency(
        name,
        image,
        10250,
        args=[
            "--cert-dir=/tmp",
            "--secure-port=10250",
            "--kubelet-preferred-address-types=InternalIP",
            "--kubelet-use-node-status-port",
            "--metric-resolution=15s",
            "--kubelet-insecure-tls",
        ],
    )
    metrics[1]["spec"]["template"]["spec"].update(
        serviceAccountName=name, automountServiceAccountToken=True
    )
    auth_reader = next(doc for doc in docs if doc["kind"] == "RoleBinding")
    docs.remove(auth_reader)
    reader_path = Path(cluster.directory.name) / "metrics-auth-reader.yaml"
    reader_path.write_text(yaml.safe_dump(auth_reader))
    cluster.kubectl("-n", "kube-system", "apply", "-f", str(reader_path))
    cluster.apply(docs + metrics)
    cluster.kubectl("rollout", "status", "deployment/" + name, "--timeout=180s")


def custom_metrics(cluster: LifecycleCluster) -> dict:
    path = f"/apis/custom.metrics.k8s.io/v1beta1/namespaces/{NAMESPACE}/pods/*/deltallm_admitted_inflight"
    return json.loads(cluster.kubectl("get", "--raw", path).stdout)
