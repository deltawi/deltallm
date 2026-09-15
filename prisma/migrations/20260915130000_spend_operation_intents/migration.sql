-- Inactive expansion: old rows keep NULL operation fields. Run once before rollout.
-- The recovery index requires a coordinated maintenance window (see PR6 design).
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';
ALTER TABLE deltallm_spend_ingestion_outbox
    ADD COLUMN operation_owner TEXT,
    ADD COLUMN operation_intent JSONB,
    ADD COLUMN operation_state TEXT,
    ADD COLUMN operation_expires_at TIMESTAMPTZ,
    ADD COLUMN operation_resolution JSONB,
    ADD CONSTRAINT deltallm_spend_operation_shape CHECK (
        (operation_owner IS NULL AND operation_intent IS NULL
            AND operation_state IS NULL AND operation_expires_at IS NULL
            AND operation_resolution IS NULL)
        OR (operation_owner IS NOT NULL AND length(operation_owner) = 36
            AND operation_intent IS NOT NULL AND jsonb_typeof(operation_intent) = 'object'
            AND octet_length(operation_intent::text) <= 65536
            AND operation_state IS NOT NULL
            AND operation_state IN ('dispatched', 'unknown', 'accepted')
            AND operation_expires_at IS NOT NULL
            AND (operation_state = 'accepted' OR status = 'blocked')
            AND (operation_resolution IS NULL OR (
                jsonb_typeof(operation_resolution) = 'object'
                AND octet_length(operation_resolution::text) <= 8192)))
    ) NOT VALID;
CREATE INDEX deltallm_spend_operation_recovery_idx
    ON deltallm_spend_ingestion_outbox(operation_expires_at, event_id)
    WHERE operation_state = 'dispatched';
COMMIT;
