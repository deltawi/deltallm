BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

ALTER TABLE deltallm_accounting_grants
    ADD COLUMN local_dispatch BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN dispatch_expires_at TIMESTAMPTZ,
    ADD COLUMN returned_exact NUMERIC(38,18) NOT NULL DEFAULT 0,
    ADD COLUMN returned_operations INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN unknown_provisional_exact NUMERIC(38,18) NOT NULL DEFAULT 0;

ALTER TABLE deltallm_accounting_grants
    DROP CONSTRAINT deltallm_accounting_grants_check,
    DROP CONSTRAINT deltallm_accounting_grants_check2,
    ADD CONSTRAINT deltallm_accounting_grant_local_dispatch_check CHECK (
        (NOT local_dispatch AND dispatch_expires_at IS NULL)
        OR (local_dispatch AND dispatch_mode='preissued'
            AND dispatch_expires_at IS NOT NULL
            AND dispatch_expires_at<=expires_at)
    ),
    ADD CONSTRAINT deltallm_accounting_grant_settled_exact_check CHECK (
        settled_exact>=0
        AND (
            (NOT local_dispatch AND settled_exact<=consumed_exact)
            OR (local_dispatch AND settled_exact<=consumed_exact+unknown_provisional_exact)
        )
    ),
    ADD CONSTRAINT deltallm_accounting_grant_settled_operations_check CHECK (
        settled_operations>=0
        AND (
            (NOT local_dispatch AND settled_operations<=consumed_operations)
            OR (local_dispatch AND settled_operations<=operation_limit)
        )
    ),
    ADD CONSTRAINT deltallm_accounting_grant_returned_capacity_check CHECK (
        returned_exact>=0 AND returned_operations>=0
        AND returned_exact+consumed_exact<=allocated_exact
        AND returned_operations+consumed_operations<=operation_limit
    ),
    ADD CONSTRAINT deltallm_accounting_grant_unknown_provisional_check CHECK (
        unknown_provisional_exact>=0 AND unknown_provisional_exact<=allocated_exact
    );

-- Allocation funds every applicable budget window. New dispatch keeps the short
-- refill and window deadline. A separate expiry retains escrow for terminal receipts.
CREATE FUNCTION deltallm_accounting_allocate_local_permit_grant(
    p_generation BIGINT,
    p_grantee_id TEXT,
    p_fence_token UUID,
    p_target_operations INTEGER,
    p_ttl_seconds INTEGER,
    p_item JSONB
) RETURNS TABLE(
    decision TEXT,
    grant_id TEXT,
    grantee_id TEXT,
    fence_token UUID,
    accounting_partition INTEGER,
    allowance_exact NUMERIC,
    operation_limit INTEGER,
    expires_at TIMESTAMPTZ,
    dispatch_expires_at TIMESTAMPTZ
)
LANGUAGE plpgsql AS $$
DECLARE
    allocated RECORD;
    lease_expires_at TIMESTAMPTZ;
BEGIN
    lease_expires_at:=(p_item->>'expires_at')::timestamptz;
    IF lease_expires_at IS NULL OR lease_expires_at<=CURRENT_TIMESTAMP
       OR lease_expires_at>CURRENT_TIMESTAMP+interval '15 minutes' THEN
        RAISE EXCEPTION 'accounting_local_permit_lease_expiry' USING ERRCODE = 'P0001';
    END IF;

    SELECT * INTO STRICT allocated
    FROM deltallm_accounting_allocate_permit_grant(
        p_generation,p_grantee_id,p_fence_token,p_target_operations,p_ttl_seconds,p_item
    );
    IF allocated.decision<>'dispatch' THEN
        RETURN QUERY SELECT allocated.decision,NULL::text,NULL::text,NULL::uuid,
                            NULL::integer,NULL::numeric,NULL::integer,NULL::timestamptz,
                            NULL::timestamptz;
        RETURN;
    END IF;

    RETURN QUERY
    UPDATE deltallm_accounting_grants g SET
        local_dispatch=TRUE,
        dispatch_expires_at=COALESCE(g.dispatch_expires_at,g.expires_at),
        expires_at=CASE WHEN g.local_dispatch THEN g.expires_at
            ELSE greatest(g.expires_at,lease_expires_at) END,
        updated_at=NOW()
    WHERE g.grant_id=allocated.grant_id
      AND g.grantee_id=p_grantee_id
      AND g.fence_token=p_fence_token
      AND g.dispatch_mode='preissued'
      AND g.state='active'
      AND g.consumed_operations=0
      AND g.returned_operations=0
    RETURNING 'dispatch'::text,g.grant_id,g.grantee_id,g.fence_token,
              g.accounting_partition,g.unit_allowance_exact,g.operation_limit,g.expires_at,
              g.dispatch_expires_at;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'accounting_local_permit_allocation_lost' USING ERRCODE = 'P0001';
    END IF;
