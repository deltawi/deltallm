-- Restrict admission effects to the current bounded operation and grant keys.
-- Preserve amounts, identity checks, consumption limits, and lock order.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

DO $migration$
DECLARE
    target RECORD;
    function_definition TEXT;
    old_consumption TEXT := $old$WHERE target_grant.grant_id=consumption.grant_id$old$;
    new_consumption TEXT := $new$WHERE target_grant.grant_id=consumption.grant_id
          AND target_grant.grant_id=ANY(ARRAY(
              SELECT delta.grant_id FROM consumption delta
          ))$new$;
    old_references TEXT := $old$JOIN deltallm_accounting_grant_windows grant_windows
          ON grant_windows.grant_id=inserted_operations.accounting_grant_id$old$;
    new_references TEXT := $new$CROSS JOIN LATERAL (
            SELECT lookup.window_id FROM deltallm_accounting_grant_windows lookup
            WHERE lookup.grant_id=inserted_operations.accounting_grant_id OFFSET 0
        ) grant_windows$new$;
BEGIN
    FOR target IN SELECT * FROM (VALUES
        ('deltallm_accounting_reserve_grant_batch(bigint,text,integer,integer,jsonb)',
         old_consumption,new_consumption),
        ('deltallm_accounting_reserve_grant_batch(bigint,text,integer,integer,jsonb)',
         old_references,new_references),
        ('deltallm_accounting_claim_permit_batch(bigint,text,text,uuid,jsonb)',
         old_references,new_references)
    ) change(signature,old_fragment,new_fragment)
    LOOP
        SELECT pg_get_functiondef(target.signature::regprocedure)
            INTO STRICT function_definition;
        IF length(function_definition)-length(replace(
            function_definition,target.old_fragment,''
        ))<>length(target.old_fragment) THEN
            RAISE EXCEPTION 'accounting_admission_effect_key_definition';
        END IF;
        EXECUTE replace(function_definition,target.old_fragment,target.new_fragment);
    END LOOP;
END;
$migration$;

COMMIT;
