-- New, inactive terminal lane. Compact replay proofs outlive pending documents.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

CREATE TABLE deltallm_accounting_terminal_capacity (
    protocol_name TEXT NOT NULL DEFAULT 'primary',
    generation BIGINT NOT NULL,
    accounting_partition INTEGER NOT NULL,
    max_entries INTEGER NOT NULL CHECK (max_entries BETWEEN 1 AND 1000000),
    max_bytes BIGINT NOT NULL DEFAULT 67108864 CHECK (max_bytes BETWEEN 1048576 AND 67108864),
    pending_entries INTEGER NOT NULL DEFAULT 0 CHECK (pending_entries>=0 AND pending_entries<=max_entries),
    pending_bytes BIGINT NOT NULL DEFAULT 0 CHECK (pending_bytes>=0 AND pending_bytes<=max_bytes),
    PRIMARY KEY (protocol_name,generation,accounting_partition),
    FOREIGN KEY (protocol_name,generation,accounting_partition)
        REFERENCES deltallm_accounting_partitions(protocol_name,generation,partition_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);

CREATE TABLE deltallm_accounting_terminal_journal (
    sequence BIGSERIAL PRIMARY KEY,
    protocol_name TEXT NOT NULL DEFAULT 'primary',
    generation BIGINT NOT NULL,
    operation_id TEXT NOT NULL UNIQUE,
    grant_id TEXT NOT NULL REFERENCES deltallm_accounting_grants(grant_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    grantee_id TEXT NOT NULL CHECK (length(grantee_id) BETWEEN 1 AND 256),
    fence_token UUID NOT NULL,
    permit_ordinal INTEGER NOT NULL CHECK (permit_ordinal BETWEEN 0 AND 1023),
    accounting_partition INTEGER NOT NULL,
    allowance_exact NUMERIC(38,18) NOT NULL CHECK (allowance_exact>=0),
    outcome TEXT NOT NULL CHECK (outcome IN ('completed','not_dispatched','uncertain')),
    reservation_sha256 BYTEA NOT NULL CHECK (octet_length(reservation_sha256)=32),
    finalization_sha256 BYTEA NOT NULL CHECK (octet_length(finalization_sha256)=32),
    payload_bytes INTEGER NOT NULL CHECK (payload_bytes BETWEEN 4 AND 524288),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','completed','failed')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5),
    lease_owner TEXT,
    lease_token UUID,
    lease_expires_at TIMESTAMPTZ,
    materialized_event_sequence BIGINT,
    accepted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    last_error_code TEXT CHECK (length(last_error_code) BETWEEN 1 AND 128),
    UNIQUE (grant_id,permit_ordinal),
    FOREIGN KEY (protocol_name,generation,accounting_partition)
        REFERENCES deltallm_accounting_terminal_capacity(protocol_name,generation,accounting_partition)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CHECK ((status='processing')=(lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)),
    CHECK (status='processing' OR (lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL)),
    CHECK ((status='completed')=(materialized_event_sequence IS NOT NULL AND completed_at IS NOT NULL))
);
CREATE INDEX deltallm_accounting_terminal_pending_idx
    ON deltallm_accounting_terminal_journal(generation,sequence) WHERE status='pending';
CREATE INDEX deltallm_accounting_terminal_expired_idx
    ON deltallm_accounting_terminal_journal(generation,lease_expires_at,sequence) WHERE status='processing';
CREATE INDEX deltallm_accounting_terminal_claim_idx
    ON deltallm_accounting_terminal_journal(generation,lease_owner,lease_token,sequence) WHERE status='processing';
CREATE INDEX deltallm_accounting_terminal_unsettled_grant_idx
    ON deltallm_accounting_terminal_journal(grant_id,permit_ordinal) WHERE status<>'completed';

CREATE TABLE deltallm_accounting_terminal_payloads (
    journal_sequence BIGINT PRIMARY KEY REFERENCES deltallm_accounting_terminal_journal(sequence)
        ON DELETE CASCADE ON UPDATE RESTRICT,
    reservation_payload TEXT NOT NULL CHECK (octet_length(reservation_payload) BETWEEN 2 AND 131072),
    finalization_payload TEXT NOT NULL CHECK (octet_length(finalization_payload) BETWEEN 2 AND 393216),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE FUNCTION deltallm_accounting_append_terminal_journal(
    p_generation BIGINT,p_items JSONB,p_reservations TEXT[],p_finalizations TEXT[]
) RETURNS TABLE(operation_id TEXT,journal_sequence BIGINT,outcome TEXT,replayed BOOLEAN)
LANGUAGE plpgsql AS $$
DECLARE
    item JSONB;
    position INTEGER;
    protocol_state TEXT;
    grant_row deltallm_accounting_grants%ROWTYPE;
    prior deltallm_accounting_terminal_journal%ROWTYPE;
    inserted_sequence BIGINT;
    bytes_value INTEGER;
BEGIN
    IF p_generation IS NULL OR p_generation<1
       OR COALESCE(jsonb_typeof(p_items),'null')<>'array' THEN
        RAISE EXCEPTION 'accounting_terminal_batch_shape' USING ERRCODE='P0001';
    END IF;
    IF jsonb_array_length(p_items) NOT BETWEEN 1 AND 256
       OR cardinality(p_reservations) IS DISTINCT FROM jsonb_array_length(p_items)
       OR cardinality(p_finalizations) IS DISTINCT FROM jsonb_array_length(p_items)
       OR array_lower(p_reservations,1) IS DISTINCT FROM 1
       OR array_lower(p_finalizations,1) IS DISTINCT FROM 1
       OR COALESCE(octet_length(p_items::text)+octet_length(array_to_json(p_reservations)::text)
                  +octet_length(array_to_json(p_finalizations)::text),2097153)>2097152 THEN
        RAISE EXCEPTION 'accounting_terminal_batch_shape' USING ERRCODE='P0001';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_items) WITH ORDINALITY v(value,n)
        WHERE COALESCE(jsonb_typeof(value),'null')<>'object'
          OR COALESCE(length(value->>'operation_id'),0) NOT BETWEEN 1 AND 256
          OR (value->>'operation_id')::uuid::text IS DISTINCT FROM value->>'operation_id'
          OR COALESCE(length(value->>'grant_id'),0) NOT BETWEEN 1 AND 256
          OR COALESCE(length(value->>'grantee_id'),0) NOT BETWEEN 1 AND 256
          OR value->>'fence_token' IS NULL OR value->>'expires_at' IS NULL
          OR COALESCE((value->>'permit_ordinal')::integer,-1) NOT BETWEEN 0 AND 1023
          OR value->>'allowance_exact' IS NULL
          OR COALESCE(jsonb_typeof(value->'subject'),'null')<>'object'
          OR COALESCE(jsonb_typeof(value#>'{subject,attribution}'),'null')<>'object'
          OR COALESCE(jsonb_typeof(value#>'{subject,windows}'),'null')<>'array'
          OR COALESCE(length(value#>>'{subject,attribution,api_key}'),0) NOT BETWEEN 1 AND 256
          OR COALESCE(length(value#>>'{subject,attribution,model}'),0) NOT BETWEEN 1 AND 256
          OR (value#>>'{subject,allowance}')::numeric IS DISTINCT FROM (value->>'allowance_exact')::numeric
          OR (value->>'allowance_exact')::numeric<0
          OR (value->>'allowance_exact')::numeric>=100000000000000000000
          OR (value->>'allowance_exact')::numeric IS DISTINCT FROM (value->>'allowance_exact')::numeric(38,18)
          OR COALESCE(value->>'outcome','') NOT IN ('completed','not_dispatched','uncertain')
          OR COALESCE(value->>'reservation_sha256','') !~ '^[0-9a-f]{64}$'
          OR COALESCE(value->>'finalization_sha256','') !~ '^[0-9a-f]{64}$'
          OR COALESCE(octet_length(p_reservations[n]),0) NOT BETWEEN 2 AND 131072
          OR COALESCE(octet_length(p_finalizations[n]),0) NOT BETWEEN 2 AND 393216
          OR sha256(convert_to(p_reservations[n],'UTF8')) IS DISTINCT FROM decode(value->>'reservation_sha256','hex')
          OR sha256(convert_to(p_finalizations[n],'UTF8')) IS DISTINCT FROM decode(value->>'finalization_sha256','hex')
    ) OR EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_items) v GROUP BY v->>'operation_id' HAVING count(*)>1
    ) OR EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_items) v
        GROUP BY v->>'grant_id',(v->>'permit_ordinal')::integer HAVING count(*)>1
    ) THEN
        RAISE EXCEPTION 'accounting_terminal_item_shape' USING ERRCODE='P0001';
    END IF;
    SELECT state INTO STRICT protocol_state FROM deltallm_accounting_protocols
        WHERE protocol_name='primary' AND generation=p_generation AND writer_version=2 FOR SHARE;
    IF protocol_state NOT IN ('active','draining') THEN
        RAISE EXCEPTION 'accounting_protocol_not_finalizable' USING ERRCODE='P0001';
    END IF;
    -- Use the same operation fence and global grant order as direct claims.
    FOR item IN SELECT v FROM jsonb_array_elements(p_items) v ORDER BY v->>'operation_id' LOOP
        PERFORM pg_advisory_xact_lock(hashtextextended('accounting-permit-claim:'||(item->>'operation_id'),0));
    END LOOP;
    PERFORM 1 FROM deltallm_accounting_grants g
        WHERE g.grant_id=ANY(ARRAY(SELECT v->>'grant_id' FROM jsonb_array_elements(p_items) v))
        ORDER BY g.grant_id FOR UPDATE;
    INSERT INTO deltallm_accounting_terminal_capacity(protocol_name,generation,accounting_partition,max_entries)
        SELECT DISTINCT g.protocol_name,g.generation,g.accounting_partition,p.max_outstanding
        FROM unnest(ARRAY(SELECT DISTINCT v->>'grant_id' FROM jsonb_array_elements(p_items) v)) key
        CROSS JOIN LATERAL (SELECT g.* FROM deltallm_accounting_grants g WHERE g.grant_id=key OFFSET 0) g
        JOIN deltallm_accounting_partitions p ON p.protocol_name=g.protocol_name
          AND p.generation=g.generation AND p.partition_id=g.accounting_partition
        ORDER BY g.protocol_name,g.generation,g.accounting_partition ON CONFLICT DO NOTHING;
    PERFORM 1 FROM deltallm_accounting_terminal_capacity c
        WHERE c.protocol_name='primary' AND c.generation=p_generation
          AND c.accounting_partition=ANY(ARRAY(
            SELECT g.accounting_partition FROM unnest(ARRAY(SELECT DISTINCT v->>'grant_id' FROM jsonb_array_elements(p_items) v)) key
            CROSS JOIN LATERAL (SELECT g.accounting_partition FROM deltallm_accounting_grants g WHERE g.grant_id=key OFFSET 0) g
          )) ORDER BY c.accounting_partition FOR UPDATE;
    FOR item,position IN SELECT v,n::integer FROM jsonb_array_elements(p_items) WITH ORDINALITY x(v,n)
                         ORDER BY v->>'operation_id' LOOP
        SELECT * INTO prior FROM deltallm_accounting_terminal_journal j WHERE j.operation_id=item->>'operation_id';
        IF FOUND THEN
            IF prior.generation IS DISTINCT FROM p_generation
               OR prior.grant_id IS DISTINCT FROM item->>'grant_id'
               OR prior.grantee_id IS DISTINCT FROM item->>'grantee_id'
               OR prior.fence_token IS DISTINCT FROM (item->>'fence_token')::uuid
               OR prior.permit_ordinal IS DISTINCT FROM (item->>'permit_ordinal')::integer
               OR prior.allowance_exact IS DISTINCT FROM (item->>'allowance_exact')::numeric
               OR prior.outcome IS DISTINCT FROM item->>'outcome'
               OR prior.reservation_sha256 IS DISTINCT FROM decode(item->>'reservation_sha256','hex')
               OR prior.finalization_sha256 IS DISTINCT FROM decode(item->>'finalization_sha256','hex') THEN
                RAISE EXCEPTION 'accounting_terminal_replay_conflict' USING ERRCODE='P0001';
            END IF;
            operation_id:=prior.operation_id; journal_sequence:=prior.sequence; outcome:=prior.outcome; replayed:=TRUE;
            RETURN NEXT; CONTINUE;
        END IF;
        SELECT * INTO STRICT grant_row FROM deltallm_accounting_grants g WHERE g.grant_id=item->>'grant_id';
        IF grant_row.protocol_name IS DISTINCT FROM 'primary' OR grant_row.generation IS DISTINCT FROM p_generation
           OR grant_row.grantee_id IS DISTINCT FROM item->>'grantee_id'
           OR grant_row.fence_token IS DISTINCT FROM (item->>'fence_token')::uuid
           OR grant_row.dispatch_mode IS DISTINCT FROM 'preissued' OR NOT grant_row.local_dispatch
           OR grant_row.state NOT IN ('active','draining') OR grant_row.expires_at<=CURRENT_TIMESTAMP
           OR (item->>'expires_at')::timestamptz<=CURRENT_TIMESTAMP
           OR (item->>'expires_at')::timestamptz>grant_row.expires_at
           OR grant_row.unit_allowance_exact IS DISTINCT FROM (item->>'allowance_exact')::numeric
           OR grant_row.subject_key IS DISTINCT FROM deltallm_accounting_grant_subject(item->'subject')
           OR (item->>'permit_ordinal')::integer>=grant_row.operation_limit-grant_row.returned_operations
           OR EXISTS (SELECT 1 FROM deltallm_billing_operations b WHERE b.operation_id=item->>'operation_id')
           OR EXISTS (SELECT 1 FROM deltallm_billing_operations b WHERE b.accounting_grant_id=grant_row.grant_id
                        AND b.accounting_permit_ordinal=(item->>'permit_ordinal')::integer) THEN
            RAISE EXCEPTION 'accounting_terminal_grant_identity' USING ERRCODE='P0001';
        END IF;
        bytes_value:=octet_length(p_reservations[position])+octet_length(p_finalizations[position]);
        UPDATE deltallm_accounting_terminal_capacity c SET pending_entries=c.pending_entries+1,pending_bytes=c.pending_bytes+bytes_value
            WHERE c.protocol_name='primary' AND c.generation=p_generation AND c.accounting_partition=grant_row.accounting_partition
              AND c.pending_entries<c.max_entries AND c.pending_bytes+bytes_value<=c.max_bytes;
        IF NOT FOUND THEN RAISE EXCEPTION 'accounting_terminal_capacity' USING ERRCODE='P0001'; END IF;
        INSERT INTO deltallm_accounting_terminal_journal AS j(generation,operation_id,grant_id,grantee_id,fence_token,
            permit_ordinal,accounting_partition,allowance_exact,outcome,reservation_sha256,finalization_sha256,payload_bytes)
        VALUES (p_generation,item->>'operation_id',grant_row.grant_id,grant_row.grantee_id,grant_row.fence_token,
            (item->>'permit_ordinal')::integer,grant_row.accounting_partition,grant_row.unit_allowance_exact,item->>'outcome',
            decode(item->>'reservation_sha256','hex'),decode(item->>'finalization_sha256','hex'),bytes_value)
        RETURNING j.sequence INTO inserted_sequence;
        INSERT INTO deltallm_accounting_terminal_payloads(journal_sequence,reservation_payload,finalization_payload)
            VALUES (inserted_sequence,p_reservations[position],p_finalizations[position]);
        operation_id:=item->>'operation_id'; journal_sequence:=inserted_sequence; outcome:=item->>'outcome'; replayed:=FALSE;
        RETURN NEXT;
    END LOOP;
END;
$$;

-- A return or manual close shares the grant lock with append. Retained journal
-- entries remain funded; they cannot be reclaimed as an unused local suffix.
CREATE FUNCTION deltallm_accounting_guard_terminal_grant() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.returned_operations>OLD.returned_operations AND EXISTS (
        SELECT 1 FROM deltallm_accounting_terminal_journal j WHERE j.grant_id=NEW.grant_id
          AND j.permit_ordinal>=NEW.operation_limit-NEW.returned_operations
    ) THEN RAISE EXCEPTION 'accounting_terminal_return_accepted' USING ERRCODE='P0001'; END IF;
    IF NEW.state='closed' AND OLD.state<>'closed' AND EXISTS (
        SELECT 1 FROM deltallm_accounting_terminal_journal j WHERE j.grant_id=NEW.grant_id AND j.status<>'completed'
    ) THEN RAISE EXCEPTION 'accounting_terminal_close_pending' USING ERRCODE='P0001'; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER deltallm_accounting_terminal_grant_guard
    BEFORE UPDATE OF returned_operations,state ON deltallm_accounting_grants
    FOR EACH ROW WHEN (OLD.local_dispatch) EXECUTE FUNCTION deltallm_accounting_guard_terminal_grant();

-- Each expiry and close pass has a fixed candidate cap. Pending accepted
-- terminals keep their grant reserved; failed rows are never ignored.
CREATE OR REPLACE FUNCTION deltallm_accounting_reconcile_grants(
    p_generation BIGINT,p_limit INTEGER
) RETURNS INTEGER
LANGUAGE plpgsql AS $$
DECLARE
    reconciled_count INTEGER;
    candidate_ids TEXT[];
BEGIN
    IF p_generation IS NULL OR COALESCE(p_limit,0) NOT BETWEEN 1 AND 256 THEN
        RAISE EXCEPTION 'accounting_grant_reconcile_limit' USING ERRCODE = 'P0001';
    END IF;
    WITH expired AS MATERIALIZED (
        SELECT g.grant_id FROM deltallm_accounting_grants g
        WHERE g.protocol_name='primary' AND g.generation=p_generation
          AND g.state='active' AND g.expires_at<=CURRENT_TIMESTAMP
        ORDER BY g.expires_at,g.grant_id FOR UPDATE SKIP LOCKED LIMIT p_limit
    ) UPDATE deltallm_accounting_grants g SET state='draining',updated_at=NOW()
        WHERE g.grant_id=ANY(ARRAY(SELECT grant_id FROM expired));
    SELECT array_agg(candidate.grant_id ORDER BY candidate.grant_id)
    INTO candidate_ids
    FROM (
        SELECT g.grant_id FROM deltallm_accounting_grants g
        WHERE g.protocol_name='primary' AND g.generation=p_generation
          AND g.state='draining'
          AND NOT EXISTS (
              SELECT 1 FROM deltallm_accounting_terminal_journal j
              WHERE j.grant_id=g.grant_id AND j.status<>'completed' OFFSET 0
          )
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

COMMIT;