END;
$$;

-- A single owner assigns ordinals monotonically. Only its never-issued suffix can be
-- returned. Anything else remains escrowed and becomes provisional after owner loss.
CREATE FUNCTION deltallm_accounting_return_local_permits(
    p_generation BIGINT,
    p_grant_id TEXT,
    p_fence_token UUID,
    p_first_unused_ordinal INTEGER
) RETURNS TABLE(returned_operations INTEGER)
LANGUAGE plpgsql AS $$
DECLARE
    grant_row deltallm_accounting_grants%ROWTYPE;
    returned_count INTEGER;
BEGIN
    SELECT * INTO STRICT grant_row FROM deltallm_accounting_grants g
    WHERE g.grant_id=p_grant_id FOR UPDATE;
    IF grant_row.protocol_name<>'primary' OR grant_row.generation<>p_generation
       OR NOT grant_row.local_dispatch OR grant_row.fence_token<>p_fence_token
       OR grant_row.state NOT IN ('active','draining')
       OR p_first_unused_ordinal NOT BETWEEN 0 AND grant_row.operation_limit THEN
        RAISE EXCEPTION 'accounting_local_permit_return_identity' USING ERRCODE = 'P0001';
    END IF;
    returned_count:=grant_row.operation_limit-p_first_unused_ordinal;
    IF EXISTS (
        SELECT 1 FROM deltallm_billing_operations b
        WHERE b.accounting_grant_id=p_grant_id
          AND b.accounting_permit_ordinal>=p_first_unused_ordinal
    ) THEN
        RAISE EXCEPTION 'accounting_local_permit_return_claimed' USING ERRCODE = 'P0001';
    END IF;
    IF grant_row.returned_operations NOT IN (0,returned_count) THEN
        RAISE EXCEPTION 'accounting_local_permit_return_conflict' USING ERRCODE = 'P0001';
    END IF;
    UPDATE deltallm_accounting_grants g SET
        returned_operations=returned_count,
        returned_exact=grant_row.unit_allowance_exact*returned_count,
        state=CASE
            WHEN consumed_operations+returned_count=operation_limit THEN 'draining'
            ELSE state
        END,
        updated_at=NOW()
    WHERE g.grant_id=p_grant_id;
    returned_operations:=returned_count;
    RETURN NEXT;
END;
$$;

