-- Keep projection source lookups bounded after repeated insert and settle cycles.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

DO $migration$
DECLARE
 definition TEXT;
 anchor TEXT := $old$BEGIN
 IF p_generation IS NULL OR p_generation<1$old$;
 replacement TEXT := $new$BEGIN
 IF NOT EXISTS (
  SELECT 1 FROM pg_index i
  WHERE i.indexrelid=to_regclass('deltallm_billing_operations_pkey')
   AND i.indisvalid AND i.indisready AND i.indisunique
 ) THEN
  RAISE EXCEPTION 'accounting_read_model_operation_index' USING ERRCODE='P0001';
 END IF;
 IF p_generation IS NULL OR p_generation<1$new$;
BEGIN
 SELECT pg_get_functiondef(
  'deltallm_accounting_project_read_models(bigint,text,uuid,integer,bigint,bigint[])'::regprocedure
 ) INTO STRICT definition;
 IF length(definition)-length(replace(definition,anchor,''))<>length(anchor) THEN
  RAISE EXCEPTION 'accounting_read_model_key_definition';
 END IF;
 EXECUTE replace(definition,anchor,replacement);
END;
$migration$;

-- These settings apply only inside this function, not to its caller or pool.
-- Each source document uses its complete primary key. The required index check
-- prevents a missing operation key from becoming a history scan.
ALTER FUNCTION deltallm_accounting_project_read_models(
 BIGINT,TEXT,UUID,INTEGER,BIGINT,BIGINT[]
) SET enable_seqscan=off;
ALTER FUNCTION deltallm_accounting_project_read_models(
 BIGINT,TEXT,UUID,INTEGER,BIGINT,BIGINT[]
) SET enable_bitmapscan=off;

COMMIT;
