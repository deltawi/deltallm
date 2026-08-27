# Production Checklist

Complete this checklist before shifting production traffic. Record an owner and evidence for every
item; a checked box without a link to configuration, a test result, or an operational record is not
a release control.

## Release and architecture

- [ ] Application, chart, and configuration revisions are recorded and images are pinned by digest.
- [ ] API, workers, PostgreSQL, Redis, ingress, egress, DNS, and external integrations have named owners.
- [ ] API and worker replicas use independent failure domains where the availability target requires it.
- [ ] Capacity tests cover non-streaming, streaming, provider latency, retry/failover, and background work.
- [ ] Resource requests, limits, autoscaling bounds, disruption budgets, and connection pools match those tests.

## Identity, secrets, and network

- [ ] Master and salt keys are unique, non-placeholder secrets injected outside Git.
- [ ] Workloads use scoped, expiring API keys rather than the master key.
- [ ] Administrative roles, SSO/MFA policy, bootstrap accounts, and break-glass access are reviewed.
- [ ] TLS, allowed hosts, proxy-header trust, request limits, and public route allowlists are verified.
- [ ] Detailed health, diagnostics, metrics, runtime API schemas, PostgreSQL, and Redis are unreachable publicly.
- [ ] Database, cache, provider, callback, email, object-storage, and identity credentials are least privilege.
- [ ] Egress is limited to approved providers, callbacks, MCP servers, telemetry, and dependency endpoints.
- [ ] The rendered workload was reviewed against [Production hardening](../security/hardening.md).

## Data and delivery

- [ ] PostgreSQL is supported, monitored, encrypted, and backed up independently of the cluster.
- [ ] Redis persistence/replication and loss behavior match the features enabled in DeltaLLM.
- [ ] The release migration ran exactly once and completed before application/worker rollout.
- [ ] API and worker commands bypass the image's per-container migration bootstrap.
- [ ] A recent backup was restored into an isolated environment and application checks passed.
- [ ] The upgrade's backward-compatibility window and database-safe rollback point are documented.
- [ ] Release-specific rollout pages were reviewed for required feature gates, backfills, or cutovers.

## Observability and response

- [ ] Liveness and readiness probes use the correct endpoints and do not expose diagnostic payloads publicly.
- [ ] Metrics, logs, traces, audit ingestion, spend ingestion, queue age, provider errors, and saturation have alerts.
- [ ] Logs and telemetry were checked for secrets and unnecessary prompt/response content.
- [ ] Dashboards distinguish application, dependency, provider, and tenant-policy failures.
- [ ] On-call ownership and the [incident runbooks](incident-runbooks.md) are linked from the alert system.
- [ ] Recovery objectives, escalation paths, provider fallback decisions, and status communication are agreed.

## Go-live evidence

Before approving traffic, capture:

1. the rendered deployment and image digest;
2. migration job name, logs, and successful completion status;
3. public/private route reachability checks;
4. authenticated smoke requests and intentional authorization failures;
5. readiness and alert state for every replica and worker; and
6. the most recent restore rehearsal and rollback exercise.
