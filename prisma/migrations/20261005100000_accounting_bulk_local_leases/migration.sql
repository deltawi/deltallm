-- Fences, operations, and grants are immutable request keys.
-- Keep each JSON-batch lookup indexed instead of scanning retained history.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

CREATE FUNCTION deltallm_accounting_allocate_local_permit_grants_batch(
    p_generation BIGINT,p_owner_id TEXT,p_ttl_seconds INTEGER,p_items JSONB
) RETURNS TABLE(
    allocation_fence_token UUID,decision TEXT,grant_id TEXT,grantee_id TEXT,
    fence_token UUID,accounting_partition INTEGER,allowance_exact NUMERIC,
    operation_limit INTEGER,expires_at TIMESTAMPTZ,
    dispatch_expires_at TIMESTAMPTZ,observed_at TIMESTAMPTZ
)
LANGUAGE plpgsql AS $$
DECLARE
    item JSONB;
    protocol_state TEXT;
BEGIN
    IF COALESCE(length(p_owner_id),0) NOT BETWEEN 1 AND 219
       OR COALESCE(p_ttl_seconds,0) NOT BETWEEN 1 AND 300
       OR COALESCE(jsonb_typeof(p_items),'null')<>'array' THEN
        RAISE EXCEPTION 'accounting_permit_refill_batch_shape' USING ERRCODE='P0001';
    END IF;
    IF jsonb_array_length(p_items) NOT BETWEEN 1 AND 256
       OR octet_length(p_items::text)>2097152 THEN
        RAISE EXCEPTION 'accounting_permit_refill_batch_shape' USING ERRCODE='P0001';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_items) value
        WHERE COALESCE(jsonb_typeof(value->'reservation'),'null')<>'object'
          OR COALESCE((value->>'target_operations')::integer,0) NOT BETWEEN 1 AND 1024
          OR value->>'fence_token' IS NULL
          OR (value#>>'{reservation,protocol_generation}')::bigint IS DISTINCT FROM p_generation
          OR COALESCE(jsonb_typeof(value#>'{reservation,windows}'),'null')<>'array'
    ) OR EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_items) value
        GROUP BY (value->>'fence_token')::uuid HAVING count(*)>1
    ) THEN
        RAISE EXCEPTION 'accounting_permit_refill_item_shape' USING ERRCODE='P0001';
    END IF;

    SELECT state INTO STRICT protocol_state FROM deltallm_accounting_protocols
        WHERE protocol_name='primary' AND generation=p_generation AND writer_version=2
        FOR SHARE;
    IF protocol_state<>'active' THEN
        RAISE EXCEPTION 'accounting_protocol_not_active' USING ERRCODE='P0001';
    END IF;
    -- Serialize retries by the immutable fence before any grant or window lock.
    FOR item IN SELECT value FROM jsonb_array_elements(p_items) value
                ORDER BY (value->>'fence_token')::uuid
    LOOP
        PERFORM pg_advisory_xact_lock(hashtextextended(
            'accounting-permit-refill:'||(item->>'fence_token')::uuid::text,0
        ));
    END LOOP;
    PERFORM 1 FROM deltallm_accounting_grants g
        WHERE g.fence_token=ANY(ARRAY(SELECT (value->>'fence_token')::uuid FROM jsonb_array_elements(p_items) value)) ORDER BY g.grant_id FOR UPDATE;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_items) value
        CROSS JOIN LATERAL (
            SELECT g.* FROM deltallm_accounting_grants g
            WHERE g.fence_token=(value->>'fence_token')::uuid OFFSET 0
        ) g
        WHERE g.protocol_name<>'primary' OR g.generation<>p_generation
          OR g.grantee_id<>p_owner_id||':'||g.fence_token::text
          OR g.subject_key<>deltallm_accounting_grant_subject(value->'reservation')
          OR g.unit_allowance_exact IS DISTINCT FROM
             (value#>>'{reservation,allowance}')::numeric
          OR g.dispatch_mode<>'preissued' OR NOT g.local_dispatch OR g.state<>'active'
          OR g.consumed_operations<>0 OR g.returned_operations<>0
          OR g.dispatch_expires_at<=CURRENT_TIMESTAMP
          OR g.expires_at<=CURRENT_TIMESTAMP
    ) THEN
        RAISE EXCEPTION 'accounting_permit_refill_identity_conflict' USING ERRCODE='P0001';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_items) value
        WHERE (SELECT count(*) FROM deltallm_accounting_permit_window_ids(
            p_generation,value->'reservation'
        ))>8
    ) THEN
        RAISE EXCEPTION 'accounting_permit_refill_window_capacity' USING ERRCODE='P0001';
    END IF;
    -- All windows are locked once in global order, not once per subject.
    PERFORM 1 FROM deltallm_accounting_budget_windows w
        WHERE w.window_id IN (
            SELECT resolved.window_id FROM jsonb_array_elements(p_items) value
            CROSS JOIN LATERAL deltallm_accounting_permit_window_ids(
                p_generation,value->'reservation'
            ) resolved
        ) ORDER BY w.window_id FOR UPDATE;
    FOR item IN SELECT value FROM jsonb_array_elements(p_items) value
                ORDER BY (value->>'fence_token')::uuid
    LOOP
        RETURN QUERY SELECT (item->>'fence_token')::uuid,allocated.*,CURRENT_TIMESTAMP
        FROM deltallm_accounting_allocate_local_permit_grant(
            p_generation,p_owner_id||':'||(item->>'fence_token')::uuid::text,
            (item->>'fence_token')::uuid,(item->>'target_operations')::integer,
            p_ttl_seconds,item->'reservation'
        ) allocated;
    END LOOP;
