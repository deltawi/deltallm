-- Short grant calls must not spend their statement budget on JIT compilation.
-- Function settings restore the caller's policy after success or error.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

ALTER FUNCTION deltallm_accounting_admit_grant_batch(
 BIGINT,TEXT,INTEGER,INTEGER,JSONB
) SET jit=off;
ALTER FUNCTION deltallm_accounting_ensure_grants_batch(
 BIGINT,TEXT,INTEGER,INTEGER,JSONB
) SET jit=off;
ALTER FUNCTION deltallm_accounting_reserve_grant_batch(
 BIGINT,TEXT,INTEGER,INTEGER,JSONB
) SET jit=off;
ALTER FUNCTION deltallm_accounting_finalize_grant_batch(BIGINT,JSONB) SET jit=off;

COMMIT;
