DO $verify$
BEGIN
    IF EXISTS (SELECT 1 FROM deltallm_spend_ingestion_outbox WHERE event_id LIKE 'pr6-upgrade-%') THEN
        IF (SELECT count(*) FROM deltallm_spend_ingestion_outbox WHERE event_id IN ('pr6-upgrade-queued','pr6-upgrade-blocked')
            AND operation_owner IS NULL AND operation_intent IS NULL AND operation_state IS NULL
            AND operation_expires_at IS NULL AND operation_resolution IS NULL
            AND payload_json->>'model'='upgrade') <> 2 THEN
            RAISE EXCEPTION 'PR6 expansion changed accepted legacy work';
        END IF;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_index WHERE indexrelid='deltallm_spend_operation_recovery_idx'::regclass AND indisvalid) THEN
        RAISE EXCEPTION 'PR6 recovery index is not valid';
    END IF;
    INSERT INTO deltallm_spend_ingestion_outbox(event_id,event_type,payload_json,status,
        operation_owner,operation_intent,operation_state,operation_expires_at)
    VALUES ('pr6-shape-probe','spend','{}','blocked','00000000-0000-0000-0000-000000000001','{}','unknown',NOW());
    BEGIN
        UPDATE deltallm_spend_ingestion_outbox SET status='retry' WHERE event_id='pr6-shape-probe';
        RAISE EXCEPTION 'PR6 unresolved intent was replayable';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
    DELETE FROM deltallm_spend_ingestion_outbox WHERE event_id='pr6-shape-probe';
END;
$verify$;
