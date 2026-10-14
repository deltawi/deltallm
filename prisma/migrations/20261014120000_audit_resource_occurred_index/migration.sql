-- The Audit logs side panel lists the other events on the same resource.
-- Prisma runs multi-statement PostgreSQL migrations in a transaction. Keep the
-- migration atomic and bound lock acquisition; large audit tables should
-- pre-create this exact index concurrently as described in the upgrade notes
-- (docs/features/audit-log.md).
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
          AND index_class.relname = 'deltallm_auditevent_resource_occurred_at_idx'
          AND (NOT index_meta.indisvalid OR NOT index_meta.indisready)
    ) THEN
        DROP INDEX "deltallm_auditevent_resource_occurred_at_idx";
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS "deltallm_auditevent_resource_occurred_at_idx"
ON "deltallm_auditevent"("resource_type", "resource_id", "occurred_at");
