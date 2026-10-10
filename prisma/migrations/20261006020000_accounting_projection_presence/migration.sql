-- Fixed health slots prove projection presence without scanning event history.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

CREATE TABLE deltallm_accounting_projection_presence (
    protocol_name TEXT NOT NULL DEFAULT 'primary',
    generation BIGINT NOT NULL,
    slot INTEGER NOT NULL CHECK (slot BETWEEN 0 AND 63),
    owner_token UUID,
    expires_at TIMESTAMPTZ NOT NULL DEFAULT '1970-01-01 00:00:00+00',
    ready BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (protocol_name,generation,slot),
    FOREIGN KEY (protocol_name,generation)
        REFERENCES deltallm_accounting_protocols(protocol_name,generation)
        ON DELETE CASCADE ON UPDATE RESTRICT,
    CHECK (NOT ready OR owner_token IS NOT NULL),
    CHECK (isfinite(expires_at))
);

COMMIT;
