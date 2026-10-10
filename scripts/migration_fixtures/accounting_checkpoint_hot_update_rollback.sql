-- Restore the old write cost without changing any checkpoint, lease, or fact.
-- Run through the coordinated migration workflow, not beside active rollout.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

DO $rollback$
BEGIN
    IF to_regclass('deltallm_accounting_projection_lease_idx') IS NOT NULL THEN
        RAISE EXCEPTION 'accounting_checkpoint_lease_index_already_present' USING ERRCODE='P0001';
    END IF;
END;
$rollback$;

CREATE INDEX deltallm_accounting_projection_lease_idx
    ON deltallm_accounting_projection_checkpoints(lease_expires_at);
COMMIT;
