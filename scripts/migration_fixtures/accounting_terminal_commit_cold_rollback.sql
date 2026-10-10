-- Restore only the prior commit body and remove its function-local plan policy.
-- No financial rows, indexes, leases, or schema are removed.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

DO $migration$
DECLARE
    definition TEXT;
    old_begin TEXT := $old$BEGIN
    IF p_generation IS NULL$old$;
    new_begin TEXT := $new$BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_index i
        WHERE i.indrelid='deltallm_accounting_terminal_journal'::regclass
          AND i.indisprimary AND i.indisvalid AND i.indisready
    ) THEN
        RAISE EXCEPTION 'accounting_terminal_commit_key_index' USING ERRCODE='P0001';
    END IF;
    IF p_generation IS NULL$new$;
BEGIN
    SELECT pg_get_functiondef('deltallm_accounting_materialize_terminal_journal(bigint,text,uuid,bigint[])'::regprocedure)
        INTO STRICT definition;
    IF length(definition)-length(replace(definition,new_begin,''))<>length(new_begin) THEN
        RAISE EXCEPTION 'accounting_terminal_commit_cold_definition';
    END IF;
    EXECUTE replace(definition,new_begin,old_begin);
END;
$migration$;

-- Preserve all other function, caller, pool, and database settings.
ALTER FUNCTION deltallm_accounting_materialize_terminal_journal(
    BIGINT,TEXT,UUID,BIGINT[]
) RESET plan_cache_mode;

COMMIT;
