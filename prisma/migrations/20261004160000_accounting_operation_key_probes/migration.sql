-- JSON batches have a fixed request shape. Keep operation locks and replay
-- probes on their primary keys; do not let set joins scan retained operations.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

CREATE OR REPLACE FUNCTION deltallm_accounting_reserve_grant_batch(
    p_generation BIGINT,
    p_grantee_id TEXT,
    p_target_operations INTEGER,
    p_ttl_seconds INTEGER,
    p_items JSONB
) RETURNS TABLE(
    operation_id TEXT,
    decision TEXT,
    dispatch_token TEXT,
    accounting_partition INTEGER
)
LANGUAGE plpgsql AS $$
DECLARE
    protocol_row deltallm_accounting_protocols%ROWTYPE;
    item JSONB;
    op deltallm_billing_operations%ROWTYPE;
    allowance NUMERIC(38,18);
    returned_count INTEGER;
BEGIN
    IF length(p_grantee_id) NOT BETWEEN 1 AND 256
       OR p_target_operations NOT BETWEEN 1 AND 1024
       OR p_ttl_seconds NOT BETWEEN 1 AND 300
       OR jsonb_typeof(p_items) <> 'array'
       OR jsonb_array_length(p_items) NOT BETWEEN 1 AND 256 THEN
        RAISE EXCEPTION 'accounting_grant_batch_shape' USING ERRCODE = 'P0001';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_items) value
        GROUP BY value->>'operation_id' HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'accounting_reservation_duplicate_operation' USING ERRCODE = 'P0001';
    END IF;

    SELECT * INTO STRICT protocol_row FROM deltallm_accounting_protocols
        WHERE protocol_name='primary' AND generation=p_generation FOR SHARE;
    IF protocol_row.state <> 'active' OR protocol_row.writer_version <> 2 THEN
        RAISE EXCEPTION 'accounting_protocol_not_active' USING ERRCODE = 'P0001';
    END IF;

    PERFORM 1 FROM deltallm_billing_operations b
        WHERE b.operation_id=ANY(ARRAY(
            SELECT value->>'operation_id' FROM jsonb_array_elements(p_items) value
        )) ORDER BY b.operation_id FOR UPDATE;
    PERFORM 1 FROM deltallm_accounting_grants g
        WHERE g.protocol_name='primary' AND g.generation=p_generation
          AND g.grantee_id=p_grantee_id AND g.state='active'
          AND g.expires_at>CURRENT_TIMESTAMP
          AND g.subject_key IN (
              SELECT deltallm_accounting_grant_subject(value)
              FROM jsonb_array_elements(p_items) value
              ) ORDER BY g.grant_id FOR UPDATE;
    FOR item IN SELECT value FROM jsonb_array_elements(p_items) value
                ORDER BY value->>'operation_id'
    LOOP
        IF (item->>'protocol_generation')::bigint <> p_generation
           OR item->>'request_fingerprint' !~ '^[0-9a-f]{64}$'
           OR jsonb_typeof(item->'windows') <> 'array'
           OR jsonb_array_length(item->'windows') > 8
           OR jsonb_typeof(item->'attribution') <> 'object'
           OR jsonb_typeof(item->'pricing_snapshot') <> 'object'
           OR jsonb_typeof(item->'audit_envelope') <> 'object'
           OR octet_length((item->'pricing_snapshot')::text) > 32768
           OR octet_length((item->'audit_envelope')::text) > 65536
           OR (item->>'expires_at')::timestamptz <= CURRENT_TIMESTAMP
           OR (item->>'expires_at')::timestamptz > CURRENT_TIMESTAMP + interval '15 minutes' THEN
            RAISE EXCEPTION 'accounting_reservation_item_shape' USING ERRCODE = 'P0001';
        END IF;
        allowance := (item->>'allowance')::numeric;
        IF allowance < 0 THEN
            RAISE EXCEPTION 'accounting_reservation_allowance' USING ERRCODE = 'P0001';
        END IF;
        SELECT * INTO op FROM deltallm_billing_operations
            WHERE deltallm_billing_operations.operation_id=item->>'operation_id';
        IF FOUND THEN
            IF op.accounting_protocol <> 'primary'
               OR op.accounting_generation <> p_generation
               OR op.request_fingerprint <> item->>'request_fingerprint'
               OR op.owner_token <> item->>'owner_token'
               OR op.snapshot <> item THEN
                RAISE EXCEPTION 'accounting_operation_identity_conflict' USING ERRCODE = 'P0001';
            END IF;
        END IF;
    END LOOP;

    RETURN QUERY
    WITH raw_items AS MATERIALIZED (
        SELECT value AS item,
               deltallm_accounting_grant_subject(value) AS subject_key,
               (value->>'allowance')::numeric AS allowance
        FROM jsonb_array_elements(p_items) value
    ), new_items AS MATERIALIZED (
        SELECT raw_items.*,
               row_number() OVER (
                   PARTITION BY raw_items.subject_key
                   ORDER BY raw_items.item->>'operation_id'
               ) AS item_slot
        FROM raw_items
        WHERE (
            SELECT existing.operation_id FROM deltallm_billing_operations existing
            WHERE existing.operation_id=raw_items.item->>'operation_id' LIMIT 1
        ) IS NULL
    ), subject_allowances AS MATERIALIZED (
        SELECT new_items.subject_key,min(new_items.allowance) AS allowance
        FROM new_items GROUP BY new_items.subject_key
    ), grant_capacity_base AS MATERIALIZED (
        SELECT g.grant_id,g.subject_key,g.accounting_partition,g.expires_at,
               CASE WHEN subjects.allowance=0
                    THEN g.operation_limit-g.consumed_operations
                    ELSE least(
                        g.operation_limit-g.consumed_operations,
                        floor((g.allocated_exact-g.consumed_exact)/subjects.allowance)::integer
                    )
               END AS slot_count
        FROM deltallm_accounting_grants g
        JOIN subject_allowances subjects ON subjects.subject_key=g.subject_key
        WHERE g.protocol_name='primary' AND g.generation=p_generation
          AND g.grantee_id=p_grantee_id AND g.state='active'
          AND g.expires_at>CURRENT_TIMESTAMP
    ), grant_ranges AS MATERIALIZED (
        SELECT grant_capacity_base.*,
               sum(grant_capacity_base.slot_count) OVER (
                   PARTITION BY grant_capacity_base.subject_key
                   ORDER BY grant_capacity_base.expires_at,grant_capacity_base.grant_id
                   ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
               )-grant_capacity_base.slot_count AS first_slot,
               sum(grant_capacity_base.slot_count) OVER (
                   PARTITION BY grant_capacity_base.subject_key
                   ORDER BY grant_capacity_base.expires_at,grant_capacity_base.grant_id
                   ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
               ) AS last_slot
        FROM grant_capacity_base WHERE grant_capacity_base.slot_count>0
    ), assigned AS MATERIALIZED (
        SELECT new_items.item,new_items.allowance,
               grant_ranges.grant_id,grant_ranges.accounting_partition
        FROM new_items
        JOIN grant_ranges
          ON grant_ranges.subject_key=new_items.subject_key
         AND new_items.item_slot>grant_ranges.first_slot
         AND new_items.item_slot<=grant_ranges.last_slot
    ), consumption AS MATERIALIZED (
        SELECT assigned.grant_id,count(*)::integer AS operation_count,
               sum(assigned.allowance)::numeric AS exact_amount
        FROM assigned GROUP BY assigned.grant_id
    ), updated_grants AS (
        UPDATE deltallm_accounting_grants AS target_grant SET
            consumed_exact=target_grant.consumed_exact+consumption.exact_amount,
            consumed_operations=target_grant.consumed_operations+consumption.operation_count,
            state=CASE
                WHEN target_grant.consumed_operations+consumption.operation_count
                     >=target_grant.operation_limit
                  OR (target_grant.allocated_exact>0
                      AND target_grant.consumed_exact+consumption.exact_amount
                          >=target_grant.allocated_exact)
                THEN 'draining' ELSE target_grant.state END,
            updated_at=NOW()
        FROM consumption
        WHERE target_grant.grant_id=consumption.grant_id
          AND target_grant.state='active' AND target_grant.expires_at>CURRENT_TIMESTAMP
          AND target_grant.consumed_operations+consumption.operation_count
              <=target_grant.operation_limit
          AND target_grant.consumed_exact+consumption.exact_amount
              <=target_grant.allocated_exact
        RETURNING target_grant.grant_id
    ), inserted_operations AS (
        INSERT INTO deltallm_billing_operations AS inserted (
            operation_id,owner_token,api_key,user_id,team_id,organization_id,model,snapshot,
            selector_event_id,selector_allowance,answer_allowance,selector_state,answer_state,
            expires_at,accounting_protocol,accounting_generation,accounting_partition,
            request_fingerprint,accounting_state,accounting_grant_id,provisional_debit_exact
        )
        SELECT assigned.item->>'operation_id',assigned.item->>'owner_token',
               assigned.item#>>'{attribution,api_key}',assigned.item#>>'{attribution,user_id}',
               assigned.item#>>'{attribution,team_id}',
               assigned.item#>>'{attribution,organization_id}',
               assigned.item#>>'{attribution,model}',assigned.item,
               (assigned.item->>'operation_id')||':selector',0,assigned.allowance,
               'unattempted','dispatched',(assigned.item->>'expires_at')::timestamptz,
               'primary',p_generation,assigned.accounting_partition,
               assigned.item->>'request_fingerprint','reserved',assigned.grant_id,0
        FROM assigned
        JOIN updated_grants ON updated_grants.grant_id=assigned.grant_id
        RETURNING inserted.operation_id,inserted.owner_token,inserted.snapshot,
                  inserted.answer_allowance,inserted.accounting_partition,
                  inserted.accounting_grant_id
    ), inserted_reservations AS (
        INSERT INTO deltallm_accounting_reservations AS inserted_reservation(
            operation_id,window_id,grant_id,allowance_exact
        )
        SELECT inserted_operations.operation_id,grant_windows.window_id,
               inserted_operations.accounting_grant_id,inserted_operations.answer_allowance
        FROM inserted_operations
        JOIN deltallm_accounting_grant_windows grant_windows
          ON grant_windows.grant_id=inserted_operations.accounting_grant_id
        RETURNING inserted_reservation.operation_id
    ), inserted_events AS (
        INSERT INTO deltallm_accounting_events AS inserted_event(
            event_id,protocol_name,generation,accounting_partition,operation_id,
            component_id,event_type,payload_json,audit_envelope_json,occurred_at
        )
        SELECT inserted_operations.operation_id||':reserved','primary',p_generation,
               inserted_operations.accounting_partition,inserted_operations.operation_id,
               'provider','reserved',jsonb_build_object(
                   'allowance',inserted_operations.answer_allowance::text,
                   'pricing_snapshot',inserted_operations.snapshot->'pricing_snapshot',
                   'grant_id',inserted_operations.accounting_grant_id
               ),inserted_operations.snapshot->'audit_envelope',CURRENT_TIMESTAMP
        FROM inserted_operations
        RETURNING inserted_event.operation_id
    ), effects AS MATERIALIZED (
        SELECT (SELECT count(*) FROM inserted_reservations) AS reservation_count,
               (SELECT count(*) FROM inserted_events) AS event_count
    )
    SELECT existing.operation_id,'replay'::text,NULL::text,NULL::integer
    FROM raw_items
    CROSS JOIN LATERAL (
        SELECT existing.operation_id FROM deltallm_billing_operations existing
        WHERE existing.operation_id=raw_items.item->>'operation_id' OFFSET 0
    ) existing
    UNION ALL
    SELECT inserted_operations.operation_id,'dispatch'::text,
           inserted_operations.owner_token,inserted_operations.accounting_partition
    FROM inserted_operations CROSS JOIN effects;

    GET DIAGNOSTICS returned_count = ROW_COUNT;
    IF returned_count<>jsonb_array_length(p_items) THEN
        RAISE EXCEPTION 'accounting_grant_lost' USING ERRCODE = 'P0001';
    END IF;
END;
$$;

COMMIT;

