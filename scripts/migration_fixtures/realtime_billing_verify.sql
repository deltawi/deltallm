-- Check both fresh and upgraded schemas without leaving billing fixtures behind.
BEGIN;
DO $realtime_verify$
BEGIN
    IF to_regclass('deltallm_realtime_billing_intents') IS NULL
        OR NOT EXISTS (
            SELECT 1 FROM deltallm_telemetry_ingestion_capacity
            WHERE queue_name='realtime_billing' AND pending_count=0
        ) THEN
        RAISE EXCEPTION 'Realtime journal or capacity seed is missing';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes WHERE indexname='deltallm_realtime_billing_intents_expiry_idx'
    ) THEN
        RAISE EXCEPTION 'Realtime expiry recovery index is missing';
    END IF;
    BEGIN
        INSERT INTO deltallm_realtime_billing_intents(operation_id,session_id,snapshot,state,expires_at)
        VALUES ('invalid-accepted','verify','{}','accepted',NOW()+INTERVAL '1 minute');
        RAISE EXCEPTION 'Accepted Realtime usage must require authoritative receipt facts';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
    BEGIN
        INSERT INTO deltallm_realtime_billing_intents(operation_id,session_id,snapshot,expires_at)
        VALUES ('invalid-lifetime','verify','{}',NOW()+INTERVAL '1 day');
        RAISE EXCEPTION 'Realtime intent lifetime must be bounded';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
    INSERT INTO deltallm_realtime_billing_intents(operation_id,session_id,snapshot,event_id,expires_at)
    VALUES ('first-receipt','verify','{}','one-receipt',NOW()+INTERVAL '1 minute');
    BEGIN
        INSERT INTO deltallm_realtime_billing_intents(operation_id,session_id,snapshot,event_id,expires_at)
        VALUES ('duplicate-receipt','verify','{}','one-receipt',NOW()+INTERVAL '1 minute');
        RAISE EXCEPTION 'A Realtime receipt must have only one billing intent';
    EXCEPTION WHEN unique_violation THEN NULL;
    END;
END
$realtime_verify$;
ROLLBACK;
