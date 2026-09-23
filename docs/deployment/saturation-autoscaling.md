---
title: Saturation autoscaling and capacity preflight
description: Check downstream budgets and operate the admitted in-flight Kubernetes HPA.
status: experimental
audience: operators
---

# Saturation autoscaling and capacity preflight

The production profile admits at most 100 active inference lifetimes per API
process with zero ingress waiters. The shared preflight budget starts at 300
active **preflight operations** deployment-wide and 100 per organization. Its
leases are released before provider execution or a long response. These are separate
ceilings: an accepted ingress lifetime can later be refused by authentication,
preflight, a database allocation, or a provider. The HPA samples the number of
*admitted ingress lifetimes*, including long streams. It is not a successful
provider-stream or requests-per-second counter.

With mean admitted duration `D`, Little's Law gives approximate mean occupancy
`L ≈ admitted RPS × D` for stable traffic. Five hundred live requests lasting
10 seconds imply about 50 RPS; lasting 0.4 seconds imply about 1,250 RPS.
Preflight limits, routing mix, provider duration, and error rates can make actual
throughput much lower. The 2/3/4-pod experiment below is functional evidence;
no supported pod-to-concurrency or N−1 number has been qualified before PR10.

## Capacity declaration and preflight

Render the exact overlays planned for the release, using an immutable image and
actual operator-supplied `providerDomains`. The chart refuses production values
without a domain declaration. The local-mock fixture file in this repository is
only for CI and a disposable cluster. Domain `modelIds` must cover the active DB
catalog. `rpm`, `tpm` and `concurrency` are provider **allocations**; set the
per-process attempt, token and request-rate envelope, reserve other clients, and
include failover domains. The report calls the check `workload-envelope` because
generic pre-call rate checks are not atomic provider quota reservations.

```yaml
# operator-capacity.yaml: replace every number and ID with an approved allocation
dependencyCapacity:
  postgresqlMaxConnections: 2000
  redisMaxClients: 5000
  extended:
    postgresqlMigrationConnections: 2
    postgresqlOtherConnections: 0
    redisOtherClients: 0
    proxyPoolsPerClient: 0
    auxiliaryHttpConnectionsPerProcess: 2048
    fileDescriptors:
      processLimit: 8192
      engineLimit: 1024
      inboundConnectionsPerProcess: 256
      otherPerProcess: 128
      headroomPerProcess: 256
      maxPodsPerNode: 44
      nodeLimit: 1048576
      nodeReserved: 8192
    providerDomains:
      primary:
        modelIds: [your-deployment-id]
        rpm: 1000000
        tpm: 1000000000
        concurrency: 100000
        apiRpmPerProcess: 600
        workerRpmPerProcess: 60
        tokensPerAttempt: 32
        attemptsPerRequest: 3
        reservedRpm: 100
        reservedTpm: 1000
        reservedConcurrency: 10
```

These example quotas deliberately use round numbers and must be replaced. The
chart counts HPA maxima, rollout surge rounded up, two retiring generations by
default, every enabled process and the four database pools. It adds migration,
other-client and operating reserves. The FD node allowance must cover **all**
possible peak pods unless a separately enforced placement limit exists; preferred
anti-affinity and spreading do not cap placement. Additional batch-worker HPA
capacity can therefore require a higher `maxPodsPerNode` and real node limit.

```bash
helm template gateway deploy/kubernetes/helm -n deltallm \
  -f deploy/kubernetes/helm/values-production.yaml \
  -f operator-capacity.yaml --set image.tag=YOUR_IMMUTABLE_TAG \
  > /tmp/deltallm-capacity-render.yaml
kubectl -n deltallm get configmap gateway-deltallm-dependency-capacity \
  -o go-template='{{index .data "report.json"}}'
kubectl -n deltallm exec deployment/gateway-deltallm -- \
  python -m src.deployment_capacity_observation --role api
```

The first command validates the proposed release; the latter two inspect a
running release. Review `report.json` against PostgreSQL `max_connections`,
Redis `maxclients` on each physical server, proxy mounts, provider agreements,
process `RLIMIT_NOFILE`, node `/proc/sys/fs/file-max`, node resources and the
edge connection budget. The startup contract checks known owned pools, required
production control policy and process FD limits before readiness. Auxiliary HTTP
connections are a reserved operator allowance for optional callback/SDK and
short-lived auth/guardrail clients; review the enabled integrations separately.
Redis database numbers do not create separate `maxclients` domains. A declared
quota is not evidence that the physical server or provider granted it.

The edge must bound **accepted idle and slow sockets**, not just requests that
reach the ASGI gate. The disposable experiment uses one HAProxy process with
`maxconn 512`, `maxconn 24` and `maxqueue 4` per API endpoint, a 200 ms queue
timeout, a 5-second HTTP request timeout and no backend keepalive. This leaves
space in each pod's 256 inbound FD allowance for monitoring and probes during
that fixture's single edge rollout. A production ingress controller must provide
an equivalent checked per-pod limit and restrict direct pod/Service access to
the intended edge, monitoring and health paths. Protect `/metrics` from public
traffic. Confirm that the chosen CNI enforces those access rules; the disposable
kind network is not a production network-policy proof.

