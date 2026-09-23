"""Production-shaped allocations and a bounded reference edge for PR9 experiments."""

from pathlib import Path

import yaml

from scripts.prepare_capacity_adapter import MonitoringDependencies
from tests.performance.lifecycle_cluster import LifecycleCluster, NAMESPACE, MASTER_KEY, SALT_KEY
from tests.performance.lifecycle_fixtures import CHART, dependency, install_dependencies


def install_capacity_dependencies(cluster: LifecycleCluster, image: str) -> None:
    install_dependencies(cluster, image)
    # Actual fixture limits match declared budgets, including retiring processes.
    cluster.kubectl(
        "patch",
        "deployment/postgres",
        "--type=strategic",
        "-p",
        '{"spec":{"template":{"spec":{"containers":[{"name":"postgres","args":["-c","shared_preload_libraries=pg_stat_statements","-c","max_connections=1000"]}]}}}}',
    )
    cluster.kubectl(
        "patch",
        "deployment/redis",
        "--type=strategic",
        "-p",
        '{"spec":{"template":{"spec":{"containers":[{"name":"redis","args":["--maxclients","5000","--maxmemory","128mb","--maxmemory-policy","noeviction"]}]}}}}',
    )
    for name in ("postgres", "redis"):
        cluster.kubectl("rollout", "status", "deployment/" + name, "--timeout=180s")


def capacity_values(cluster: LifecycleCluster, image: str) -> Path:
    repository, tag = image.rsplit(":", 1)
    profile = yaml.safe_load(Path("tests/performance/gateway_concurrency_profile.yaml").read_text())
    general = profile["general_settings"]
    general.update(
        model_deployment_source="db_only",
        model_deployment_bootstrap_from_config=False,
        migration_mode="external",
        budget_enforcement_query_mode="combined",
        spend_operation_intents_enabled=True,
        gateway_ingress_enabled=True,
        embeddings_batch_enabled=False,
        db_pool_timeout=2,
    )
    profile["router_settings"]["timeout"] = 350
    profile["model_list"][0]["deltallm_params"]["api_base"] = "http://provider:8000/v1"
    values = {
        "image": {"repository": repository, "tag": tag, "pullPolicy": "Never"},
        "prometheus": {"serviceMonitor": {"enabled": False}},
        "secret": {"values": {"masterKey": MASTER_KEY, "saltKey": SALT_KEY}},
        "runtime": {"redis": {"url": "redis://redis:6379/0"}},
        "dependencyCapacity": {"postgresqlMaxConnections": 1000},
        "config": profile,
    }
    path = Path(cluster.directory.name) / "capacity-values.yaml"
    path.write_text(yaml.safe_dump(values))
    (cluster.output / "fixture-values.yaml").write_text(path.read_text())
    return path


def capacity_release(cluster: LifecycleCluster, values: Path, *extra: str) -> None:
    cluster.helm(
        "upgrade",
        "--install",
        "gateway",
        str(CHART),
        "-f",
        str(CHART / "values-production.yaml"),
        "-f",
        str(CHART / "values-capacity-experiment.yaml"),
        "-f",
        str(CHART / "values-capacity-fixture.yaml"),
        "-f",
        str(values),
        *extra,
        "--wait",
        "--timeout=600s",
        timeout=660,
    )


def install_edge(cluster: LifecycleCluster) -> None:
    """At most 24 connections per API IP per edge process; no backend reuse."""
    config = f"""global
  maxconn 512
  nbthread 1
defaults
  mode http
  timeout connect 1s
  timeout client 360s
  timeout server 360s
  timeout http-request 5s
  timeout queue 200ms
  option http-server-close
  errorfile 503 /usr/local/etc/haproxy/503.http
resolvers kubernetes
  parse-resolv-conf
  accepted_payload_size 8192
  hold valid 1s
  hold obsolete 1s
frontend clients
  bind :8080
  http-request deny deny_status 404 if {{ path_beg /metrics /health/deployments /health/fallback-events }}
  default_backend api
backend api
  balance roundrobin
  option httpchk GET /health/readiness
  http-check expect status 200
  server-template api 16 _http._tcp.capacity-api.{NAMESPACE}.svc.cluster.local resolvers kubernetes init-addr none check inter 1s maxconn 24 maxqueue 4
"""
    body = '{"error":{"message":"Gateway temporarily unavailable","type":"service_unavailable","code":"edge_unavailable"}}'
    failure = (
        "HTTP/1.1 503 Service Unavailable\r\n"
        "Content-Type: application/json\r\n"
        "Cache-Control: no-store\r\n"
        f"Content-Length: {len(body.encode()) + 1}\r\n"
        "Connection: close\r\n\r\n" + body + "\n"
    )
    docs = [
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "capacity-api"},
            "spec": {
                "clusterIP": "None",
                "selector": {
                    "app.kubernetes.io/instance": "gateway",
                    "app.kubernetes.io/component": "api",
                },
                "ports": [{"name": "http", "port": 4000, "targetPort": "http"}],
            },
        },
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "capacity-edge"},
            "data": {"haproxy.cfg": config, "503.http": failure},
        },
    ]
    edge = dependency(
        "capacity-edge",
        MonitoringDependencies.read().edge_image,
        8080,
        volumeMounts=[{"name": "config", "mountPath": "/usr/local/etc/haproxy", "readOnly": True}],
    )
    edge[1]["spec"]["strategy"] = {"type": "Recreate"}
    edge[1]["spec"]["template"]["spec"]["volumes"] = [
        {"name": "config", "configMap": {"name": "capacity-edge"}}
    ]
    cluster.apply(docs + edge)
    (cluster.output / "edge.cfg").write_text(config)
    cluster.kubectl("rollout", "status", "deployment/capacity-edge", "--timeout=180s")
