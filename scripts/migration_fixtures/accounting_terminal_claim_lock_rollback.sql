-- Restore the original claim lock scope. No records or schema are removed.
-- Run only through the coordinated migration workflow before reverting the image.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

DO $migration$
DECLARE
    definition TEXT;
    original_lock TEXT := $old$    PERFORM 1 FROM deltallm_accounting_terminal_capacity c WHERE c.protocol_name='primary' AND c.generation=p_generation
        AND c.accounting_partition=ANY(ARRAY(SELECT j.accounting_partition FROM deltallm_accounting_terminal_journal j
            WHERE j.sequence=ANY(candidate_ids))) ORDER BY c.accounting_partition FOR UPDATE;$old$;
    current_lock TEXT := $new$    PERFORM 1 FROM deltallm_accounting_terminal_capacity c WHERE c.protocol_name='primary' AND c.generation=p_generation
        AND c.accounting_partition=ANY(ARRAY(SELECT j.accounting_partition FROM deltallm_accounting_terminal_journal j
            WHERE j.sequence=ANY(candidate_ids) AND j.attempts>=5)) ORDER BY c.accounting_partition FOR UPDATE;$new$;
BEGIN
    SELECT pg_get_functiondef('deltallm_accounting_claim_terminal_journal(bigint,text,uuid,integer,integer)'::regprocedure)
        INTO STRICT definition;
    IF length(definition)-length(replace(definition,current_lock,''))<>length(current_lock) THEN
        RAISE EXCEPTION 'accounting_terminal_claim_capacity_definition';
    END IF;
    EXECUTE replace(definition,current_lock,original_lock);
END;
$migration$;

COMMIT;