-- The terminal call binds each locally issued ordinal to its immutable reservation and
-- then finalizes it in the same transaction. A retry replays both existing records.
CREATE FUNCTION deltallm_accounting_finalize_local_permit_batch(
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
            SELECT g.local_dispatch FROM deltallm_accounting_grants g
            WHERE g.grant_id=entry->>'grant_id' OFFSET 0
        ) g ON TRUE
        WHERE g.local_dispatch IS DISTINCT FROM TRUE
    ) THEN
        RAISE EXCEPTION 'accounting_local_finalization_grant' USING ERRCODE = 'P0001';
    END IF;
    SELECT count(*)::integer INTO claimed_count
    FROM deltallm_accounting_claim_permits_batch(p_generation,p_items);
    IF claimed_count<>jsonb_array_length(p_items) THEN
        RAISE EXCEPTION 'accounting_local_finalization_claim_result' USING ERRCODE = 'P0001';
    END IF;

    SELECT jsonb_agg(entry->'finalization' ORDER BY entry#>>'{finalization,operation_id}')
    INTO STRICT terminal_items FROM jsonb_array_elements(p_items) entry;
    RETURN QUERY
    SELECT * FROM deltallm_accounting_finalize_grant_batch(p_generation,terminal_items);
END;
$$;

-- Unknown locally issued capacity has no request identity after owner loss. It is
-- therefore charged at the grant level instead of being released as unused capacity.
CREATE OR REPLACE FUNCTION deltallm_accounting_reconcile_grants(
    p_generation BIGINT,p_limit INTEGER
) RETURNS INTEGER
LANGUAGE plpgsql AS $$
DECLARE
    reconciled_count INTEGER;
    candidate_ids TEXT[];
BEGIN
    IF p_limit NOT BETWEEN 1 AND 256 THEN
        RAISE EXCEPTION 'accounting_grant_reconcile_limit' USING ERRCODE = 'P0001';
    END IF;
    UPDATE deltallm_accounting_grants SET state='draining',updated_at=NOW()
        WHERE protocol_name='primary' AND generation=p_generation
          AND state='active' AND expires_at<=CURRENT_TIMESTAMP;
    SELECT array_agg(candidate.grant_id ORDER BY candidate.grant_id)
    INTO candidate_ids
    FROM (
        SELECT g.grant_id FROM deltallm_accounting_grants g
        WHERE g.protocol_name='primary' AND g.generation=p_generation
          AND g.state='draining'
          AND NOT EXISTS (
              SELECT 1 FROM deltallm_billing_operations b
              WHERE b.accounting_grant_id=g.grant_id
                AND b.accounting_state='reserved'
          )
        ORDER BY g.expires_at,g.grant_id FOR UPDATE SKIP LOCKED LIMIT p_limit
    ) candidate;
    IF candidate_ids IS NULL THEN
        RETURN 0;
    END IF;
    PERFORM 1 FROM deltallm_accounting_budget_windows w
        WHERE w.window_id IN (
            SELECT gw.window_id FROM deltallm_accounting_grant_windows gw
            WHERE gw.grant_id=ANY(candidate_ids)
        ) ORDER BY w.window_id FOR UPDATE;
    PERFORM 1 FROM deltallm_accounting_partitions p
        WHERE (p.protocol_name,p.generation,p.partition_id) IN (
            SELECT g.protocol_name,g.generation,g.accounting_partition
            FROM deltallm_accounting_grants g WHERE g.grant_id=ANY(candidate_ids)
        ) ORDER BY p.protocol_name,p.generation,p.partition_id FOR UPDATE;
    IF EXISTS (
        SELECT 1
        FROM deltallm_accounting_partitions p
        JOIN (
            SELECT protocol_name,generation,accounting_partition,
                   sum(operation_limit)::integer AS slots
            FROM deltallm_accounting_grants
            WHERE grant_id=ANY(candidate_ids)
            GROUP BY protocol_name,generation,accounting_partition
        ) d ON d.protocol_name=p.protocol_name AND d.generation=p.generation
             AND d.accounting_partition=p.partition_id
        WHERE p.outstanding_count<d.slots
    ) THEN
        RAISE EXCEPTION 'accounting_partition_drift' USING ERRCODE = 'P0001';
    END IF;
    WITH aggregates AS MATERIALIZED (
        SELECT gw.grant_id,gw.window_id,gw.allocated_exact,
               COALESCE(sum(r.committed_exact),0)::numeric AS committed_exact,
               (
                   COALESCE(sum(r.provisional_exact),0)
                   + CASE WHEN g.local_dispatch THEN greatest(
                       g.allocated_exact-g.consumed_exact-g.returned_exact,0
                   ) ELSE 0 END
               )::numeric AS provisional_exact
        FROM deltallm_accounting_grant_windows gw
        JOIN deltallm_accounting_grants g ON g.grant_id=gw.grant_id
        LEFT JOIN deltallm_accounting_reservations r
          ON r.grant_id=gw.grant_id AND r.window_id=gw.window_id
        WHERE gw.grant_id=ANY(candidate_ids)
        GROUP BY gw.grant_id,gw.window_id,gw.allocated_exact,g.local_dispatch,
                 g.allocated_exact,g.consumed_exact,g.returned_exact
    ), window_deltas AS MATERIALIZED (
        SELECT window_id,sum(allocated_exact)::numeric AS allocated_exact,
               sum(committed_exact)::numeric AS committed_exact,
               sum(provisional_exact)::numeric AS provisional_exact
        FROM aggregates GROUP BY window_id
    ), partition_deltas AS MATERIALIZED (
        SELECT protocol_name,generation,accounting_partition,
               sum(operation_limit)::integer AS slots
        FROM deltallm_accounting_grants
        WHERE grant_id=ANY(candidate_ids)
        GROUP BY protocol_name,generation,accounting_partition
    ), updated_windows AS (
        UPDATE deltallm_accounting_budget_windows w SET
            reserved_exact=w.reserved_exact-d.allocated_exact,
            committed_exact=w.committed_exact+d.committed_exact,
            provisional_exact=w.provisional_exact+d.provisional_exact,
            updated_at=NOW()
        FROM window_deltas d WHERE w.window_id=d.window_id
        RETURNING w.window_id
    ), updated_partitions AS (
        UPDATE deltallm_accounting_partitions p SET
            outstanding_count=p.outstanding_count-d.slots,updated_at=NOW()
        FROM partition_deltas d
        WHERE p.protocol_name=d.protocol_name AND p.generation=d.generation
          AND p.partition_id=d.accounting_partition
        RETURNING p.partition_id
    ), closed AS (
        UPDATE deltallm_accounting_grants g SET
            state='closed',
            unknown_provisional_exact=CASE WHEN local_dispatch THEN greatest(
                allocated_exact-consumed_exact-returned_exact,0
            ) ELSE 0 END,
            settled_exact=consumed_exact+CASE WHEN local_dispatch THEN greatest(
                allocated_exact-consumed_exact-returned_exact,0
            ) ELSE 0 END,
            settled_operations=consumed_operations+CASE WHEN local_dispatch THEN greatest(
                operation_limit-consumed_operations-returned_operations,0
            ) ELSE 0 END,
            reconciled_at=NOW(),updated_at=NOW()
        WHERE g.grant_id=ANY(candidate_ids)
        RETURNING g.grant_id
    ) SELECT count(*)::integer INTO reconciled_count FROM closed;
    RETURN reconciled_count;
END;
$$;

-- Returned local suffixes cannot be claimed later. Non-local grants retain
-- the existing bounded claim behavior because their returned capacity is zero.
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
        WHERE b.operation_id=ANY(ARRAY(
            SELECT value->>'operation_id' FROM jsonb_array_elements(p_items) value
        )) ORDER BY b.operation_id FOR UPDATE;

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
           OR (grant_row.local_dispatch AND (item->>'permit_ordinal')::integer
                >=grant_row.operation_limit-grant_row.returned_operations)
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
    WHERE (
        SELECT existing.operation_id FROM deltallm_billing_operations existing
        WHERE existing.operation_id=item.value->>'operation_id' LIMIT 1
    ) IS NULL;

    IF new_operation_count>0 THEN
        UPDATE deltallm_accounting_grants g SET
            consumed_operations=g.consumed_operations+new_operation_count,
            consumed_exact=g.consumed_exact+new_exact,
            state=CASE
                WHEN g.consumed_operations+new_operation_count+g.returned_operations
                    >=g.operation_limit
                  OR (g.allocated_exact>0 AND g.consumed_exact+new_exact>=g.allocated_exact)
                THEN 'draining' ELSE g.state END,
            updated_at=NOW()
        WHERE g.grant_id=p_grant_id AND g.state='active'
          AND g.consumed_operations+new_operation_count+g.returned_operations<=g.operation_limit
          AND g.consumed_exact+new_exact+g.returned_exact<=g.allocated_exact;
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
        WHERE (
        SELECT existing.operation_id FROM deltallm_billing_operations existing
        WHERE existing.operation_id=raw_items.reservation_item->>'operation_id' LIMIT 1
    ) IS NULL
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
    FROM raw_items
    CROSS JOIN LATERAL (
        SELECT existing.operation_id FROM deltallm_billing_operations existing
        WHERE existing.operation_id=raw_items.reservation_item->>'operation_id' OFFSET 0
    ) existing
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
