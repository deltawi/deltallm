-- Restore the earlier claim body and remove only its local plan-cache policy.
-- No records, indexes, schema, leases, or capacity charges are removed.
-- Run through the coordinated migration workflow before reverting the image.
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
        RAISE EXCEPTION 'accounting_terminal_claim_key_index' USING ERRCODE='P0001';
    END IF;
    IF p_generation IS NULL$new$;
    old_candidates TEXT := $old$    ) pick;
    PERFORM 1 FROM deltallm_accounting_terminal_capacity$old$;
    new_candidates TEXT := $new$    ) pick;
    IF candidate_ids IS NULL THEN RETURN; END IF;
    PERFORM 1 FROM deltallm_accounting_terminal_capacity$new$;
    old_chosen TEXT := $old$    ) pick WHERE pick.ordinal<=p_limit AND pick.bytes<=1048576;
    UPDATE deltallm_accounting_terminal_journal$old$;
    new_chosen TEXT := $new$    ) pick WHERE pick.ordinal<=p_limit AND pick.bytes<=1048576;
    IF chosen_ids IS NULL THEN RETURN; END IF;
    UPDATE deltallm_accounting_terminal_journal$new$;
BEGIN
    SELECT pg_get_functiondef('deltallm_accounting_claim_terminal_journal(bigint,text,uuid,integer,integer)'::regprocedure)
        INTO STRICT definition;
    IF length(definition)-length(replace(definition,new_begin,''))<>length(new_begin)
       OR length(definition)-length(replace(definition,new_candidates,''))<>length(new_candidates)
       OR length(definition)-length(replace(definition,new_chosen,''))<>length(new_chosen) THEN
        RAISE EXCEPTION 'accounting_terminal_claim_cold_definition';
    END IF;
    EXECUTE replace(replace(replace(definition,new_begin,old_begin),new_candidates,old_candidates),new_chosen,old_chosen);
END;
$migration$;

-- The earlier function had no plan_cache_mode override; keep other settings.
ALTER FUNCTION deltallm_accounting_claim_terminal_journal(
    BIGINT,TEXT,UUID,INTEGER,INTEGER
) RESET plan_cache_mode;

COMMIT;
