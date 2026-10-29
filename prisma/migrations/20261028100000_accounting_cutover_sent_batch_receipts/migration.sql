-- Batch completion receipts finish with status 'sent' ('completed' is never
-- written), so the cutover guard treated every delivered receipt as pending.
-- Only 'sent' is settled; queued, processing, retrying and failed rows still
-- block preparation and activation.
BEGIN;

CREATE OR REPLACE FUNCTION deltallm_accounting_pending_legacy_work()
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
        SELECT 1 FROM deltallm_batch_completion_outbox WHERE status <> 'sent'
    );
$$;

COMMIT;
