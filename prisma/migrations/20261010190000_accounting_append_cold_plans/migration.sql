-- Replan terminal append with current values instead of an empty-table plan.
-- Keep all SQL validations, locks, foreign keys, and monetary effects unchanged.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

ALTER FUNCTION deltallm_accounting_append_terminal_journal(
    BIGINT,JSONB,TEXT[],TEXT[]
) SET plan_cache_mode=force_custom_plan;

COMMIT;
