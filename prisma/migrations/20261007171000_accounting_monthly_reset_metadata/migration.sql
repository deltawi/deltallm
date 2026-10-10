-- Keep the monthly anchor when older metadata has a non-object reset value.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

DO $migration$
DECLARE
    definition TEXT;
    marker TEXT := $marker$COALESCE(NEW.metadata->'_budget_reset','{}'::jsonb)$marker$;
    replacement TEXT := $replacement$CASE
                    WHEN jsonb_typeof(NEW.metadata->'_budget_reset')='object'
                    THEN NEW.metadata->'_budget_reset' ELSE '{}'::jsonb END$replacement$;
BEGIN
    SELECT pg_get_functiondef(
        'deltallm_accounting_budget_period_trigger()'::regprocedure
    ) INTO STRICT definition;
    IF (length(definition)-length(replace(definition,marker,'')))<>length(marker) THEN
        RAISE EXCEPTION 'accounting_monthly_reset_metadata_definition';
    END IF;
    EXECUTE replace(definition,marker,replacement);
END;
$migration$;

COMMIT;