END;
$$;

-- The owner can retire several grants in one call. Grant locks keep the same
-- global order as claims. A closed grant can replay only its exact return proof.
CREATE FUNCTION deltallm_accounting_return_local_permits_batch(
    p_generation BIGINT,p_owner_id TEXT,p_items JSONB
) RETURNS TABLE(
    grant_id TEXT,fence_token UUID,first_unused_ordinal INTEGER,returned_operations INTEGER
)
LANGUAGE plpgsql AS $$
DECLARE
    item JSONB;
    grant_row deltallm_accounting_grants%ROWTYPE;
    returned_count INTEGER;
BEGIN
    IF COALESCE(length(p_owner_id),0) NOT BETWEEN 1 AND 219
       OR COALESCE(jsonb_typeof(p_items),'null')<>'array' THEN
        RAISE EXCEPTION 'accounting_local_return_batch_shape' USING ERRCODE='P0001';
    END IF;
    IF jsonb_array_length(p_items) NOT BETWEEN 1 AND 256
       OR octet_length(p_items::text)>2097152 THEN
        RAISE EXCEPTION 'accounting_local_return_batch_shape' USING ERRCODE='P0001';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_items) value
        WHERE COALESCE(length(value->>'grant_id'),0) NOT BETWEEN 1 AND 256
          OR value->>'fence_token' IS NULL
          OR COALESCE((value->>'first_unused_ordinal')::integer,-1) NOT BETWEEN 0 AND 1024
    ) OR EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_items) value
        GROUP BY value->>'grant_id' HAVING count(*)>1
    ) THEN
        RAISE EXCEPTION 'accounting_local_return_item_shape' USING ERRCODE='P0001';
    END IF;

    PERFORM 1 FROM deltallm_accounting_grants g
        WHERE g.grant_id=ANY(ARRAY(
            SELECT value->>'grant_id' FROM jsonb_array_elements(p_items) value
        )) ORDER BY g.grant_id FOR UPDATE;
    FOR item IN SELECT value FROM jsonb_array_elements(p_items) value
                ORDER BY value->>'grant_id'
    LOOP
        SELECT * INTO STRICT grant_row FROM deltallm_accounting_grants g
            WHERE g.grant_id=item->>'grant_id';
        returned_count:=grant_row.operation_limit-(item->>'first_unused_ordinal')::integer;
        IF grant_row.protocol_name<>'primary' OR grant_row.generation<>p_generation
           OR NOT grant_row.local_dispatch
           OR grant_row.fence_token IS DISTINCT FROM (item->>'fence_token')::uuid
           OR grant_row.grantee_id<>p_owner_id||':'||grant_row.fence_token::text
           OR returned_count NOT BETWEEN 0 AND grant_row.operation_limit THEN
            RAISE EXCEPTION 'accounting_local_return_identity' USING ERRCODE='P0001';
        END IF;
        IF grant_row.state='closed' THEN
            IF grant_row.returned_operations<>returned_count
               OR grant_row.returned_exact<>grant_row.unit_allowance_exact*returned_count THEN
                RAISE EXCEPTION 'accounting_local_return_conflict' USING ERRCODE='P0001';
            END IF;
        ELSE
            PERFORM * FROM deltallm_accounting_return_local_permits(
                p_generation,grant_row.grant_id,grant_row.fence_token,
                (item->>'first_unused_ordinal')::integer
            );
        END IF;
        RETURN QUERY SELECT grant_row.grant_id,grant_row.fence_token,
                            (item->>'first_unused_ordinal')::integer,returned_count;
    END LOOP;
