-- Prisma runs multi-statement PostgreSQL migrations in a transaction. Keep the
-- migration atomic and bound lock acquisition; production tables should
-- pre-create these exact indexes concurrently as described in the rollout guide.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '15min';

-- Repair an invalid target left by an interrupted manual concurrent build.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_class AS index_class
        JOIN pg_index AS index_meta ON index_meta.indexrelid = index_class.oid
        JOIN pg_namespace AS namespace ON namespace.oid = index_class.relnamespace
        WHERE namespace.nspname = current_schema()
          AND index_class.relname = 'deltallm_spendlog_events_org_time_id_idx'
          AND (NOT index_meta.indisvalid OR NOT index_meta.indisready)
    ) THEN
        DROP INDEX "deltallm_spendlog_events_org_time_id_idx";
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS "deltallm_spendlog_events_org_time_id_idx"
ON "deltallm_spendlog_events"("organization_id", "start_time", "id");

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_class AS index_class
        JOIN pg_index AS index_meta ON index_meta.indexrelid = index_class.oid
        JOIN pg_namespace AS namespace ON namespace.oid = index_class.relnamespace
        WHERE namespace.nspname = current_schema()
          AND index_class.relname = 'deltallm_spendlog_events_time_id_idx'
          AND (NOT index_meta.indisvalid OR NOT index_meta.indisready)
    ) THEN
        DROP INDEX "deltallm_spendlog_events_time_id_idx";
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS "deltallm_spendlog_events_time_id_idx"
ON "deltallm_spendlog_events"("start_time", "id");

-- The cursor indexes cover the same leading-column lookups as their shorter
-- predecessors, so remove the redundant indexes to avoid extra spend-log write cost.
DROP INDEX IF EXISTS "deltallm_spendlog_events_org_time_idx";
DROP INDEX IF EXISTS "deltallm_spendlog_events_start_time_idx";
