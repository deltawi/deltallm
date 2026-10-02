-- Repeated native Realtime turns extend the existing spend outbox/ledger owner.
-- No media, transcripts, credentials or provider request bodies are retained.
CREATE TABLE deltallm_realtime_billing_intents (
    operation_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    snapshot JSONB NOT NULL CHECK (octet_length(snapshot::text) <= 32768),
    state TEXT NOT NULL DEFAULT 'dispatched'
        CHECK (state IN ('dispatched','pending','accepted','settled')),
    event_id TEXT UNIQUE,
    receipt_facts JSONB,
    spend_payload JSONB CHECK (octet_length(spend_payload::text) <= 32768),
    pending_reason TEXT,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (expires_at > created_at AND expires_at <= created_at + INTERVAL '65 minutes'),
    CHECK (state NOT IN ('accepted','settled') OR
        (event_id IS NOT NULL AND receipt_facts IS NOT NULL AND spend_payload IS NOT NULL))
);
CREATE INDEX deltallm_realtime_billing_intents_recovery_idx
    ON deltallm_realtime_billing_intents (state, updated_at, operation_id);
CREATE INDEX deltallm_realtime_billing_intents_expiry_idx
    ON deltallm_realtime_billing_intents (state, expires_at, operation_id);
CREATE INDEX deltallm_realtime_billing_intents_session_idx
    ON deltallm_realtime_billing_intents (session_id);
INSERT INTO deltallm_telemetry_ingestion_capacity(queue_name,pending_count)
    VALUES ('realtime_billing',0) ON CONFLICT (queue_name) DO NOTHING;
