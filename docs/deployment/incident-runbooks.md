# Incident Runbooks

These runbooks provide a safe starting structure. Replace placeholders with your alert links,
owners, provider contacts, and recovery objectives before production use.

## First five minutes

1. Acknowledge the alert, assign an incident lead, and record the start time and affected traffic.
2. Check recent releases, migrations, configuration changes, provider incidents, and dependency health.
3. Preserve request IDs, sanitized logs, metrics, rollout state, and migration/job output.
4. Prefer reversible containment: stop a rollout, disable a feature gate, remove a failing
   deployment from routing, or reduce traffic. Do not delete durable data to clear a symptom.
5. Communicate scope and next update time without placing credentials or customer content in chat/tickets.

## Triage map

| Signal | Likely domain | First checks |
| --- | --- | --- |
| Liveness fails | Process/node | Pod events, exit reason, CPU/memory, image/config availability |
| Liveness passes; readiness fails | PostgreSQL, Redis, or supervised service | Private readiness details, dependency latency/auth, worker startup logs |
| Provider errors or high latency | Provider/egress/routing | Provider status, deployment health, timeouts, connection pools, fallback events |
| `401`/`403` spike | Keys, JWT/SSO, tenancy, lifecycle | Credential expiry/revocation, clock, role/scope changes, lifecycle state |
| `429` spike | Rate/budget/capacity policy | Rate-limit headers, tier saturation, tenant budget, Redis health |
| Audit/spend/email backlog | Durable worker/dependency | Queue depth/age, database capacity, leases, worker logs, blocked records |
| Suspected data exposure | Security | Isolate access, preserve evidence, revoke affected credentials, private reporting path |

Operational endpoints are unauthenticated in the application. Query them only through the trusted
operator network described in [Health and metrics](../api/health.md).

## Dependency outage

1. Determine whether PostgreSQL, Redis, DNS, object storage, or another required dependency failed.
2. Stop rollout and avoid restart storms. Confirm connection limits and provider maintenance state.
3. For PostgreSQL, preserve write integrity; do not fail over or restore without database-owner approval.
4. For Redis, identify which enabled features rely on durable/distributed state before flushing or replacing it.
5. When dependency health returns, verify readiness for every role, then watch durable queue age and
   duplicate-prevention behavior while traffic recovers.

## Provider degradation

1. Filter failures by provider, model deployment, status, timeout phase, and region.
2. Compare provider status with internal egress/NAT/DNS saturation and configured HTTP limits.
3. Cool down or disable only the affected deployment when healthy alternatives exist; respect data
   residency, capability, and cost constraints when changing fallback order.
4. Test one authenticated request, then restore traffic gradually and watch error/latency and spend.

## Backlog or worker failure

1. Identify the queue/outbox and whether records are best-effort, required, retrying, blocked, or unknown.
2. Repair the dependency or worker first. Do not blindly replay records with uncertain external side effects.
3. Scale only after checking database connections, lease contention, provider quotas, and downstream capacity.
4. Reconcile unknown email/webhook/provider outcomes manually; record replay decisions in the audit trail.
5. Confirm oldest-event age trends down and no new failures accumulate.

## Suspected credential or data compromise

1. Restrict affected routes/accounts and preserve logs, audit records, and deployment state.
2. Revoke exposed provider, application, session, database, or integration credentials. Rotate related
   credentials according to their dependency order.
3. Block unapproved egress and review access across logs, backups, callbacks, MCP tools, and providers.
4. Report product vulnerabilities through the [private vulnerability process](../security/vulnerability-reporting.md).
5. Coordinate legal/privacy/customer notification through your organization's incident process.

## After recovery

- Verify synthetic success and intentional authorization failures from the correct network zones.
- Confirm queues, alerts, spend, audit ingestion, and provider health remain stable.
- Record timeline, impact, decisions, contributing controls, and follow-up owners.
- Update alerts, runbooks, tests, capacity assumptions, and documentation from the findings.
