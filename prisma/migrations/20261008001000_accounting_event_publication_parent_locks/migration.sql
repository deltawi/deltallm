-- Foreign-key parent locks must precede event publication locks.
-- Fully settled compact receipts do not need reservation parent locks.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

DO $migration$
DECLARE
    definition TEXT;
    marker TEXT := '    PERFORM deltallm_accounting_lock_event_publication(p_generation,ARRAY(';
    parent_locks TEXT := $locks$    IF EXISTS (SELECT 1 FROM unnest(prepared) v WHERE NOT (v->>'receipt_only')::boolean) THEN
        PERFORM 1 FROM deltallm_accounting_budget_windows w
        WHERE w.window_id=ANY(ARRAY(
            SELECT gw.window_id FROM unnest(ARRAY(
                SELECT DISTINCT v->>'grant_id' FROM unnest(prepared) v
                WHERE NOT (v->>'receipt_only')::boolean
            )) grant_key(grant_id)
            CROSS JOIN LATERAL (
                SELECT gw.window_id FROM deltallm_accounting_grant_windows gw
                WHERE gw.grant_id=grant_key.grant_id OFFSET 0
            ) gw
        )) ORDER BY w.window_id FOR KEY SHARE;
    END IF;
$locks$;
BEGIN
    SELECT pg_get_functiondef('deltallm_accounting_materialize_terminal_journal(bigint,text,uuid,bigint[])'::regprocedure)
        INTO STRICT definition;
    IF length(definition)-length(replace(definition,marker,''))<>length(marker) THEN
        RAISE EXCEPTION 'accounting_event_publication_parent_definition';
    END IF;
    EXECUTE replace(definition,marker,parent_locks||marker);
END;
$migration$;

COMMIT;
