BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

-- Existing counters keep their existing authority. The constant default is a
-- PostgreSQL fast default; no event history or counter-table rewrite is needed.
ALTER TABLE deltallm_teammodelspend
    ADD COLUMN reconciled_at TIMESTAMP(3) DEFAULT CURRENT_TIMESTAMP;
-- A newly created/recreated counter contains only subsequent deltas. Neither
-- old nor new ledger writers may mistake that partial value for complete history.
ALTER TABLE deltallm_teammodelspend ALTER COLUMN reconciled_at DROP DEFAULT;

COMMIT;
