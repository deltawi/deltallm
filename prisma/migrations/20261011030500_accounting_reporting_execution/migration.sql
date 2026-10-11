-- Short reporting calls do not need runtime compilation.
-- Keep custom plans and restore the caller's JIT setting on return or error.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

ALTER FUNCTION deltallm_accounting_claim_read_model(TEXT,BIGINT,TEXT,UUID,INTEGER,INTEGER)
    SET jit=off;
ALTER FUNCTION deltallm_accounting_read_model_progress(TEXT,BIGINT)
    SET jit=off;
ALTER FUNCTION deltallm_accounting_recover_read_model_claim(TEXT,BIGINT,TEXT,UUID,INTEGER)
    SET jit=off;

COMMIT;
