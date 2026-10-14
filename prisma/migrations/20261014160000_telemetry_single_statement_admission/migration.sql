-- Required spend and audit admission serialize on one global advisory lock per
-- queue. Called as one statement outside an explicit transaction, each function
-- takes that lock and releases it at the statement's own commit, so no client
-- round trip (or stalled gateway process) runs while the lock is held.
-- PL/pgSQL statements in a VOLATILE function each read a new snapshot, so the
-- admission query still sees every earlier lock holder's committed count.
-- Called inside an explicit transaction, the locks last until that commit.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION deltallm_admit_spend_event(
    p_event_id TEXT,
    p_event_type TEXT,
    p_payload JSONB,
    p_max_attempts INTEGER,
    p_max_pending BIGINT
) RETURNS TABLE(duplicate BOOLEAN, accepted BOOLEAN, pending_count BIGINT)
LANGUAGE plpgsql VOLATILE AS $$
#variable_conflict use_column
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('deltallm:spend-ingestion-capacity', 0));
    RETURN QUERY
    WITH existing AS MATERIALIZED (
        SELECT 1
        FROM deltallm_spend_ingestion_outbox
        WHERE event_id = p_event_id
    ),
    capacity AS MATERIALIZED (
        SELECT pending_count
        FROM deltallm_telemetry_ingestion_capacity
        WHERE queue_name = 'spend'
    ),
    inserted AS (
        INSERT INTO deltallm_spend_ingestion_outbox (
            event_id, event_type, payload_json, max_attempts
        )
        SELECT p_event_id, p_event_type, p_payload, p_max_attempts
        FROM capacity
        WHERE pending_count < p_max_pending
          AND NOT EXISTS (SELECT 1 FROM existing)
        ON CONFLICT (event_id) DO NOTHING
        RETURNING event_id
    ),
    bumped AS (
        UPDATE deltallm_telemetry_ingestion_capacity
        SET pending_count = pending_count + 1,
            updated_at = NOW()
        WHERE queue_name = 'spend'
          AND EXISTS (SELECT 1 FROM inserted)
        RETURNING pending_count
    )
    SELECT
        EXISTS (SELECT 1 FROM existing),
        EXISTS (SELECT 1 FROM inserted),
        COALESCE(
            (SELECT pending_count FROM bumped),
            (SELECT pending_count FROM capacity),
            0
        )::BIGINT;
END;
$$;

CREATE OR REPLACE FUNCTION deltallm_begin_spend_operation(
    p_event_id TEXT,
    p_owner TEXT,
    p_intent JSONB,
    p_expires_at TIMESTAMPTZ,
    p_max_attempts INTEGER,
    p_capacity BIGINT,
    p_previous JSONB,
    p_attempt_count INTEGER,
    p_lock_timeout_ms INTEGER
) RETURNS TABLE(accepted BOOLEAN, pending_count BIGINT)
LANGUAGE plpgsql VOLATILE AS $$
#variable_conflict use_column
BEGIN
    PERFORM set_config('lock_timeout', GREATEST(1, p_lock_timeout_ms)::TEXT || 'ms', true);
    PERFORM pg_advisory_xact_lock(hashtextextended('deltallm:spend-ingestion-capacity', 0));
    RETURN QUERY
    WITH existing AS MATERIALIZED (
        SELECT event_id FROM deltallm_spend_ingestion_outbox WHERE event_id = p_event_id
    ), settled AS MATERIALIZED (
        SELECT id FROM deltallm_spendlog_events WHERE id = p_event_id
    ), capacity AS MATERIALIZED (
        SELECT pending_count FROM deltallm_telemetry_ingestion_capacity WHERE queue_name = 'spend'
    ), inserted AS (
        INSERT INTO deltallm_spend_ingestion_outbox (
            event_id, event_type, payload_json, max_attempts, status, blocked_at, last_error,
            operation_owner, operation_intent, operation_state, operation_expires_at
        )
        SELECT p_event_id, 'spend', (p_intent->'principal') || jsonb_build_object(
            'model', p_intent->>'model', 'call_type', p_intent->>'call_type'
        ), p_max_attempts, 'blocked', NOW(), 'operation_receipt_pending', p_owner, p_intent,
            'dispatched', p_expires_at
        FROM capacity WHERE pending_count < p_capacity AND p_attempt_count = 1
            AND NOT EXISTS (SELECT 1 FROM existing) AND NOT EXISTS (SELECT 1 FROM settled)
        ON CONFLICT (event_id) DO NOTHING RETURNING event_id
    ), advanced AS (
        UPDATE deltallm_spend_ingestion_outbox SET operation_intent = p_intent, updated_at = NOW()
        WHERE event_id = p_event_id AND operation_owner = p_owner
            AND operation_state = 'dispatched'
            AND operation_intent = p_previous AND p_attempt_count > 1
            AND NOT EXISTS (SELECT 1 FROM settled)
        RETURNING event_id
    ), bumped AS (
        UPDATE deltallm_telemetry_ingestion_capacity
        SET pending_count = pending_count + 1, updated_at = NOW()
        WHERE queue_name = 'spend' AND EXISTS (SELECT 1 FROM inserted)
        RETURNING pending_count
    )
    SELECT (EXISTS (SELECT 1 FROM inserted) OR EXISTS (SELECT 1 FROM advanced)),
        COALESCE((SELECT pending_count FROM bumped), (SELECT pending_count FROM capacity))::BIGINT;
END;
$$;

