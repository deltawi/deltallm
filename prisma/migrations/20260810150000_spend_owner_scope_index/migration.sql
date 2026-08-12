-- Kept separate from the owner-column migration so a lock-timeout failure
-- leaves the column committed and allows an operator to pre-create this index
-- concurrently before resolving and retrying the migration.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '15min';

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_class AS index_class
        JOIN pg_index AS index_meta ON index_meta.indexrelid = index_class.oid
        JOIN pg_namespace AS namespace ON namespace.oid = index_class.relnamespace
        WHERE namespace.nspname = current_schema()
          AND index_class.relname = 'deltallm_spendlog_events_owner_time_id_idx'
          AND (NOT index_meta.indisvalid OR NOT index_meta.indisready)
    ) THEN
        DROP INDEX "deltallm_spendlog_events_owner_time_id_idx";
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS "deltallm_spendlog_events_owner_time_id_idx"
ON "deltallm_spendlog_events"("owner_account_id", "start_time", "id");