## Monitoring and HPA

The application exposes `deltallm_ingress_active{allocation="inference"}` from
the lifetime gate. A ServiceMonitor can discover the API in its namespace or a
separate monitoring namespace. The pinned Prometheus Adapter values under
`deploy/kubernetes/monitoring/` map fresh per-pod series to
`deltallm_admitted_inflight` through the Kubernetes custom metrics API. The
adapter has a private serving certificate; the preparation script verifies the
chart SHA256 and writes its private values file with mode 0600. Supply a reachable
Prometheus endpoint and appropriate authenticated/network-restricted access in
your monitoring environment. Rotate the generated serving certificate before
its 90-day expiry. Reuse the cluster's existing custom metrics adapter if one
already owns `v1beta1.custom.metrics.k8s.io`; do not install a second APIService.
A working resource metrics API is also required for CPU and memory HPA signals.

```bash
uv run python scripts/prepare_capacity_adapter.py \
  --output /tmp/deltallm-adapter --namespace monitoring \
  --release deltallm-metrics \
  --prometheus-url http://prometheus.monitoring.svc
helm upgrade --install deltallm-metrics \
  /tmp/deltallm-adapter/prometheus-adapter-5.3.0.tgz \
  -n monitoring --create-namespace \
  -f /tmp/deltallm-adapter/adapter-private-values.yaml
kubectl get apiservice v1beta1.custom.metrics.k8s.io
kubectl get --raw '/apis/custom.metrics.k8s.io/v1beta1/namespaces/deltallm/pods/*/deltallm_admitted_inflight'
kubectl -n deltallm describe hpa gateway-deltallm
```

Check that every ready API pod has a zero sample at idle and that a held stream
raises only its own pod's value. The rule deduplicates multiple scrapes per pod
and excludes worker, health and control allocations. Missing or older-than-45s
samples disappear rather than becoming zero. When a metric fails, Kubernetes
may still recommend scale-up from another valid metric, but does not safely
downscale from the remaining metrics while the required one is missing. Alert
on APIService unavailability, missing pod samples, HPA `ScalingActive=False`,
high admitted occupancy, ingress/preflight rejections, DB/Redis allocation
timeouts, durable backlog age, pod FD use and CPU throttling.

The initial production HPA target is 70 admitted lifetimes per pod against the
100-active gate, plus CPU 65% and memory 75%. It has a 300-second downscale
stabilization window and a minimum of three warm pods. Change the HPA maximum
only after updating the downstream/FD/provider allocations and checking
remaining dependency headroom. A slow provider or SQL bottleneck can saturate
all pods; adding replicas then increases demand on the bottleneck. Investigate
the downstream limits before raising the maximum.

## Disposable 2/3/4-pod check and rollout

The acceptance runner owns a two-node kind cluster and kubeconfig, builds a committed
PR8 comparison image, uses real PostgreSQL/Redis, a fixed one-token provider,
Prometheus, the adapter, metrics-server and a bounded HAProxy edge. It compares
identical fixed offered load across 2/3/4 pods, then tests a 2–4 HPA, missing
metrics, recovery and pod loss. The experiment uses 1 process, CPU request/limit
1/2 cores, memory request/limit 2/4 GiB, DB pools 20/8/5/5 and shared preflight
100/50. Its **10-lifetime** HPA target produces a functional scaling signal
from 30–50 held streams without requiring hundreds of local test connections.
Preflight limits apply only during the preflight phase. Neither target is an
SLO. The runner retains bounded raw samples in its output directory.
The disposable worker node gives Kubernetes enough schedulable CPU for four
1-core API requests plus monitoring and dependency pods on a standard public
GitHub runner. The nodes share the runner's physical CPU; this functional
experiment does not measure production throughput or per-node isolation.

```bash
uv sync --frozen --extra dev
uv run prisma generate --schema=./prisma/schema.prisma
docker build --build-arg INSTALL_PRESIDIO=false -t deltallm-capacity:candidate .
uv run python scripts/build_lifecycle_baseline.py \
  --ref 9a3f2cfc9c13f4eba332b3e331e7003acba20b03 \
  --image deltallm-capacity:baseline \
  --manifest /tmp/capacity-baseline.json
uv run python -m tests.performance.run_capacity_acceptance \
  --image deltallm-capacity:candidate \
  --baseline-image deltallm-capacity:baseline \
  --baseline-manifest /tmp/capacity-baseline.json \
  --kind /path/to/pinned/kind-v0.31.0 \
  --output /tmp/capacity-evidence
```

Before an operator rollout, verify the exact effective ConfigMap/report, required
Redis fail-closed behavior, durable audit/spend workers, model catalog and
monitoring samples. Start with a fixed warm count that fits physical headroom;
enable HPA after metrics are healthy. Serialize scale and release operations
within the declared surge/retiring budget. Abort on missing samples or workers,
growing durable backlog, sustained rejections at the target workload, process
FD/pool exhaustion or terminating pods beyond the declared allowance. Restore
the last verified fixed replica profile and retain fail-closed admission and
durable accounting while diagnosing. Follow [managed lifecycle](process-lifecycle.md)
for migration and drain order.
