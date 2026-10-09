-- Five existing 256-character identities can exceed 2 KiB with Unicode or
-- JSON escaping. Preserve that contract within a fixed 8 KiB grant bound.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

ALTER TABLE deltallm_accounting_grants
    DROP CONSTRAINT deltallm_accounting_grant_policy_attribution_shape;
ALTER TABLE deltallm_accounting_grants
    ADD CONSTRAINT deltallm_accounting_grant_policy_attribution_shape CHECK (
        policy_attribution IS NULL OR
        (jsonb_typeof(policy_attribution)='object' AND octet_length(policy_attribution::text)<=8192
         AND policy_attribution ?& ARRAY['api_key','user_id','team_id','organization_id','model']
         AND jsonb_typeof(policy_attribution->'api_key')='string'
         AND jsonb_typeof(policy_attribution->'model')='string')
    );

COMMIT;
