-- These short key-bounded functions must not compile an expensive JIT plan.
-- Function settings restore the caller's policy after success or error.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

ALTER FUNCTION deltallm_accounting_allocate_local_permit_grants_batch(
 BIGINT,TEXT,INTEGER,JSONB
) SET jit=off;
ALTER FUNCTION deltallm_accounting_backlog_snapshot(BIGINT) SET jit=off;
ALTER FUNCTION deltallm_accounting_project_read_models(
 BIGINT,TEXT,UUID,INTEGER,BIGINT,BIGINT[]
) SET jit=off;

COMMIT;
