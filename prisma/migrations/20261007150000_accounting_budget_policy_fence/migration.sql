-- A new scope must not omit capacity granted before that scope existed.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

DO $migration$
DECLARE
    definition TEXT;
    marker TEXT := '    SELECT COALESCE(max(policy_generation),-1)+1 INTO next_policy_generation';
BEGIN
    SELECT pg_get_functiondef(
        'deltallm_accounting_sync_budget(text,text,numeric,numeric,text,timestamp,jsonb)'::regprocedure
    ) INTO STRICT definition;
    IF position(marker IN definition)=0
       OR (length(definition)-length(replace(definition,'FOR SHARE;','')))<>length('FOR SHARE;') THEN
        RAISE EXCEPTION 'accounting_budget_policy_fence_definition';
    END IF;
    -- Refills and direct reservations take a shared protocol-row lock. The
    -- exclusive lock closes the race between this check and a new allocation.
    definition := replace(definition,'FOR SHARE;','FOR UPDATE;');
    definition := replace(definition,marker,$guard$
    IF EXISTS (
        SELECT 1 FROM deltallm_accounting_partitions p
        WHERE p.protocol_name='primary' AND p.generation=protocol_row.generation
          AND p.outstanding_count>0
    ) THEN
        RAISE EXCEPTION 'accounting_budget_policy_requires_drain' USING ERRCODE='55000';
    END IF;

$guard$ || marker);
    EXECUTE definition;
END;
$migration$;

COMMIT;
