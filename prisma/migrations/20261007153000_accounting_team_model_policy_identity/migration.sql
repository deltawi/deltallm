-- Team/model window identities use the existing colon delimiter.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

DO $migration$
DECLARE
    definition TEXT;
    grant_identity TEXT := '(g.policy_attribution->>''team_id'')||''/''||(g.policy_attribution->>''model'')';
    direct_identity TEXT := 'b.team_id||''/''||b.model';
BEGIN
    SELECT pg_get_functiondef(
        'deltallm_accounting_sync_budget(text,text,numeric,numeric,text,timestamp,jsonb)'::regprocedure
    ) INTO STRICT definition;
    IF position(grant_identity IN definition)=0 OR position(direct_identity IN definition)=0 THEN
        RAISE EXCEPTION 'accounting_team_model_policy_identity_definition';
    END IF;
    definition := replace(definition,grant_identity,replace(grant_identity,'''/''',''':'''));
    definition := replace(definition,direct_identity,replace(direct_identity,'''/''',''':'''));
    EXECUTE definition;
END;
$migration$;

COMMIT;
