"""Fixed dependencies and chart overrides for the disposable lifecycle cluster."""

from pathlib import Path

import yaml

from tests.performance.lifecycle_batch import ITEM_LEASE_SECONDS
from tests.performance.lifecycle_cluster import LifecycleCluster, MASTER_KEY, SALT_KEY

CHART = Path("deploy/kubernetes/helm")


def dependency(name: str, image: str, port: int, **container: object) -> list[dict]:
    labels = {"fixture": name}
    return [
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": name},
            "spec": {"selector": labels, "ports": [{"port": port, "targetPort": port}]},
        },
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": name},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": labels},
                "template": {
                    "metadata": {"labels": labels},
                    "spec": {
                        "automountServiceAccountToken": False,
                        "containers": [
                            {
                                "name": name,
                                "image": image,
                                "imagePullPolicy": "IfNotPresent",
                                "resources": {
                                    "requests": {"cpu": "100m", "memory": "128Mi"},
                                    "limits": {"cpu": "2", "memory": "1Gi"},
                                },
                                "readinessProbe": {"tcpSocket": {"port": port}, "periodSeconds": 1},
                                **container,
                            }
                        ],
                    },
                },
            },
        },
    ]


def install_dependencies(cluster: LifecycleCluster, image: str) -> None:
    documents = dependency(
        "postgres",
        "postgres:15",
        5432,
        env=[
            {"name": "POSTGRES_PASSWORD", "value": "fixture-only"},
            {"name": "POSTGRES_DB", "value": "deltallm_concurrency"},
        ],
        args=["-c", "shared_preload_libraries=pg_stat_statements", "-c", "max_connections=300"],
    )
    documents += dependency("redis", "redis:7", 6379)
    documents += [
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "provider"},
            "data": {"mock.py": Path("tests/performance/gateway_concurrency_mock.py").read_text()},
        }
    ]
    provider = dependency(
        "provider",
        image,
        8000,
        command=[
            "python",
            "-m",
            "uvicorn",
            "mock:app",
            "--app-dir",
            "/fixture",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
            "--no-access-log",
        ],
        volumeMounts=[{"name": "fixture", "mountPath": "/fixture", "readOnly": True}],
    )
    provider[1]["spec"]["template"]["spec"]["volumes"] = [
        {"name": "fixture", "configMap": {"name": "provider"}}
    ]
    documents += provider
    documents += [
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": "deltallm-database"},
            "stringData": {
                "database-url": "postgresql://postgres:fixture-only@postgres:5432/deltallm_concurrency"
            },
        },
        # This single-node test PVC survives API/worker container and pod loss.
        # Production multi-node batch deployments use S3, as required by the chart.
        {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": "artifacts"},
            "spec": {
                "accessModes": ["ReadWriteOnce"],
                "resources": {"requests": {"storage": "1Gi"}},
            },
        },
    ]
    cluster.apply(documents)
    for name in ("postgres", "redis", "provider"):
        cluster.kubectl("rollout", "status", "deployment/" + name, "--timeout=180s")


def chart_values(cluster: LifecycleCluster, image: str) -> Path:
    repository, tag = image.rsplit(":", 1)
    config = yaml.safe_load(Path("tests/performance/gateway_concurrency_profile.yaml").read_text())
    general = config["general_settings"]
    config["router_settings"]["timeout"] = 350
    general.update(
        migration_mode="external",
        budget_enforcement_query_mode="combined",
        spend_operation_intents_enabled=True,
        model_deployment_bootstrap_from_config=False,
        # Keep the PR8 lifecycle qualification on its recorded ingress path.
        # PR9's separate capacity acceptance owns production ingress saturation.
        gateway_ingress_enabled=False,
        embeddings_batch_enabled=True,
        embeddings_batch_item_lease_seconds=ITEM_LEASE_SECONDS,
        embeddings_batch_storage_dir="/artifacts",
        embeddings_batch_stale_lease_sweeper_interval_seconds=1,
        embeddings_batch_stale_lease_sweeper_failure_interval_seconds=1,
    )
    config["model_list"][0]["deltallm_params"]["api_base"] = "http://provider:8000/v1"
    values = {
        "image": {"repository": repository, "tag": tag, "pullPolicy": "Never"},
        # This PR8 fixture keeps its deterministic config-only provider. PR9's
        # separate capacity fixture exercises the strict production/DB-only contract.
        "managedLifecycle": {"production": False},
        "dependencyCapacity": {"extended": {"enabled": False}},
        "replicaCount": 2,
        "autoscaling": {"enabled": False},
        "prometheus": {"serviceMonitor": {"enabled": False}},
        "secret": {"values": {"masterKey": MASTER_KEY, "saltKey": SALT_KEY}},
        "runtime": {"redis": {"url": "redis://redis:6379/0"}},
        "resources": {
            "requests": {"cpu": "100m", "memory": "256Mi"},
            "limits": {"cpu": "2", "memory": "2Gi"},
        },
        "batchWorker": {
            "enabled": True,
            "replicaCount": 1,
            "allowUnsafeLocalStorage": True,
            "resources": {
                "requests": {"cpu": "100m", "memory": "256Mi"},
                "limits": {"cpu": "2", "memory": "2Gi"},
            },
        },
        "extraVolumes": [
            {"name": "artifacts", "persistentVolumeClaim": {"claimName": "artifacts"}}
        ],
        "extraVolumeMounts": [{"name": "artifacts", "mountPath": "/artifacts"}],
        "config": config,
    }
    path = Path(cluster.directory.name) / "values.yaml"
    path.write_text(yaml.safe_dump(values))
    # This file contains fixed, disposable credentials, never operator inputs.
    (cluster.output / "fixture-values.yaml").write_text(path.read_text())
    return path


def release(cluster: LifecycleCluster, values: Path, *extra: str, check: bool = True):
    return cluster.helm(
        "upgrade",
        "--install",
        "gateway",
        str(CHART),
        "-f",
        str(CHART / "values-production.yaml"),
        "-f",
        str(values),
        "--wait",
        "--timeout",
        "240s",
        *extra,
        timeout=300,
        check=check,
    )
