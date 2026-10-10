-- Change only bounded window membership lookups in the existing allocator.
-- Preserve financial calculations, identity checks, and ordered window locks.
-- Check exact old fragments before replacing them. Fail on unexpected SQL.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

DO $migration$
DECLARE
    allocator_definition TEXT;
    old_lookup TEXT := $old$w.window_id IN (
              SELECT resolved.window_id
              FROM deltallm_accounting_permit_window_ids(p_generation,item) resolved
          )$old$;
    new_lookup TEXT := $new$w.window_id = ANY(ARRAY(
              SELECT resolved.window_id
              FROM deltallm_accounting_permit_window_ids(p_generation,item) resolved
          ))$new$;
    old_update TEXT := $old$w.window_id IN (
                    SELECT gw.window_id FROM deltallm_accounting_grant_windows gw
                    WHERE gw.grant_id=grant_id_value
                )$old$;
    new_update TEXT := $new$w.window_id = ANY(ARRAY(
                    SELECT gw.window_id FROM deltallm_accounting_grant_windows gw
                    WHERE gw.grant_id=grant_id_value
                ))$new$;
BEGIN
    SELECT pg_get_functiondef(
        'deltallm_accounting_ensure_grants_batch(bigint,text,integer,integer,jsonb)'::regprocedure
    ) INTO STRICT allocator_definition;
    IF (length(allocator_definition)-length(replace(allocator_definition,old_lookup,'')))
       <>3*length(old_lookup)
       OR (length(allocator_definition)-length(replace(allocator_definition,old_update,'')))
       <>length(old_update) THEN
        RAISE EXCEPTION 'accounting_allocator_window_keyset_definition';
    END IF;
    EXECUTE replace(replace(allocator_definition,old_lookup,new_lookup),old_update,new_update);
END;
$migration$;

COMMIT;
