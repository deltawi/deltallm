SET lock_timeout = '5s';
SET statement_timeout = '30s';

ALTER TABLE deltallm_batch_item
    ADD COLUMN selector_checkpoint JSONB,
    ADD CONSTRAINT deltallm_batch_selector_checkpoint_bound CHECK (
        selector_checkpoint IS NULL OR (
            jsonb_typeof(selector_checkpoint) = 'object'
            AND octet_length(selector_checkpoint::text) <= 4096
            AND selector_checkpoint->'version' = '1'::jsonb
            AND selector_checkpoint ?& ARRAY[
                'operation_id', 'input_fingerprint', 'model_group', 'policy_identity', 'decision'
            ]
        ) IS TRUE
    ) NOT VALID;

RESET statement_timeout;
RESET lock_timeout;