-- Frees a first-attempt reservation whose caller failed before any provider
-- call. The queue lock orders this behind an admission that is still running.
CREATE OR REPLACE FUNCTION deltallm_release_spend_operation(
    p_event_id TEXT,
    p_owner TEXT,
    p_intent JSONB
) RETURNS BOOLEAN
LANGUAGE plpgsql VOLATILE AS $$
DECLARE
    removed BOOLEAN;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('deltallm:spend-ingestion-capacity', 0));
    WITH released AS (
        DELETE FROM deltallm_spend_ingestion_outbox
        WHERE event_id = p_event_id AND operation_owner = p_owner
            AND operation_state = 'dispatched' AND operation_intent = p_intent
        RETURNING event_id
    ), adjusted AS (
        UPDATE deltallm_telemetry_ingestion_capacity
        SET pending_count = GREATEST(0, pending_count - (SELECT COUNT(*) FROM released)),
            updated_at = NOW()
        WHERE queue_name = 'spend' AND EXISTS (SELECT 1 FROM released)
        RETURNING pending_count
    )
    SELECT EXISTS (SELECT 1 FROM released) INTO removed;
    RETURN removed;
END;
$$;

CREATE OR REPLACE FUNCTION deltallm_admit_audit_bundle(
    p_envelopes JSONB,
    p_organization_id TEXT,
    p_max_pending BIGINT,
    p_required_reserve BIGINT
) RETURNS TABLE(event_id TEXT, status TEXT, pending_count BIGINT)
LANGUAGE plpgsql VOLATILE AS $$
#variable_conflict use_column
BEGIN
    -- Queue lock before the organization policy lock, as everywhere else.
    PERFORM pg_advisory_xact_lock(hashtextextended('deltallm:audit-ingestion-capacity', 0));
    IF p_organization_id IS NOT NULL THEN
        PERFORM pg_advisory_xact_lock_shared(
            hashtextextended('deltallm:audit-content-policy:' || p_organization_id, 0)
        );
    END IF;
    RETURN QUERY
    WITH input AS MATERIALIZED (
        SELECT
            item->>'event_id' AS event_id,
            item->>'record_type' AS record_type,
            NULLIF(item->>'organization_id', '') AS organization_id,
            item->>'delivery_class' AS delivery_class,
            item->'payload' AS payload,
            item->'redacted_payload' AS redacted_payload,
            (item->>'max_attempts')::INT AS max_attempts
        FROM jsonb_array_elements(p_envelopes) AS source(item)
    ),
    existing AS MATERIALIZED (
        SELECT input.event_id
        FROM input
        JOIN deltallm_audit_ingestion_outbox existing
          ON existing.event_id = input.event_id
    ),
    capacity AS MATERIALIZED (
        SELECT pending_count
        FROM deltallm_telemetry_ingestion_capacity
        WHERE queue_name = 'audit'
    ),
    policy AS MATERIALIZED (
        SELECT
            COALESCE(o.audit_content_storage_enabled, FALSE) AS enabled,
            COALESCE(o.audit_content_policy_version, 0)::BIGINT AS version
        FROM (VALUES (1)) AS singleton(value)
        LEFT JOIN deltallm_organizationtable o ON o.organization_id = p_organization_id
        LIMIT 1
    ),
    new_rows AS MATERIALIZED (
        SELECT input.*
        FROM input
        LEFT JOIN existing USING (event_id)
        WHERE existing.event_id IS NULL
    ),
    required_admitted AS MATERIALIZED (
        SELECT new_rows.*
        FROM new_rows, capacity
        WHERE delivery_class = 'required'
        ORDER BY event_id
        LIMIT GREATEST(0, p_max_pending - (SELECT pending_count FROM capacity))
    ),
    best_effort_admitted AS MATERIALIZED (
        SELECT new_rows.*
        FROM new_rows, capacity
        WHERE delivery_class = 'best_effort'
        ORDER BY event_id
        LIMIT GREATEST(
            0,
            p_max_pending - p_required_reserve - (SELECT pending_count FROM capacity)
            - (SELECT COUNT(*) FROM required_admitted)
        )
    ),
    admitted AS MATERIALIZED (
        SELECT * FROM required_admitted
        UNION ALL
        SELECT * FROM best_effort_admitted
    ),
    inserted AS (
        INSERT INTO deltallm_audit_ingestion_outbox (
            event_id, record_type, organization_id, delivery_class,
            payload_json, redacted_payload_json, policy_version, max_attempts
        )
        SELECT
            admitted.event_id,
            admitted.record_type,
            admitted.organization_id,
            admitted.delivery_class,
            CASE WHEN policy.enabled THEN admitted.payload ELSE admitted.redacted_payload END,
            admitted.redacted_payload,
            policy.version,
            admitted.max_attempts
        FROM admitted
        CROSS JOIN policy
        ORDER BY admitted.event_id
        ON CONFLICT (event_id) DO NOTHING
        RETURNING event_id
    ),
    bumped AS (
        UPDATE deltallm_telemetry_ingestion_capacity
        SET pending_count = pending_count + (SELECT COUNT(*) FROM inserted),
            updated_at = NOW()
        WHERE queue_name = 'audit'
          AND EXISTS (SELECT 1 FROM inserted)
        RETURNING pending_count
    )
    SELECT
        input.event_id,
        CASE
            WHEN existing.event_id IS NOT NULL THEN 'duplicate'
            WHEN inserted.event_id IS NOT NULL THEN 'accepted'
            ELSE 'full'
        END,
        COALESCE(
            (SELECT pending_count FROM bumped),
            (SELECT pending_count FROM capacity),
            0
        )::BIGINT
    FROM input
    LEFT JOIN existing USING (event_id)
    LEFT JOIN inserted USING (event_id)
    ORDER BY input.event_id;
END;
$$;
