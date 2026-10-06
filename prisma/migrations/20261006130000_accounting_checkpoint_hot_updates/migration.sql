-- Fixed checkpoint keys already bound lease discovery. Indexing each changing
-- lease expiry prevents heap-only updates on this high-frequency state owner.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

DO $migration$
DECLARE
    definition TEXT;
BEGIN
    SELECT pg_get_indexdef(i.indexrelid) INTO definition FROM pg_index i
    WHERE i.indexrelid=to_regclass('deltallm_accounting_projection_lease_idx')
      AND i.indrelid='deltallm_accounting_projection_checkpoints'::regclass
      AND NOT i.indisunique AND i.indisvalid AND i.indpred IS NULL;
    IF definition IS DISTINCT FROM 'CREATE INDEX deltallm_accounting_projection_lease_idx ON public.deltallm_accounting_projection_checkpoints USING btree (lease_expires_at)' THEN
        RAISE EXCEPTION 'accounting_checkpoint_lease_index_shape' USING ERRCODE='P0001';
    END IF;
END;
$migration$;

DROP INDEX deltallm_accounting_projection_lease_idx;
COMMIT;
