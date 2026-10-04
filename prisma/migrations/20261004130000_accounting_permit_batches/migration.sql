BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

CREATE INDEX deltallm_accounting_window_subject_time_idx
    ON deltallm_accounting_budget_windows(
        protocol_name,generation,scope_type,scope_id,window_ends_at,window_starts_at
    );

-- Bound each scope lookup before a refill locks all subjects in one order.
-- The existing allocator still validates policy and reserves exact balances.
CREATE FUNCTION deltallm_accounting_permit_window_ids(p_generation BIGINT,p_item JSONB)
RETURNS TABLE(window_id TEXT)
LANGUAGE SQL STABLE AS $$
    SELECT w.window_id
    FROM jsonb_array_elements(p_item->'windows') ref
    JOIN deltallm_accounting_budget_windows w ON w.window_id=ref->>'window_id'
    WHERE w.protocol_name='primary' AND w.generation=p_generation
      AND w.window_starts_at<=CURRENT_TIMESTAMP AND w.window_ends_at>CURRENT_TIMESTAMP
    UNION ALL
    SELECT found.window_id FROM (VALUES
        ('api_key',p_item#>>'{attribution,api_key}'),
        ('user',p_item#>>'{attribution,user_id}'),
        ('team',p_item#>>'{attribution,team_id}'),
        ('organization',p_item#>>'{attribution,organization_id}'),
        ('team_model',(p_item#>>'{attribution,team_id}')||':'||
                      (p_item#>>'{attribution,model}'))
    ) AS scope(scope_type,scope_id)
    CROSS JOIN LATERAL (
        SELECT w.window_id FROM deltallm_accounting_budget_windows w
        WHERE w.protocol_name='primary' AND w.generation=p_generation
          AND w.scope_type=scope.scope_type AND w.scope_id=scope.scope_id
          AND w.window_starts_at<=CURRENT_TIMESTAMP AND w.window_ends_at>CURRENT_TIMESTAMP
        ORDER BY w.window_ends_at,w.window_starts_at,w.window_id LIMIT 9
    ) found
    WHERE jsonb_array_length(p_item->'windows')=0
$$;

CREATE FUNCTION deltallm_accounting_allocate_permit_grants_batch(
    p_generation BIGINT,p_owner_id TEXT,p_ttl_seconds INTEGER,p_items JSONB
) RETURNS TABLE(
    allocation_fence_token UUID,decision TEXT,grant_id TEXT,grantee_id TEXT,
    fence_token UUID,accounting_partition INTEGER,allowance_exact NUMERIC,
    operation_limit INTEGER,expires_at TIMESTAMPTZ
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
        WHERE g.fence_token IN (
            SELECT (value->>'fence_token')::uuid FROM jsonb_array_elements(p_items) value
        ) ORDER BY g.grant_id FOR UPDATE;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_items) value
        JOIN deltallm_accounting_grants g ON g.fence_token=(value->>'fence_token')::uuid
        WHERE g.protocol_name<>'primary' OR g.generation<>p_generation
          OR g.grantee_id<>p_owner_id||':'||g.fence_token::text
          OR g.subject_key<>deltallm_accounting_grant_subject(value->'reservation')
          OR g.unit_allowance_exact IS DISTINCT FROM
             (value#>>'{reservation,allowance}')::numeric
          OR g.dispatch_mode<>'preissued' OR g.state<>'active'
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
        RETURN QUERY SELECT (item->>'fence_token')::uuid,allocated.*
        FROM deltallm_accounting_allocate_permit_grant(
            p_generation,p_owner_id||':'||(item->>'fence_token')::uuid::text,
            (item->>'fence_token')::uuid,(item->>'target_operations')::integer,
            p_ttl_seconds,item->'reservation'
        ) allocated;
    END LOOP;
END;
$$;

CREATE FUNCTION deltallm_accounting_claim_permits_batch(p_generation BIGINT,p_items JSONB)
RETURNS TABLE(operation_id TEXT,decision TEXT,dispatch_token TEXT,accounting_partition INTEGER)
LANGUAGE plpgsql AS $$
DECLARE
    group_item RECORD;
    item JSONB;
    protocol_state TEXT;
BEGIN
    IF COALESCE(jsonb_typeof(p_items),'null')<>'array' THEN
        RAISE EXCEPTION 'accounting_permit_claims_batch_shape' USING ERRCODE='P0001';
    END IF;
    IF jsonb_array_length(p_items) NOT BETWEEN 1 AND 256
       OR octet_length(p_items::text)>2097152 THEN
        RAISE EXCEPTION 'accounting_permit_claims_batch_shape' USING ERRCODE='P0001';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_items) value
        WHERE COALESCE(jsonb_typeof(value->'reservation'),'null')<>'object'
          OR COALESCE(length(value->>'grant_id'),0) NOT BETWEEN 1 AND 256
          OR COALESCE(length(value->>'grantee_id'),0) NOT BETWEEN 1 AND 256
          OR value->>'fence_token' IS NULL
          OR COALESCE((value->>'permit_ordinal')::integer,-1) NOT BETWEEN 0 AND 1023
          OR (value#>>'{reservation,protocol_generation}')::bigint IS DISTINCT FROM p_generation
          OR value#>>'{reservation,operation_id}' IS NULL
    ) OR EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_items) value
        GROUP BY value#>>'{reservation,operation_id}' HAVING count(*)>1
    ) OR EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_items) value
        GROUP BY value->>'grant_id',(value->>'permit_ordinal')::integer HAVING count(*)>1
    ) THEN
        RAISE EXCEPTION 'accounting_permit_claims_item_shape' USING ERRCODE='P0001';
    END IF;
    SELECT state INTO STRICT protocol_state FROM deltallm_accounting_protocols
        WHERE protocol_name='primary' AND generation=p_generation AND writer_version=2
        FOR SHARE;
    IF protocol_state<>'active' THEN
        RAISE EXCEPTION 'accounting_protocol_not_active' USING ERRCODE='P0001';
    END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(p_items) value
                ORDER BY value#>>'{reservation,operation_id}'
    LOOP
        PERFORM pg_advisory_xact_lock(hashtextextended(
            'accounting-permit-claim:'||(item#>>'{reservation,operation_id}'),0
        ));
    END LOOP;
    PERFORM 1 FROM deltallm_billing_operations b
        WHERE b.operation_id IN (
            SELECT value#>>'{reservation,operation_id}' FROM jsonb_array_elements(p_items) value
        ) ORDER BY b.operation_id FOR UPDATE;
    PERFORM 1 FROM deltallm_accounting_grants g
        WHERE g.grant_id IN (
            SELECT value->>'grant_id' FROM jsonb_array_elements(p_items) value
        ) ORDER BY g.grant_id FOR UPDATE;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_items) value
        JOIN deltallm_billing_operations b ON b.operation_id=value#>>'{reservation,operation_id}'
        WHERE b.accounting_protocol IS DISTINCT FROM 'primary'
          OR b.accounting_generation IS DISTINCT FROM p_generation
          OR b.accounting_grant_id IS DISTINCT FROM value->>'grant_id'
          OR b.accounting_permit_ordinal IS DISTINCT FROM (value->>'permit_ordinal')::integer
          OR b.accounting_grant_fence_token IS DISTINCT FROM (value->>'fence_token')::uuid
          OR b.owner_token IS DISTINCT FROM value#>>'{reservation,owner_token}'
          OR b.request_fingerprint IS DISTINCT FROM value#>>'{reservation,request_fingerprint}'
          OR b.snapshot IS DISTINCT FROM value->'reservation'
    ) OR EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_items) value
        LEFT JOIN deltallm_accounting_grants g ON g.grant_id=value->>'grant_id'
        WHERE g.grant_id IS NULL OR g.protocol_name<>'primary' OR g.generation<>p_generation
          OR g.grantee_id<>value->>'grantee_id' OR g.dispatch_mode<>'preissued'
          OR g.fence_token IS DISTINCT FROM (value->>'fence_token')::uuid
    ) THEN
        RAISE EXCEPTION 'accounting_permit_claims_identity_conflict' USING ERRCODE='P0001';
    END IF;
    -- Completed or expired grants may replay an exact claim, never dispatch it.
    RETURN QUERY SELECT b.operation_id,'replay'::text,NULL::text,NULL::integer
        FROM deltallm_billing_operations b
        JOIN jsonb_array_elements(p_items) value
          ON b.operation_id=value#>>'{reservation,operation_id}';
    FOR group_item IN
        SELECT value->>'grant_id' AS id,value->>'grantee_id' AS owner,
               (value->>'fence_token')::uuid AS fence,
               jsonb_agg((value->'reservation')||jsonb_build_object(
                   'permit_ordinal',(value->>'permit_ordinal')::integer
               ) ORDER BY value#>>'{reservation,operation_id}') AS items
        FROM jsonb_array_elements(p_items) value
        WHERE NOT EXISTS (
            SELECT 1 FROM deltallm_billing_operations b
            WHERE b.operation_id=value#>>'{reservation,operation_id}'
        ) GROUP BY value->>'grant_id',value->>'grantee_id',(value->>'fence_token')::uuid
        ORDER BY value->>'grant_id'
    LOOP
        RETURN QUERY SELECT claimed.* FROM deltallm_accounting_claim_permit_batch(
            p_generation,group_item.owner,group_item.id,group_item.fence,group_item.items
        ) claimed;
    END LOOP;
END;
$$;

COMMIT;
