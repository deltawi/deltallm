# Organization Deletion

Organization deletion is an asynchronous, durable control-plane workflow. A platform administrator starts it from an organization's **Overview** page in the admin UI. Deletion is not exposed to organization owners or ordinary organization administrators.

## Lifecycle

The workflow uses four organization states:

1. `active` — normal access and mutations are allowed.
2. `deletion_pending` — access is revoked immediately, pending work is cancelled, and the recovery window is open.
3. `purging` — irreversible cleanup has begun; restore is no longer available.
4. `deletion_failed` — cleanup exhausted its automatic retries and requires a platform administrator to retry it.

The request transaction creates the durable deletion job, changes the organization state, increments the fleet-wide lifecycle generation, writes an audit event, and enqueues cache invalidation atomically. API-key, JWT, and custom authentication paths reject inactive organizations. Each process refreshes the global generation in the background and compares it with the lifecycle snapshot already carried by cached API-key auth. The steady-state data plane therefore performs no additional lifecycle database call; a rare generation mismatch triggers a single-flighted organization lookup. Requests fail closed when the background generation becomes older than the configured staleness bound. PostgreSQL remains authoritative.

## Administrator flow

The UI first loads a server-generated impact preview. Its confirmation token binds the organization
lifecycle and destructive scope, while naturally changing operational and retained-history counts
remain informational and are rechecked by the locked request transaction. The administrator must:

- inspect counts for teams, credentials, memberships, invitations, approvals, and owned assets;
- transfer or unbind organization-owned MCP servers, prompt templates, and route groups that are
  still referenced by another organization or team;
- resolve sensitive-history ownership blockers before requesting deletion. Durable organization,
  team, and API-key claims take precedence; the requester's current membership is never used as
  historical ownership proof;
- normalize legacy batch records that can be tenant-attributed but do not yet carry a durable
  organization snapshot;
- type the exact organization name;
- acknowledge that active batches are cancelled and will not restart after restore; and
- submit an idempotent deletion request.

The page then shows the durable job phase and progress. Refreshing or switching API replicas does not lose the job.

Restore is available only before the recovery deadline and before the worker enters an irreversible phase. Restore reactivates access but does not recreate cancelled invitations, approvals, batch work, or email deliveries.

## Removed and retained data

Cleanup removes organization-owned control-plane state in bounded pages:

- pending invitations and their active tokens, while cancelling queued invitation email;
- pending MCP approvals and approval history;
- active/staged batch work, via normal cancellation paths;
- organization-owned MCP servers, prompt templates, and route groups, including their child rows;
- prompt render logs, including stored variables and metadata;
- API keys and service accounts;
- callable-target, route, MCP, prompt, tier, and related scope bindings;
- organization and team memberships, teams, and scheduler flow state; and
- queued or retrying webhook deliveries, plus processing deliveries whose lease has expired,
  associated with cancelled batch work.

The workflow deliberately retains immutable or terminal records needed for reporting and compliance:

- spend events;
- audit events;
- terminal batch job and item records; and
- batch file metadata and artifacts until their existing expiry/retention policy removes them.

Retained records continue to carry the deleted organization identifier. Organization and team
tombstones prevent accidental identifier reuse, so an old team identifier cannot acquire retained
history after deletion.

Platform account identities are not organization-owned and are therefore not deleted. Their organization/team memberships are removed, and legacy runtime users are detached from deleted teams. This preserves access those people may have to other organizations without leaving them access to the deleted one.

Sensitive prompt and approval history is deleted only when a durable organization, team, or API-key
claim assigns it to the organization. Contradictory claims are reported as **conflicting sensitive
records**. Legacy rows without a durable ownership claim are reported as **unattributed sensitive
records**. Either count blocks the deletion request; there is no force-delete bypass. An operator
must use an audited data-repair procedure to add or correct the explicit ownership claim, then load
a fresh preview.

