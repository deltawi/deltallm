# Scoped Usage Reporting Rollout

Team and personal usage views depend on immutable API-key owner snapshots. They are disabled by default so schema changes and every ownership writer can be deployed safely before users rely on them.

## Rollout

1. Keep `general_settings.spend_reporting_v2_enabled: false`.
2. On a large or write-heavy spend table, pre-create the two cursor indexes before deploying. Prisma cannot combine several concurrent index operations safely in one retryable migration; pre-creating the exact names makes the transactional migration a no-op for those indexes:

   ```sql
   CREATE INDEX CONCURRENTLY IF NOT EXISTS "deltallm_spendlog_events_org_time_id_idx"
     ON "deltallm_spendlog_events"("organization_id", "start_time", "id");
   CREATE INDEX CONCURRENTLY IF NOT EXISTS "deltallm_spendlog_events_time_id_idx"
     ON "deltallm_spendlog_events"("start_time", "id");
   ```

3. Deploy the release to every gateway and batch worker. Startup applies the owner columns, immutable batch-snapshot markers, compatibility triggers, and retry-safe transactional index migrations. Schema and index lock acquisition is capped at five seconds, so a busy table fails deployment instead of waiting indefinitely or queuing gateway writes behind the migration.

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

   A zero exit status and `"ready": true` confirm that all migrations completed, snapshot-completeness columns have their fail-safe defaults, required indexes are valid, and rolling-compatibility ownership triggers are enabled. Upgraded writers mark snapshots complete even when a key is deliberately ownerless, so steady-state traffic bypasses compatibility lookups. Existing batch sessions and jobs remain unattributed instead of being assigned to a later key owner. The check deliberately does not infer fleet versions; confirm that separately in your deployment platform.
6. Confirm every gateway and batch worker is on this release, then set `spend_reporting_v2_enabled: true` and perform a configuration rollout.
7. Sign in as a regular user and verify that **Usage** shows only **Your usage**. Verify a team administrator can switch between team and personal views, and an organization owner can switch between organization and personal views.

## Rollback

Set `spend_reporting_v2_enabled: false` first. This immediately hides team and personal views while keeping platform and organization reporting available. Keep the additive columns, indexes, and compatibility triggers in place during a code rollback; removing them while older and newer writers overlap can permanently lose owner attribution.

Historical spend rows whose owner was not recorded remain unattributed. Do not backfill them from current API-key ownership because keys can be transferred or deleted after the request occurred.
