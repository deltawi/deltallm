-- Cold cached array-key plans must not scan completed terminal history.
-- The claim keeps its existing limits, lock order, leases, and money effects.
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
    IF length(definition)-length(replace(definition,old_begin,''))<>length(old_begin)
       OR length(definition)-length(replace(definition,old_candidates,''))<>length(old_candidates)
       OR length(definition)-length(replace(definition,old_chosen,''))<>length(old_chosen) THEN
        RAISE EXCEPTION 'accounting_terminal_claim_cold_definition';
    END IF;
    EXECUTE replace(replace(replace(definition,old_begin,new_begin),old_candidates,new_candidates),old_chosen,new_chosen);
END;
$migration$;

-- Only this function replans bounded array-key probes using their actual keys.
-- The caller, other functions, pool, and database keep their existing policy.
ALTER FUNCTION deltallm_accounting_claim_terminal_journal(
    BIGINT,TEXT,UUID,INTEGER,INTEGER
) SET plan_cache_mode=force_custom_plan;

COMMIT;
