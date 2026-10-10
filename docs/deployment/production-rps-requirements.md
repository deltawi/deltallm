# Production requirements for RPS targets

Size the deployment for its request rate, request duration, token volume and
failure policy. Requests per second (RPS) alone do not define a CPU, memory or
replica requirement. Use the measured configuration below as a test reference,
then qualify the exact production image and workload before accepting traffic.

## What the measured targets mean

The [Issue 320 report](../project/issue-320-rps-report.md) records the full results,
source identities and limits. The workload uses immediate, one-token,
non-streaming synthetic replies, with response-cache bypass and required native
accounting. It does not represent long model replies, large bodies or streaming.

| Target | Current evidence | Required before a production capacity claim |
| --- | --- | --- |
| 50 RPS | Earlier image passed a 60-second stage | Pass ten-minute qualification on the release image and test the real traffic mix |
| 100 RPS | Same earlier image passed a 60-second stage | Same checks, with measured provider and dependency headroom |
| 200 RPS | Same earlier image passed a 60-second stage | Same checks, including peak concurrency and worker catch-up |
| 500 RPS | Latest provider-pool image passed a 30-second confirmation | Pass sustained stability and all four normal stages on one image |
| 1,000 RPS | Latest 30-second diagnostic failed | Resolve or isolate CPU saturation, then repeat the diagnostic and sustained tests |

These results do not form one unchanged-image qualification. There is no verified
production hardware minimum for each tier. Main revision `14cf7871` is integrated.
The draft upgrade must pass the combined auth, output-token, migration and release
checks. Earlier RPS results do not qualify the merged image.

## Measured reference topology

The latest test used a dedicated Linux arm64 VM with six CPUs and 12 GiB RAM.
Its two-node kind cluster shared that VM; it did not have two separate hardware
failure domains. The generator, synthetic provider, monitoring, PostgreSQL and
Redis also used the VM.

| Role | Replicas and processes | CPU request / limit per pod | Memory request / limit per pod |
| --- | --- | --- | --- |
| API | Four pods, one process per pod | 1 / 2 cores | 256 MiB / 1 GiB |
| Accounting request | Two pods, one process per pod | 0.1 / 2 cores | 256 MiB / 1 GiB |
| Accounting projection | One pod, one process | 0.1 / 2 cores | 256 MiB / 1 GiB |
| PostgreSQL and Redis | One pod each, test services only | 0.1 / 2 cores each | 128 MiB / 1 GiB each |

This is a reproduction configuration, not a production installation recipe.
Pod limits do not add physical CPU capacity: the sum of these limits exceeds the
shared VM's six CPUs. At 1,000 RPS the VM was 99.11% busy. Increasing only a pool,
pod limit or HPA maximum does not establish that this bottleneck is removed.

For production, give API processes usable CPU and memory based on their measured
peak load. Keep load generation off the serving nodes. Give PostgreSQL and Redis
reserved capacity that unrelated workloads cannot consume. Use separate failure
domains where the availability target requires them. Reserve node capacity for
workers, monitoring, rolling updates and loss of a node.

CPU limits can throttle a container even when the node has spare CPU. Monitor
throttling and node saturation separately. Kubernetes resource requests and
limits have different purposes; see
[Kubernetes resource management](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/).
Do not derive a production minimum from the test fixture's small memory requests.
Measure RSS with the actual models, cache cardinality, payloads and enabled features.

## Size concurrency for real response duration

For stable traffic, estimate mean live requests as:

```text
mean live requests = target RPS × mean admitted request duration in seconds
```

Duration includes the full response lifetime, including a stream. The following
example assumes a five-second mean lifetime and one provider attempt per request.
It gives a lower bound at mean load, not a release sizing recommendation.

| Target | Mean live requests at five seconds | Provider requests per minute |
| --- | ---: | ---: |
| 50 RPS | 250 | 3,000 |
| 100 RPS | 500 | 6,000 |
| 200 RPS | 1,000 | 12,000 |
| 500 RPS | 2,500 | 30,000 |
| 1,000 RPS | 5,000 | 60,000 |

Allow additional capacity for the measured duration distribution, uneven routing,
bursts and failure recovery. Specify the permitted burst rate and duration.
Do not use p95 as the mean input or treat average occupancy as a safe admission cap.

The current production overlay permits 100 active inference lifetimes per API
process and up to 12 API replicas, with one process each. Its maximum ingress
capacity is therefore 1,200, before other limits apply. It cannot sustain the
example 500 RPS workload with 2,500 mean live requests. That example needs at least
25 such processes at full occupancy, before headroom or failure capacity.
Any new replica or admission limit requires a new complete capacity calculation.

The shared preflight limit applies only before final admission; it is not a
provider-stream limit. Model, tenant and provider concurrency limits also apply.
Check ingress sockets, HTTP pools and file descriptors against the full lifetime
and retry workload. See [saturation autoscaling](saturation-autoscaling.md).

## Production component requirements

### API and worker roles

Use the role separation in [Accounting v2](accounting-v2.md). For native accounting,
API pods use local permits and the durable terminal journal. Dedicated accounting
request and projection owners handle database work. Do not run competing legacy
and native money owners for the same generation.

Choose worker replica counts from measured acknowledgement time, oldest pending
work age and post-load drain. Worker processing must keep up with new required
work and clear backlog within the recovery objective while arrivals continue.
Test the loss of an owner and recovery of its accepted work. Extra API replicas
without sufficient accounting capacity can move the bottleneck into persistence.
Keep batch processing on separate workers with durable shared object storage.

### PostgreSQL

Use the authoritative primary with authenticated, protected connections and
durable storage. Keep commit durability enabled. Size CPU, storage latency, IOPS,
memory and connection capacity for accounting, control work and background
maintenance together. Test retained-history cardinality, not only an empty database.

