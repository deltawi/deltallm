-- Preparation must not copy balances before accepted legacy work settles.
-- This check runs only during an operator-controlled, stopped-writer cutover.
BEGIN;

CREATE FUNCTION deltallm_accounting_pending_legacy_work()
RETURNS TABLE (lane TEXT) LANGUAGE SQL STABLE AS $$
    SELECT 'realtime'::text WHERE EXISTS (
        SELECT 1 FROM deltallm_realtime_billing_intents
        WHERE state IN ('dispatched','pending','accepted')
    )
    UNION ALL
    SELECT 'spend'::text WHERE EXISTS (
        SELECT 1 FROM deltallm_spend_ingestion_outbox WHERE status <> 'completed'
    )
    UNION ALL
    SELECT 'selector'::text WHERE EXISTS (
        SELECT 1 FROM deltallm_billing_operations
        WHERE accounting_protocol IS NULL AND closed_at IS NULL
    )
    UNION ALL
    SELECT 'batch'::text WHERE EXISTS (
        SELECT 1 FROM deltallm_batch_job
        WHERE status IN ('queued','in_progress','finalizing')
    ) OR EXISTS (
        SELECT 1 FROM deltallm_batch_completion_outbox WHERE status <> 'completed'
    );
$$;

CREATE OR REPLACE FUNCTION deltallm_activate_accounting_protocol_locked(p_generation BIGINT)
RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE
    protocol_row deltallm_accounting_protocols%ROWTYPE;
BEGIN
    IF EXISTS (SELECT 1 FROM deltallm_accounting_pending_legacy_work()) THEN
        RAISE EXCEPTION 'accounting_legacy_work_pending' USING ERRCODE = 'P0001';
    END IF;
    SELECT * INTO STRICT protocol_row FROM deltallm_accounting_protocols
        WHERE protocol_name='primary' AND generation=p_generation FOR UPDATE;
    IF protocol_row.state <> 'prepared' OR EXISTS (
        SELECT 1 FROM deltallm_accounting_protocols
        WHERE protocol_name='primary' AND state IN ('active','draining')
    ) OR (SELECT count(*) FROM deltallm_accounting_partitions
          WHERE protocol_name='primary' AND generation=p_generation)
          <> protocol_row.partition_count
       OR EXISTS (
          SELECT 1 FROM deltallm_accounting_partitions
          WHERE protocol_name='primary' AND generation=p_generation
            AND (max_outstanding<>protocol_row.max_outstanding_per_partition
                 OR outstanding_count<>0)
       ) THEN
        RAISE EXCEPTION 'accounting_protocol_not_ready' USING ERRCODE = 'P0001';
    END IF;
    UPDATE deltallm_accounting_protocols
        SET state='active',activated_at=NOW(),updated_at=NOW()
        WHERE protocol_name='primary' AND generation=p_generation;
END;
$$;

COMMIT;
