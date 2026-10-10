-- Batch commits use one counter update per partition and bulk document inserts.
-- Keep the applied journal foundation unchanged.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

CREATE OR REPLACE FUNCTION deltallm_accounting_append_terminal_journal(
    p_generation BIGINT,p_items JSONB,p_reservations TEXT[],p_finalizations TEXT[]
) RETURNS TABLE(operation_id TEXT,journal_sequence BIGINT,outcome TEXT,replayed BOOLEAN)
LANGUAGE plpgsql AS $$
DECLARE
    item JSONB;
    position INTEGER;
    protocol_state TEXT;
    grant_row deltallm_accounting_grants%ROWTYPE;
    prior deltallm_accounting_terminal_journal%ROWTYPE;
    capacity_available BOOLEAN;
    returned_count INTEGER;
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
            CONTINUE;
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

    END LOOP;
    -- Validate the whole batch before one counter update per partition. Grants
    -- and capacity rows are already locked in a fixed global order.
    WITH additions AS MATERIALIZED (
        SELECT g.accounting_partition,count(*)::integer AS entries,
               sum(octet_length(p_reservations[n])+octet_length(p_finalizations[n]))::bigint AS bytes
        FROM jsonb_array_elements(p_items) WITH ORDINALITY x(v,n)
        CROSS JOIN LATERAL (SELECT g.accounting_partition FROM deltallm_accounting_grants g WHERE g.grant_id=v->>'grant_id' OFFSET 0) g
        WHERE NOT EXISTS (SELECT 1 FROM deltallm_accounting_terminal_journal j WHERE j.operation_id=v->>'operation_id' OFFSET 0)
        GROUP BY g.accounting_partition
    ), charged AS (
        UPDATE deltallm_accounting_terminal_capacity c
        SET pending_entries=c.pending_entries+a.entries,pending_bytes=c.pending_bytes+a.bytes
        FROM additions a WHERE c.protocol_name='primary' AND c.generation=p_generation
          AND c.accounting_partition=a.accounting_partition
          AND c.pending_entries+a.entries<=c.max_entries AND c.pending_bytes+a.bytes<=c.max_bytes
        RETURNING c.accounting_partition
    )
    SELECT (SELECT count(*) FROM charged)=(SELECT count(*) FROM additions)
        INTO capacity_available;
    IF NOT capacity_available THEN
        RAISE EXCEPTION 'accounting_terminal_capacity' USING ERRCODE='P0001';
    END IF;
    RETURN QUERY
    WITH raw AS MATERIALIZED (
        SELECT v,n FROM jsonb_array_elements(p_items) WITH ORDINALITY x(v,n)
    ), inserted AS (
        INSERT INTO deltallm_accounting_terminal_journal AS j(
            generation,operation_id,grant_id,grantee_id,fence_token,permit_ordinal,
            accounting_partition,allowance_exact,outcome,reservation_sha256,finalization_sha256,payload_bytes
        )
        SELECT p_generation,v->>'operation_id',g.grant_id,g.grantee_id,g.fence_token,
               (v->>'permit_ordinal')::integer,g.accounting_partition,g.unit_allowance_exact,v->>'outcome',
               decode(v->>'reservation_sha256','hex'),decode(v->>'finalization_sha256','hex'),
               octet_length(p_reservations[n])+octet_length(p_finalizations[n])
        FROM raw CROSS JOIN LATERAL (
            SELECT g.* FROM deltallm_accounting_grants g WHERE g.grant_id=v->>'grant_id' OFFSET 0
        ) g
        WHERE NOT EXISTS (SELECT 1 FROM deltallm_accounting_terminal_journal existing WHERE existing.operation_id=v->>'operation_id' OFFSET 0)
        RETURNING j.sequence,j.operation_id,j.outcome
    ), payloads AS (
        INSERT INTO deltallm_accounting_terminal_payloads(journal_sequence,reservation_payload,finalization_payload)
        SELECT i.sequence,p_reservations[raw.n],p_finalizations[raw.n]
        FROM inserted i JOIN raw ON raw.v->>'operation_id'=i.operation_id
        RETURNING journal_sequence
    ), effects AS MATERIALIZED (SELECT count(*) AS documents FROM payloads)
    SELECT i.operation_id,i.sequence,i.outcome,FALSE FROM inserted i CROSS JOIN effects
    UNION ALL
    SELECT j.operation_id,j.sequence,j.outcome,TRUE FROM raw CROSS JOIN LATERAL (
        SELECT j.operation_id,j.sequence,j.outcome FROM deltallm_accounting_terminal_journal j
        WHERE j.operation_id=raw.v->>'operation_id' OFFSET 0
    ) j;
    GET DIAGNOSTICS returned_count=ROW_COUNT;
    IF returned_count<>jsonb_array_length(p_items) THEN
        RAISE EXCEPTION 'accounting_terminal_incomplete_result' USING ERRCODE='P0001';
    END IF;
END;
$$;

COMMIT;

