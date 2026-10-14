-- Capacity reconciliation runs when a replica starts its spend or audit worker.
-- It used to hold the global queue admission lock across four client round trips
-- and a full outbox count, so a slow or stalled starting replica held up required
-- admission on every other replica. Each function now takes the same queue lock,
-- locks the capacity row, counts and rewrites it in one self-committing statement.
-- PL/pgSQL statements in a VOLATILE function each read a new snapshot, so the
-- count still sees every admission and completion committed before the lock.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION deltallm_reconcile_spend_capacity() RETURNS BIGINT
LANGUAGE plpgsql VOLATILE AS $$
DECLARE
    actual BIGINT;
    reconciled BIGINT;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('deltallm:spend-ingestion-capacity', 0));
    PERFORM 1
    FROM deltallm_telemetry_ingestion_capacity
    WHERE queue_name = 'spend'
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'spend ingestion capacity row is missing';
    END IF;
    SELECT COUNT(*) INTO actual
    FROM deltallm_spend_ingestion_outbox
    WHERE status IN ('queued', 'retry', 'processing', 'blocked', 'failed');
    UPDATE deltallm_telemetry_ingestion_capacity
    SET pending_count = actual, updated_at = NOW()
    WHERE queue_name = 'spend'
    RETURNING pending_count INTO reconciled;
    RETURN reconciled;
END;
$$;

CREATE OR REPLACE FUNCTION deltallm_reconcile_audit_capacity() RETURNS BIGINT
LANGUAGE plpgsql VOLATILE AS $$
DECLARE
    actual BIGINT;
    reconciled BIGINT;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('deltallm:audit-ingestion-capacity', 0));
    PERFORM 1
    FROM deltallm_telemetry_ingestion_capacity
    WHERE queue_name = 'audit'
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'audit ingestion capacity row is missing';
    END IF;
    SELECT COUNT(*) INTO actual
    FROM deltallm_audit_ingestion_outbox
    WHERE status IN ('queued', 'retry', 'processing', 'blocked')
       OR (status = 'failed' AND delivery_class = 'required');
    UPDATE deltallm_telemetry_ingestion_capacity
    SET pending_count = actual, updated_at = NOW()
    WHERE queue_name = 'audit'
    RETURNING pending_count INTO reconciled;
    RETURN reconciled;
END;
$$;
