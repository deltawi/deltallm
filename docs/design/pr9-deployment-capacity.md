# Deployment capacity and saturation autoscaling

PR9 of [#320](https://github.com/deltawi/deltallm/issues/320) extends the existing
Helm capacity calculation. Helm owns replica, surge, retiring-generation and pool
arithmetic. A versioned report is consumed by typed operator tooling and startup
validation; those consumers do not implement another deployment calculator.

The process bootstrap owns clients and their allocation snapshots. PostgreSQL
remains the durable source of truth; a capacity report is an operator declaration,
not a reservation ledger. A mismatch fails startup before readiness. No report
read, infrastructure probe or additional SQL/Redis call runs per inference request.

Production explicitly enables the existing ingress and shared preflight gates,
required outboxes and fail-closed critical Redis controls. All enabled roles,
transports (including proxy mounts), migrations, other clients and headroom count.
File descriptor declarations cover inbound idle connections as well as admitted
requests, Python and Prisma processes, and peak node placement. Operators must
verify physical limits and edge enforcement; an allocation is not provisioning.

The production profile starts one managed process per pod. Its per-process
inventory is:

| Owner | Declared ceiling | Accounting |
| --- | ---: | --- |
| Control and foreground Prisma pools | 20 + 8 PostgreSQL connections | Counted directly with their engine processes |
| Telemetry and worker Prisma pools | 5 + 5 PostgreSQL connections | Counted with outboxes; spend settlement is inside telemetry's 5 |
| Critical, cache and bulk Redis pools | 64 + 16 + 16 clients | Counted by physical Redis endpoint |
| Shared provider HTTPX client | 500 connections | Counted per process and proxy transport |
| Control HTTPX client | 100 connections | Counted per process and proxy transport |
| Shared notification webhook client | 20 connections when constructed | Included in the auxiliary HTTP reserve |
| Batch webhook transport | `batch_webhook_max_concurrency` when the batch role is enabled | Included in the auxiliary HTTP reserve |
| Short-lived JWKS/SSO and configured HTTP guardrail clients | Bounded by the control/ingress and guardrail admissions | Included in the auxiliary HTTP reserve, not claimed as an owned pool |
| Optional provider SDK, callback, MCP and object-storage transports | Depends on enabled integrations | Operator must size the auxiliary reserve for the actual release |

The enabled outbox and spend-intent profile budgets five Prisma engine processes
per application process. The runtime observation checks that process count and
each engine's file descriptor limit.

The example auxiliary reserve is 2,048 connections per process. Startup checks
the known owned pool ceilings and requires at least 20 auxiliary slots for the
shared notification client; it cannot inspect every optional SDK transport.
Before a release, the operator must inventory the integrations actually enabled,
their client multiplicity, retry behavior, proxy mounts and close ownership.
An unknown optional transport is not evidence of zero connections.

Provider RPM, TPM and simultaneous-attempt allocations are separate dimensions.
An HTTP pool limits transports; existing generic pre-call usage checks do not
provide atomic quota reservation. Reports identify this enforcement limitation.
PR9 rejects invalid declared envelopes without claiming to implement #210.

Prometheus Adapter translates the existing ingress lifetime gauge to a per-pod
custom metric. Kubernetes HPA owns scaling, using admitted saturation alongside
CPU. The monitoring release owns the adapter/APIService; application releases
must not install competing cluster-wide adapters. Missing/stale metrics stay
unavailable, never zero. Labels identify fixed allocations and Kubernetes pods,
not tenants, requests or credentialed URLs.

Alternatives rejected: CPU-only asynchronous scaling, a second application
autoscaling controller, a Python duplicate of Helm arithmetic, multiplying HTTP
pools into provider quota claims, and unbounded local overflow caches.

Rollout first verifies the physical dependencies, edge and metrics API, then a
fixed warm replica count, then HPA. Rollback restores the last verified fixed
replica profile while preserving admission, durable accounting, migration-before-
rollout and PR8 drain ordering. Never lower allocations beneath live or retiring
processes. Existing summary ConfigMap keys remain for operator compatibility;
the structured report adds a schema version and rejects unknown fields.

Controlled 2/3/4-pod experiments prove configuration, scaling and bounded overload.
Sustained supported RPS/stream counts and N−1 capacity remain PR10 qualification.
