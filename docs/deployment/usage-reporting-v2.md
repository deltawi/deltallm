---
title: Scoped usage reporting rollout
description: Release-specific steps for enabling owner-based usage views.
status: stable
audience: operators
applies_to: Releases whose notes explicitly link this runbook.
---

# Scoped usage reporting rollout

Team and personal usage views depend on immutable API-key owner snapshots. They are disabled by default so schema changes and every ownership writer can be deployed safely before users rely on them.

!!! warning "Check your release notes"
    Use this runbook only when the release notes for your target version link to it. Do not assume its migration names apply to another release.

## Rollout

1. Keep `general_settings.spend_reporting_v2_enabled: false`.
2. On a large or write-heavy spend table, pre-create the two cursor indexes before deploying. Prisma cannot combine several concurrent index operations safely in one retryable migration; pre-creating the exact names makes the transactional migration a no-op for those indexes:

   ```sql
   CREATE INDEX CONCURRENTLY IF NOT EXISTS "deltallm_spendlog_events_org_time_id_idx"
     ON "deltallm_spendlog_events"("organization_id", "start_time", "id");
   CREATE INDEX CONCURRENTLY IF NOT EXISTS "deltallm_spendlog_events_time_id_idx"
     ON "deltallm_spendlog_events"("start_time", "id");
   ```

3. Deploy the release to each gateway and batch worker.
   Startup applies owner columns, immutable batch-snapshot markers, compatibility triggers, and transactional index migrations with retry support.
   Schema and index lock acquisition has a five-second limit.
   A busy table causes deployment failure instead of an indefinite wait or a queue of gateway writes behind the migration.

   If the owner-scope migration times out, allow the blocking transaction to finish or schedule the migration for a quieter window. The migration is atomic and does not need manual schema cleanup. Mark only that failed attempt rolled back, then rerun deployment:

   ```bash
   uv run prisma migrate resolve --rolled-back 20260810140000_spend_owner_scope \
     --schema prisma/schema.prisma
   uv run prisma migrate deploy --schema prisma/schema.prisma
   ```

   If the cursor-index migration times out because step 2 was skipped, create both indexes concurrently, then recover only that failed migration and rerun deployment:

   ```bash
   uv run prisma migrate resolve --rolled-back 20260810120000_spend_log_cursor_indexes \
     --schema prisma/schema.prisma
   uv run prisma migrate deploy --schema prisma/schema.prisma
   ```
4. For a large or write-heavy spend table, create the owner index concurrently after the owner-column migration commits. If the separate owner-index migration timed out, create the index, mark only that failed attempt rolled back, and rerun the deployment:

   ```sql
   CREATE INDEX CONCURRENTLY IF NOT EXISTS "deltallm_spendlog_events_owner_time_id_idx"
     ON "deltallm_spendlog_events"("owner_account_id", "start_time", "id");
   ```

   ```bash
   uv run prisma migrate resolve --rolled-back 20260810150000_spend_owner_scope_index \
     --schema prisma/schema.prisma
   uv run prisma migrate deploy --schema prisma/schema.prisma
   ```

5. Run the read-only database check:

   ```bash
   DATABASE_URL='postgresql://...' uv run python scripts/check_spend_reporting_v2_readiness.py
   ```

   A zero exit status and `"ready": true` confirm these conditions:

   - All migrations completed.
   - Snapshot-completeness columns have fail-safe defaults.
   - Required indexes are valid.
   - Ownership triggers for compatibility during rolling upgrades are enabled.

   Upgraded writers mark snapshots complete even when a key is intentionally ownerless.
   Steady-state traffic thus skips compatibility lookups.
   Existing batch sessions and jobs remain unattributed. They do not receive a later key owner.
   This check does not infer replica versions.
6. Confirm in the deployment platform that each gateway and batch worker uses this release.
7. Set `spend_reporting_v2_enabled: true`.
8. Deploy the configuration change.
9. Sign in as a regular user.
10. Verify that **Usage** shows only **Your usage**.
11. Verify that a team administrator can switch between team and personal views.
12. Verify that an organization owner can switch between organization and personal views.

## Rollback

First, set `spend_reporting_v2_enabled: false`.
This immediately hides team and personal views. Platform and organization reporting remain available.
During code rollback, keep the added columns, indexes, and compatibility triggers.
Their removal while older and newer writers run together can permanently lose owner attribution.

Historical spend rows whose owner was not recorded remain unattributed. Do not backfill them from current API-key ownership because keys can be transferred or deleted after the request occurred.
