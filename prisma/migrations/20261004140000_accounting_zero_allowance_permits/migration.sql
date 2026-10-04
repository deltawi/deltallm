BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

-- Zero-cost permits still consume operation slots. Keep a grant active until
-- its operation limit is reached; zero money alone is not an exhausted grant.
CREATE OR REPLACE FUNCTION deltallm_accounting_claim_permit_batch(
    p_generation BIGINT,
    p_grantee_id TEXT,
    p_grant_id TEXT,
    p_fence_token UUID,
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
    grant_row deltallm_accounting_grants%ROWTYPE;
    item JSONB;
    reservation_item JSONB;
    op deltallm_billing_operations%ROWTYPE;
    new_operation_count INTEGER;
    new_exact NUMERIC(38,18);
    returned_count INTEGER:=0;
BEGIN
    IF length(p_grantee_id) NOT BETWEEN 1 AND 256
       OR length(p_grant_id) NOT BETWEEN 1 AND 256
       OR jsonb_typeof(p_items)<>'array'
       OR jsonb_array_length(p_items) NOT BETWEEN 1 AND 256 THEN
        RAISE EXCEPTION 'accounting_permit_claim_batch_shape' USING ERRCODE = 'P0001';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_items) value
        GROUP BY value->>'operation_id' HAVING count(*)>1
    ) OR EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_items) value
        GROUP BY value->>'permit_ordinal' HAVING count(*)>1
    ) THEN
        RAISE EXCEPTION 'accounting_permit_claim_duplicate' USING ERRCODE = 'P0001';
    END IF;

    SELECT * INTO STRICT protocol_row FROM deltallm_accounting_protocols
        WHERE protocol_name='primary' AND generation=p_generation FOR SHARE;
    IF protocol_row.state<>'active' OR protocol_row.writer_version<>2 THEN
        RAISE EXCEPTION 'accounting_protocol_not_active' USING ERRCODE = 'P0001';
    END IF;

    PERFORM 1 FROM deltallm_billing_operations b
        WHERE b.operation_id IN (
            SELECT value->>'operation_id' FROM jsonb_array_elements(p_items) value
        ) ORDER BY b.operation_id FOR UPDATE;

    SELECT * INTO STRICT grant_row FROM deltallm_accounting_grants g
        WHERE g.grant_id=p_grant_id FOR UPDATE;
    IF grant_row.protocol_name<>'primary' OR grant_row.generation<>p_generation
       OR grant_row.grantee_id<>p_grantee_id OR grant_row.dispatch_mode<>'preissued'
       OR grant_row.fence_token<>p_fence_token OR grant_row.state<>'active'
       OR grant_row.expires_at<=CURRENT_TIMESTAMP
       OR grant_row.unit_allowance_exact IS NULL THEN
        RAISE EXCEPTION 'accounting_permit_grant_unavailable' USING ERRCODE = 'P0001';
    END IF;

    FOR item IN SELECT value FROM jsonb_array_elements(p_items) value
                ORDER BY value->>'operation_id'
    LOOP
        reservation_item:=item-'permit_ordinal';
        IF (reservation_item->>'protocol_generation')::bigint<>p_generation
           OR reservation_item->>'request_fingerprint' !~ '^[0-9a-f]{64}$'
           OR jsonb_typeof(reservation_item->'windows')<>'array'
           OR jsonb_array_length(reservation_item->'windows')>8
           OR jsonb_typeof(reservation_item->'attribution')<>'object'
           OR jsonb_typeof(reservation_item->'pricing_snapshot')<>'object'
           OR jsonb_typeof(reservation_item->'audit_envelope')<>'object'
           OR octet_length((reservation_item->'pricing_snapshot')::text)>32768
           OR octet_length((reservation_item->'audit_envelope')::text)>65536
           OR (reservation_item->>'expires_at')::timestamptz<=CURRENT_TIMESTAMP
           OR (reservation_item->>'expires_at')::timestamptz>CURRENT_TIMESTAMP+interval '15 minutes'
           OR (item->>'permit_ordinal')::integer NOT BETWEEN 0 AND grant_row.operation_limit-1
           OR (reservation_item->>'allowance')::numeric<>grant_row.unit_allowance_exact
           OR deltallm_accounting_grant_subject(reservation_item)<>grant_row.subject_key THEN
            RAISE EXCEPTION 'accounting_permit_claim_item_shape' USING ERRCODE = 'P0001';
        END IF;

        SELECT * INTO op FROM deltallm_billing_operations b
            WHERE b.operation_id=reservation_item->>'operation_id';
        IF FOUND AND (
            op.accounting_protocol<>'primary'
            OR op.accounting_generation<>p_generation
            OR op.accounting_grant_id<>p_grant_id
            OR op.accounting_permit_ordinal<>(item->>'permit_ordinal')::integer
            OR op.accounting_grant_fence_token<>p_fence_token
            OR op.owner_token<>reservation_item->>'owner_token'
            OR op.request_fingerprint<>reservation_item->>'request_fingerprint'
            OR op.snapshot<>reservation_item
        ) THEN
            RAISE EXCEPTION 'accounting_permit_claim_identity_conflict' USING ERRCODE = 'P0001';
        END IF;
        IF EXISTS (
            SELECT 1 FROM deltallm_billing_operations occupied
            WHERE occupied.accounting_grant_id=p_grant_id
              AND occupied.accounting_permit_ordinal=(item->>'permit_ordinal')::integer
              AND occupied.operation_id<>reservation_item->>'operation_id'
        ) THEN
            RAISE EXCEPTION 'accounting_permit_ordinal_conflict' USING ERRCODE = 'P0001';
        END IF;
    END LOOP;

    SELECT count(*)::integer,COALESCE(sum((item.value->>'allowance')::numeric),0)::numeric
    INTO new_operation_count,new_exact
    FROM jsonb_array_elements(p_items) item
    WHERE NOT EXISTS (
        SELECT 1 FROM deltallm_billing_operations existing
        WHERE existing.operation_id=item.value->>'operation_id'
    );

    IF new_operation_count>0 THEN
        UPDATE deltallm_accounting_grants g SET
            consumed_operations=g.consumed_operations+new_operation_count,
            consumed_exact=g.consumed_exact+new_exact,
            state=CASE
                WHEN g.consumed_operations+new_operation_count>=g.operation_limit
                  OR (g.allocated_exact>0 AND g.consumed_exact+new_exact>=g.allocated_exact)
                THEN 'draining' ELSE g.state END,
            updated_at=NOW()
        WHERE g.grant_id=p_grant_id AND g.state='active'
          AND g.consumed_operations+new_operation_count<=g.operation_limit
          AND g.consumed_exact+new_exact<=g.allocated_exact;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'accounting_permit_capacity_lost' USING ERRCODE = 'P0001';
        END IF;
    END IF;

    RETURN QUERY
    WITH raw_items AS MATERIALIZED (
        SELECT value AS item,value-'permit_ordinal' AS reservation_item
        FROM jsonb_array_elements(p_items) value
    ), new_items AS MATERIALIZED (
        SELECT * FROM raw_items
        WHERE NOT EXISTS (
            SELECT 1 FROM deltallm_billing_operations existing
            WHERE existing.operation_id=raw_items.reservation_item->>'operation_id'
        )
    ), inserted_operations AS (
        INSERT INTO deltallm_billing_operations AS inserted (
            operation_id,owner_token,api_key,user_id,team_id,organization_id,model,snapshot,
            selector_event_id,selector_allowance,answer_allowance,selector_state,answer_state,
            expires_at,accounting_protocol,accounting_generation,accounting_partition,
            request_fingerprint,accounting_state,accounting_grant_id,
            accounting_permit_ordinal,accounting_grant_fence_token,provisional_debit_exact
        )
        SELECT new_items.reservation_item->>'operation_id',
               new_items.reservation_item->>'owner_token',
               new_items.reservation_item#>>'{attribution,api_key}',
               new_items.reservation_item#>>'{attribution,user_id}',
               new_items.reservation_item#>>'{attribution,team_id}',
               new_items.reservation_item#>>'{attribution,organization_id}',
               new_items.reservation_item#>>'{attribution,model}',new_items.reservation_item,
               (new_items.reservation_item->>'operation_id')||':selector',0,
               (new_items.reservation_item->>'allowance')::numeric,
               'unattempted','dispatched',
               (new_items.reservation_item->>'expires_at')::timestamptz,
               'primary',p_generation,grant_row.accounting_partition,
               new_items.reservation_item->>'request_fingerprint','reserved',p_grant_id,
               (new_items.item->>'permit_ordinal')::integer,p_fence_token,0
        FROM new_items
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
                   'grant_id',inserted_operations.accounting_grant_id,
                   'permit_ordinal',(
                       SELECT (raw.item->>'permit_ordinal')::integer FROM raw_items raw
                       WHERE raw.reservation_item->>'operation_id'=inserted_operations.operation_id
                   )
               ),inserted_operations.snapshot->'audit_envelope',CURRENT_TIMESTAMP
        FROM inserted_operations
        RETURNING inserted_event.operation_id
    ), effects AS MATERIALIZED (
        SELECT (SELECT count(*) FROM inserted_reservations) AS reservation_count,
               (SELECT count(*) FROM inserted_events) AS event_count
    )
    SELECT existing.operation_id,'replay'::text,NULL::text,NULL::integer
    FROM deltallm_billing_operations existing
    JOIN raw_items ON existing.operation_id=raw_items.reservation_item->>'operation_id'
    WHERE existing.operation_id NOT IN (
        SELECT inserted.operation_id FROM inserted_operations inserted
    )
    UNION ALL
    SELECT inserted_operations.operation_id,'dispatch'::text,
           inserted_operations.owner_token,inserted_operations.accounting_partition
    FROM inserted_operations CROSS JOIN effects;

    GET DIAGNOSTICS returned_count=ROW_COUNT;
    IF returned_count<>jsonb_array_length(p_items) THEN
        RAISE EXCEPTION 'accounting_permit_claim_incomplete_result' USING ERRCODE = 'P0001';
    END IF;
END;
$$;

COMMIT;
