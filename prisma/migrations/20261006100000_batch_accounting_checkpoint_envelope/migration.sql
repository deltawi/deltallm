SET lock_timeout = '2s';
SET statement_timeout = '30s';

-- CHECK accepts null expressions. Require the envelope fields explicitly.
ALTER TABLE deltallm_batch_item ADD CONSTRAINT batch_accounting_checkpoint_envelope
CHECK (
    accounting_checkpoint IS NULL OR COALESCE(
        accounting_checkpoint ?& ARRAY['version','operation_id','claim_epoch','operation','terminal']
        AND jsonb_typeof(accounting_checkpoint->'version') = 'number'
        AND accounting_checkpoint->>'version' = '1'
        AND jsonb_typeof(accounting_checkpoint->'claim_epoch') = 'number'
        AND (accounting_checkpoint->>'claim_epoch')::numeric >= 1
        AND jsonb_typeof(accounting_checkpoint->'operation_id') = 'string'
        AND accounting_checkpoint->>'operation_id' ~
            '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        AND jsonb_typeof(accounting_checkpoint->'terminal') IN ('object','null'),
        FALSE
    )
) NOT VALID;
