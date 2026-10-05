-- Promote the one locked grant by its materialized primary key.
-- A FROM join can scan closed grants instead of probing this single candidate.
-- Preserve selection order, zero-consumption checks, and the funded grant amount.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

DO $migration$
DECLARE
    allocator_definition TEXT;
    old_probe TEXT := $old$FROM candidate
        WHERE g.grant_id=candidate.grant_id$old$;
    new_probe TEXT := $new$WHERE g.grant_id = ANY(ARRAY(
            SELECT candidate.grant_id FROM candidate
        ))$new$;
BEGIN
    SELECT pg_get_functiondef(
        'deltallm_accounting_allocate_permit_grant(bigint,text,uuid,integer,integer,jsonb)'
        ::regprocedure
    ) INTO STRICT allocator_definition;
    IF length(allocator_definition)-length(replace(allocator_definition,old_probe,''))
       <>length(old_probe) THEN
        RAISE EXCEPTION 'accounting_permit_promotion_keyset_definition';
    END IF;
    EXECUTE replace(allocator_definition,old_probe,new_probe);
END;
$migration$;

COMMIT;
