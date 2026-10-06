SET lock_timeout = '2s';
SET statement_timeout = '30s';

ALTER TABLE deltallm_batch_item ADD COLUMN accounting_checkpoint JSONB;

-- Existing rows are null. NOT VALID avoids a table scan during expansion.
-- PostgreSQL still checks each new or changed row.
ALTER TABLE deltallm_batch_item ADD CONSTRAINT batch_accounting_checkpoint_bound
CHECK (
    accounting_checkpoint IS NULL OR (
        jsonb_typeof(accounting_checkpoint) = 'object'
        AND jsonb_typeof(accounting_checkpoint->'version') = 'number'
        AND accounting_checkpoint->>'version' = '1'
        AND jsonb_typeof(accounting_checkpoint->'operation') = 'object'
        AND accounting_checkpoint ? 'terminal'
        AND octet_length(accounting_checkpoint::text) <= 65536
    )
) NOT VALID;
