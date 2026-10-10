-- Retain scope identity at funding, not once per provider request.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

ALTER TABLE deltallm_accounting_grants ADD COLUMN policy_attribution JSONB;
ALTER TABLE deltallm_accounting_grants ADD CONSTRAINT deltallm_accounting_grant_policy_attribution_shape
    CHECK (policy_attribution IS NULL OR
        (jsonb_typeof(policy_attribution)='object' AND octet_length(policy_attribution::text)<=2048
         AND policy_attribution ?& ARRAY['api_key','user_id','team_id','organization_id','model']
         AND jsonb_typeof(policy_attribution->'api_key')='string'
         AND jsonb_typeof(policy_attribution->'model')='string'));
CREATE INDEX deltallm_accounting_live_grant_policy_idx
    ON deltallm_accounting_grants USING gin(policy_attribution jsonb_path_ops)
    WHERE state IN ('active','draining');

DO $migration$
DECLARE
    definition TEXT;
    old_columns TEXT := 'accounting_partition,state,allocated_exact,operation_limit,expires_at';
    old_values TEXT := 'candidate_partition,''active'',grant_amount,grant_operation_limit,grant_expires_at';
    old_guard TEXT := $old$    IF EXISTS (
        SELECT 1 FROM deltallm_accounting_partitions p
        WHERE p.protocol_name='primary' AND p.generation=protocol_row.generation
          AND p.outstanding_count>0
    ) THEN$old$;
BEGIN
    SELECT pg_get_functiondef(
        'deltallm_accounting_ensure_grants_batch(bigint,text,integer,integer,jsonb)'::regprocedure
    ) INTO STRICT definition;
    IF (length(definition)-length(replace(definition,old_columns,'')))<>length(old_columns)
       OR (length(definition)-length(replace(definition,old_values,'')))<>length(old_values) THEN
        RAISE EXCEPTION 'accounting_grant_policy_attribution_definition';
    END IF;
    definition := replace(definition,old_columns,old_columns||',policy_attribution');
    definition := replace(definition,old_values,old_values||$values$,
                jsonb_build_object(
                    'api_key',item#>>'{attribution,api_key}',
                    'user_id',item#>>'{attribution,user_id}',
                    'team_id',item#>>'{attribution,team_id}',
                    'organization_id',item#>>'{attribution,organization_id}',
                    'model',item#>>'{attribution,model}'
                )$values$);
    EXECUTE definition;

    SELECT pg_get_functiondef(
        'deltallm_accounting_sync_budget(text,text,numeric,numeric,text,timestamp,jsonb)'::regprocedure
    ) INTO STRICT definition;
    IF position(old_guard IN definition)=0 THEN
        RAISE EXCEPTION 'accounting_scoped_budget_policy_fence_definition';
    END IF;
    definition := replace(definition,old_guard,$guard$    IF EXISTS (
        SELECT 1 FROM deltallm_accounting_grants g
        WHERE g.protocol_name='primary' AND g.generation=protocol_row.generation
          AND g.state IN ('active','draining')
          AND (g.policy_attribution IS NULL
               OR (p_scope_type='team_model' AND
                   (g.policy_attribution->>'team_id')||'/'||(g.policy_attribution->>'model')=p_scope_id)
               OR (p_scope_type<>'team_model' AND g.policy_attribution @>
                   jsonb_build_object(p_scope_type,p_scope_id)))
    ) OR EXISTS (
        SELECT 1 FROM deltallm_billing_operations b
        WHERE b.accounting_protocol='primary' AND b.accounting_generation=protocol_row.generation
          AND b.accounting_grant_id IS NULL AND b.accounting_state='reserved'
          AND CASE p_scope_type
              WHEN 'api_key' THEN b.api_key
              WHEN 'user' THEN b.user_id
              WHEN 'team' THEN b.team_id
              WHEN 'organization' THEN b.organization_id
              WHEN 'team_model' THEN b.team_id||'/'||b.model
          END=p_scope_id
    ) THEN$guard$);
    -- Attribution uses the reservation names, not the window scope names.
    definition := replace(definition,'jsonb_build_object(p_scope_type,p_scope_id)',
        'jsonb_build_object(CASE p_scope_type WHEN ''user'' THEN ''user_id'' WHEN ''team'' THEN ''team_id'' WHEN ''organization'' THEN ''organization_id'' ELSE p_scope_type END,p_scope_id)');
    EXECUTE definition;
END;
$migration$;

COMMIT;