Separate foreground, control and accounting allocations must retain their bounds.
Include maximum replicas, process counts, rollout overlap, migration connections
and operating reserves. The chart's declared connection ceiling must fit the real
server allocation; it is not a measured throughput rating. Monitor acquisition,
lock and statement time, journal age, reporting delay and vacuum progress.
See [dependency capacity](dependency-capacity.md).

Apply the additive migrations through one coordinated release workflow.
Follow the stopped-writer upgrade, generation activation, checkpoint replay and
rollback rules in the accounting runbook. Back up and restore-test the database.
Do not change money, disable required writes or weaken deadlines to reach a rate.

### Redis and the network

Use the centrally owned critical, cache and bulk allocations. Protect critical
auth, routing and lease state from bulk-cache eviction and memory pressure.
Separate Redis database numbers are not memory or CPU isolation. Use the
supported topology and real server client limits described in the capacity runbook.

Keep network latency and packet loss between APIs, Redis and PostgreSQL within
the tested budgets. Monitor Redis server execution and client round-trip time
separately. Fast Redis commands do not prove that the client event loop or network
is fast. The 1,000 RPS diagnosis measured about 39 microseconds for server EVAL,
but 38.15 milliseconds for successful client round trips.

Retain finite acquisition and socket deadlines, bounded waiters and the production
failure policy. A larger pool is not a remedy for CPU saturation or repeated work.
Use private networking, TLS where supported, scoped credentials and enforced
network policies. Replace all fixture credentials, test CIDRs and local URLs.

### Provider quotas and request shape

Obtain real allocations for every model and provider credential domain, including
failover destinations and other applications that share the quota.

```text
provider RPM = target RPS × 60 × expected attempts per request
provider TPM = target RPS × 60 × mean charged tokens across all attempts
```

Check input, output and model-specific token limits separately where they apply.
Use worst-case attempt and token bounds for admission and the chart's provider
capacity contract; averages alone cannot enforce a hard quota. Include concurrent
streams, connection budgets, retry limits and output-token policies.
Do not assume that a provider's RPM allocation permits the required concurrency.

Test realistic payload sizes, prompts, guardrails, tools, streams, cache misses,
auth misses, tenant mix and optional callbacks. A cache-hit rate or many independent
tenants can change capacity; record that mix with the target.

## Configure and validate the deployment

Use `deploy/kubernetes/helm/values-production.yaml` and, for native accounting,
layer `values-accounting-native.yaml` afterward. Add an operator overlay with
real secrets, resource allocations, provider domains, cluster CIDRs and network
rules. Set `secret.existingSecret` to a provisioned master/salt Secret and configure
the external database and Redis connection secrets. Do not put secret values in
the overlay. Do not deploy `values-capacity-fixture.yaml` as production configuration.
The native overlay's test CIDR and restricted egress rules require local replacement.

Validate the exact overlays without applying them:

```sh
helm lint deploy/kubernetes/helm \
  -f deploy/kubernetes/helm/values-production.yaml \
  -f deploy/kubernetes/helm/values-accounting-native.yaml \
  -f operator-capacity.yaml --set image.digest=sha256:YOUR_RELEASE_DIGEST
helm template gateway deploy/kubernetes/helm --namespace deltallm \
  -f deploy/kubernetes/helm/values-production.yaml \
  -f deploy/kubernetes/helm/values-accounting-native.yaml \
  -f operator-capacity.yaml --set image.digest=sha256:YOUR_RELEASE_DIGEST
```

Review the rendered `report.json` capacity contract and effective settings.
Budget peak connections across each process role that actually opens that pool:
`peak processes × connections per process`, summed across roles plus reserves.
Check actual PostgreSQL connections, Redis clients on each physical server,
provider allocations, node resources and file descriptors. The
[rendered profiles](capacity-profiles.md) show example declarations, not purchased
capacity or an RPS guarantee.

Enable HPA saturation signals as well as CPU and memory. Confirm that resource
and custom metrics are available, and that cluster capacity can place new pods.
Keep enough ready replicas for the required burst; HPA cannot react immediately.
Validate scale-up, safe scale-down and loss of a pod or node under traffic. See
[Kubernetes HPA](https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/)
and the repository's autoscaling runbook. Do not raise HPA maxima without updating
the dependency capacity contract.

## Evidence required before release

1. Record the immutable image, schema, overlays, hardware, replica counts,
   workload, provider allocations and acceptance limits.
2. Prove generator capacity separately. Run it outside production-serving nodes.
3. Pass the normal fixed-image short ladder and ten-minute 50/100/200/500 RPS
   stages with the [reproduction runner](../project/issue-320-rps-reproduction.md).
   Keep its stop rules. Short selected diagnostics do not qualify a release.
4. Retain the existing synthetic-workload limits: at least 99.9% success,
   p95 at most 150 ms, p99 at most 300 ms and active-request slope at most
   +0.01 requests/second. Preserve generator, resource, dependency and strict
   financial gates, including correct charges, no overspend and completed drain.
5. Test the representative production traffic mix through the real ingress.
   Set separate streaming and real-provider SLOs; the synthetic 150/300 ms limits
   do not describe model generation time.
6. Test bursts, background work, dependency failure, owner loss, rollout and
   recovery. Capture scaling delays, rejection rates, reporting catch-up and
   exact financial state. Keep every failed attempt.
7. Complete the [production checklist](production-checklist.md), including
   security, backups, alert ownership and the last safe rollback point.

Do not promise 1,000 RPS from the current evidence. First distinguish application
CPU cost from fixture overhead, then verify the chosen code or topology change
with new source and image identities. More hardware may help, but its effect
must be measured rather than assumed.
