-- Replan bounded terminal commit key batches with their actual array values.
-- Keep financial validation, replay, fencing, lock order, and effects unchanged.
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
    IF length(definition)-length(replace(definition,old_begin,''))<>length(old_begin) THEN
        RAISE EXCEPTION 'accounting_terminal_commit_cold_definition';
    END IF;
    EXECUTE replace(definition,old_begin,new_begin);
END;
$migration$;

-- This function is the only policy owner. Caller, pool, and database are unchanged.
ALTER FUNCTION deltallm_accounting_materialize_terminal_journal(
    BIGINT,TEXT,UUID,BIGINT[]
) SET plan_cache_mode=force_custom_plan;

COMMIT;