END;
$$;

CREATE OR REPLACE FUNCTION deltallm_accounting_finalize_local_permit_batch(
    p_generation BIGINT,p_items JSONB
) RETURNS TABLE(
    operation_id TEXT,
    event_sequence BIGINT,
    outcome TEXT,
    replayed BOOLEAN
)
LANGUAGE plpgsql AS $$
DECLARE
    claimed_count INTEGER;
    terminal_items JSONB;
BEGIN
    IF COALESCE(jsonb_typeof(p_items),'null')<>'array' THEN
        RAISE EXCEPTION 'accounting_local_finalization_batch_shape' USING ERRCODE = 'P0001';
    END IF;
    IF jsonb_array_length(p_items) NOT BETWEEN 1 AND 256
       OR octet_length(p_items::text)>2097152 THEN
        RAISE EXCEPTION 'accounting_local_finalization_batch_shape' USING ERRCODE = 'P0001';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_items) entry
        WHERE jsonb_typeof(entry->'reservation')<>'object'
           OR jsonb_typeof(entry->'finalization')<>'object'
           OR entry#>>'{reservation,operation_id}'
                IS DISTINCT FROM entry#>>'{finalization,operation_id}'
           OR entry#>>'{reservation,owner_token}'
                IS DISTINCT FROM entry#>>'{finalization,owner_token}'
           OR entry#>>'{reservation,request_fingerprint}'
                IS DISTINCT FROM entry#>>'{finalization,request_fingerprint}'
           OR (entry#>>'{reservation,protocol_generation}')::bigint<>p_generation
           OR (entry#>>'{finalization,protocol_generation}')::bigint<>p_generation
    ) THEN
        RAISE EXCEPTION 'accounting_local_finalization_identity' USING ERRCODE = 'P0001';
    END IF;

    -- The shared claim owner keeps global operation and grant lock order.
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_items) entry
        LEFT JOIN LATERAL (
            SELECT g.local_dispatch,g.expires_at FROM deltallm_accounting_grants g
            WHERE g.grant_id=entry->>'grant_id' OFFSET 0
        ) g ON TRUE
        WHERE g.local_dispatch IS DISTINCT FROM TRUE
           OR (entry#>>'{reservation,expires_at}')::timestamptz>g.expires_at
    ) THEN
        RAISE EXCEPTION 'accounting_local_finalization_grant' USING ERRCODE = 'P0001';
    END IF;
    SELECT count(*)::integer INTO claimed_count
    FROM deltallm_accounting_claim_permits_batch(p_generation,p_items);
    IF claimed_count<>jsonb_array_length(p_items) THEN
        RAISE EXCEPTION 'accounting_local_finalization_claim_result' USING ERRCODE = 'P0001';
    END IF;
    -- Claim holds the operation locks before checking an existing terminal time.
    -- Concurrent replay cannot bypass this check with a stale pre-lock snapshot.
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_items) entry
        CROSS JOIN LATERAL (
            SELECT e.occurred_at FROM deltallm_accounting_events e
            WHERE e.event_id=entry#>>'{finalization,event_id}'
              AND e.event_type='finalized' OFFSET 0
        ) e
        WHERE e.occurred_at IS DISTINCT FROM
              (entry#>>'{finalization,occurred_at}')::timestamptz
    ) THEN
        RAISE EXCEPTION 'accounting_local_finalization_replay_time' USING ERRCODE='P0001';
    END IF;
    SELECT jsonb_agg(entry->'finalization' ORDER BY entry#>>'{finalization,operation_id}')
    INTO STRICT terminal_items FROM jsonb_array_elements(p_items) entry;
    RETURN QUERY
    SELECT * FROM deltallm_accounting_finalize_grant_batch(p_generation,terminal_items);
END;
$$;

COMMIT;
