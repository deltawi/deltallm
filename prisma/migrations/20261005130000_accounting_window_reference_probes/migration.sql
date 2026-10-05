-- Keep each explicit reference dependent on one window primary key.
-- Keep both bulk lock queries dependent on their bounded window ID arrays.
-- Replace only exact lookup fragments. Do not change money or lock order.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

DO $migration$
DECLARE
    target RECORD;
    function_definition TEXT;
    old_reference TEXT := $old$JOIN deltallm_accounting_budget_windows w ON w.window_id=ref->>'window_id'$old$;
    new_reference TEXT := $new$CROSS JOIN LATERAL (
        SELECT lookup.* FROM deltallm_accounting_budget_windows lookup
        WHERE lookup.window_id=ref->>'window_id' OFFSET 0
    ) w$new$;
    old_bulk TEXT := $old$w.window_id IN (
            SELECT resolved.window_id FROM jsonb_array_elements(p_items) value
            CROSS JOIN LATERAL deltallm_accounting_permit_window_ids(
                p_generation,value->'reservation'
            ) resolved
        )$old$;
    new_bulk TEXT := $new$w.window_id = ANY(ARRAY(
            SELECT resolved.window_id FROM jsonb_array_elements(p_items) value
            CROSS JOIN LATERAL deltallm_accounting_permit_window_ids(
                p_generation,value->'reservation'
            ) resolved
        ))$new$;
BEGIN
    FOR target IN SELECT * FROM (VALUES
        ('deltallm_accounting_permit_window_ids(bigint,jsonb)',old_reference,new_reference),
        ('deltallm_accounting_ensure_grants_batch(bigint,text,integer,integer,jsonb)',old_reference,new_reference),
        ('deltallm_accounting_allocate_permit_grants_batch(bigint,text,integer,jsonb)',old_bulk,new_bulk),
        ('deltallm_accounting_allocate_local_permit_grants_batch(bigint,text,integer,jsonb)',old_bulk,new_bulk)
    ) change(signature,old_fragment,new_fragment)
    LOOP
        SELECT pg_get_functiondef(target.signature::regprocedure)
            INTO STRICT function_definition;
        IF (length(function_definition)-length(replace(
            function_definition,target.old_fragment,''
        )))<>length(target.old_fragment) THEN
            RAISE EXCEPTION 'accounting_window_reference_definition';
        END IF;
        EXECUTE replace(function_definition,target.old_fragment,target.new_fragment);
    END LOOP;
END;
$migration$;

COMMIT;
