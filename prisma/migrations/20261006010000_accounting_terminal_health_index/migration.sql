-- Index the oldest retained terminal work without scanning settled receipts.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

CREATE INDEX deltallm_accounting_terminal_oldest_work_idx
    ON deltallm_accounting_terminal_journal(generation,accepted_at,sequence)
    WHERE protocol_name='primary' AND status<>'completed';

COMMIT;

