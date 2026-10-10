-- Checkpoint lease tokens are text; preserve their exact comparison value.
-- Keep the applied function migration and every financial record unchanged.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

DO $boundary$
DECLARE
    definition TEXT;
    fragment CONSTANT TEXT := 'c.lease_token=$4';
BEGIN
    SELECT pg_get_functiondef(
        'deltallm_accounting_recover_read_model_claim(text,bigint,text,uuid,integer)'::regprocedure
    ) INTO definition;
    IF position(fragment || '::text' IN definition) > 0 THEN
        RETURN;
    END IF;
    IF (length(definition)-length(replace(definition,fragment,''))) / length(fragment) <> 1 THEN
        RAISE EXCEPTION 'accounting_reporting_token_definition';
    END IF;
    EXECUTE replace(definition,fragment,fragment || '::text');
END;
$boundary$;

COMMIT;