Batch jobs, create sessions, and webhook deliveries use an immutable organization snapshot.
Legacy rows that can be resolved only through a current team, API key, or user relationship are
reported as **batch records missing ownership** and block deletion until the coordinated migration
job backfills them in bounded, retryable pages.

## Worker safety

Workers claim jobs with PostgreSQL row locking, leases, and a monotonically increasing claim epoch.
Every bounded cleanup page locks and validates that fence, mutates tenant rows, renews or releases
the lease, and persists progress in one short transaction. If the lease expires before commit, the
page rolls back. Cleanup queries are idempotent and page-bounded, so a worker can safely resume
after a timeout, pod termination, or expired lease.

Automatic failures use exponential backoff. After `organization_deletion_max_attempts`, the organization enters `deletion_failed`; a platform administrator can reschedule the same durable job with **Retry cleanup**. The UI never creates a second cleanup graph.

When a separate Helm batch-worker deployment is enabled, organization-deletion workers are disabled on API pods and enabled on worker pods. Without that split, each API replica may run a worker safely because database claims provide ownership.

## Operations

Important Prometheus metrics are:

- `deltallm_organization_deletion_claims_total`
- `deltallm_organization_deletion_phases_total{phase,outcome}`
- `deltallm_organization_deletion_phase_latency_seconds{phase}`
- `deltallm_organization_deletion_jobs_total{outcome}`

Alert on permanent failures, a sustained retry rate, or phase latency near `organization_deletion_worker_record_timeout_seconds`. Readiness is refreshed by successful polling and bounded record progress, so a worker remains ready while legitimately processing a full multi-wave claim batch. It fails when polling or progress is stale, an in-flight record exceeds the configured record timeout plus polling grace, or the worker task exits. To investigate, inspect the deletion job's `phase`, `attempt_count`, `last_error_code`, `next_attempt_at`, and lease fields. Error details intentionally contain an exception class and safe phase message rather than raw provider or tenant data.

Roll out lifecycle-aware binaries first with `organization_deletion_requests_enabled: false`. Enable new requests only after every API and worker replica reports lifecycle protocol v2 and a fresh lifecycle snapshot. Protocol v2 requires fail-closed scope resolution, durable principal tombstones, and atomic final inventory checks.

For emergency rollback, first disable `organization_deletion_requests_enabled`, then disable `organization_deletion_worker_enabled` to stop new claims. This does not reactivate organizations already marked for deletion. Restore individually while still within the recovery window, or correct the worker fault and re-enable it. Never deploy lifecycle-unaware code while an organization is inactive or a deletion job is unfinished. The migration is additive; do not remove lifecycle columns, job tables, triggers, or tombstones while any deployed binary uses this feature.

The large-table indexes are each built by a separate `CREATE INDEX CONCURRENTLY` migration. Deploy
them before application binaries with the checked-in coordinator:

```bash
python -m src.organization_deletion_migrations deploy --schema ./prisma/schema.prisma
```

The coordinator recognizes only the allowlisted organization-deletion migrations and exact index
definitions. On retry it removes matching invalid indexes concurrently, marks only a matching
failed Prisma migration as rolled back, redeploys, performs the bounded ownership backfill, and
verifies every required migration and index. An unexpected index definition fails closed for
operator review. Do not invoke this repair path from API startup or let replicas race it.

For a release rehearsal, point the checked-in upgrade verifier at a disposable PostgreSQL database whose name contains `organization_deletion_migration_test`:

```bash
ORGANIZATION_DELETION_MIGRATION_TEST_DATABASE_URL=postgresql://.../deltallm_organization_deletion_migration_test \
  bash scripts/verify_organization_deletion_migration.sh
```

The script resets only that explicitly marked database. It verifies an upgrade from the preceding
migration with existing organization/team data, validates the concurrent indexes, then resets once
more and verifies a fresh installation of the full migration set.

## Configuration

The default recovery window is seven days and the default authentication lifecycle staleness bound is three seconds. See [General Settings](../configuration/general.md) for every worker and cache setting.
