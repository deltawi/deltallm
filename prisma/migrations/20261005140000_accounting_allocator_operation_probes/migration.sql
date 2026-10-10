-- Count only the bounded request's missing operations with primary-key probes.
-- A flattened anti-join can scan retained operations under another join plan.
-- Preserve replay decisions, requested counts, money, and grant lock order.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

DO $migration$
DECLARE
    allocator_definition TEXT;
    old_probe TEXT := $old$FROM jsonb_array_elements(p_items) required
        WHERE deltallm_accounting_grant_subject(required)=subject_key_value
          AND NOT EXISTS (
              SELECT 1 FROM deltallm_billing_operations existing
              WHERE existing.operation_id=required->>'operation_id'
          )$old$;
    new_probe TEXT := $new$FROM jsonb_array_elements(p_items) required
        CROSS JOIN LATERAL (
            SELECT EXISTS (
                SELECT 1 FROM deltallm_billing_operations existing
                WHERE existing.operation_id=required->>'operation_id'
            ) AS is_replay OFFSET 0
        ) replay
        WHERE deltallm_accounting_grant_subject(required)=subject_key_value
          AND NOT replay.is_replay$new$;
BEGIN
    SELECT pg_get_functiondef(
        'deltallm_accounting_ensure_grants_batch(bigint,text,integer,integer,jsonb)'::regprocedure
    ) INTO STRICT allocator_definition;
    IF length(allocator_definition)-length(replace(allocator_definition,old_probe,''))
       <>length(old_probe) THEN
        RAISE EXCEPTION 'accounting_allocator_operation_probe_definition';
    END IF;
    EXECUTE replace(allocator_definition,old_probe,new_probe);
END;
$migration$;

COMMIT;
