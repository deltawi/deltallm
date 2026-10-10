-- Terminal processing stays separate from API acceptance. Failed rows remain
-- durable, funded, and charged against the same pending entry and byte limits.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

ALTER TABLE deltallm_accounting_terminal_capacity ADD COLUMN failed_entries INTEGER NOT NULL DEFAULT 0
    CHECK (failed_entries BETWEEN 0 AND pending_entries);

CREATE FUNCTION deltallm_accounting_claim_terminal_journal(
    p_generation BIGINT,p_worker_id TEXT,p_lease_token UUID,p_lease_seconds INTEGER,p_limit INTEGER
) RETURNS TABLE(journal_sequence BIGINT)
LANGUAGE plpgsql AS $$
DECLARE
    candidate_ids BIGINT[];
    chosen_ids BIGINT[];
BEGIN
    IF p_generation IS NULL OR p_generation<1 OR COALESCE(length(p_worker_id),0) NOT BETWEEN 1 AND 256
       OR p_lease_token IS NULL OR COALESCE(p_lease_seconds,0) NOT BETWEEN 5 AND 300
       OR COALESCE(p_limit,0) NOT BETWEEN 1 AND 256 THEN
        RAISE EXCEPTION 'accounting_terminal_claim_shape' USING ERRCODE='P0001';
    END IF;
    IF EXISTS (SELECT 1 FROM deltallm_accounting_terminal_journal j WHERE j.generation=p_generation
        AND j.status='processing' AND j.lease_owner=p_worker_id AND j.lease_token=p_lease_token) THEN
        RETURN QUERY SELECT j.sequence FROM deltallm_accounting_terminal_journal j
            WHERE j.generation=p_generation AND j.status='processing' AND j.lease_owner=p_worker_id
              AND j.lease_token=p_lease_token AND j.lease_expires_at>clock_timestamp()
            ORDER BY j.sequence LIMIT p_limit;
        RETURN;
    END IF;
    -- Each index branch inspects at most one entry slice. Do not sort all
    -- retained pending and expired work before applying the limit.
    SELECT array_agg(pick.sequence ORDER BY pick.sequence) INTO candidate_ids FROM (
        (SELECT j.sequence FROM deltallm_accounting_terminal_journal j
         WHERE j.generation=p_generation AND j.status='pending'
         ORDER BY j.sequence FOR UPDATE SKIP LOCKED LIMIT p_limit)
        UNION ALL
        (SELECT j.sequence FROM deltallm_accounting_terminal_journal j
         WHERE j.generation=p_generation AND j.status='processing' AND j.lease_expires_at<=statement_timestamp()
         ORDER BY j.lease_expires_at,j.sequence FOR UPDATE SKIP LOCKED LIMIT p_limit)
    ) pick;
    PERFORM 1 FROM deltallm_accounting_terminal_capacity c WHERE c.protocol_name='primary' AND c.generation=p_generation
        AND c.accounting_partition=ANY(ARRAY(SELECT j.accounting_partition FROM deltallm_accounting_terminal_journal j
            WHERE j.sequence=ANY(candidate_ids))) ORDER BY c.accounting_partition FOR UPDATE;
    WITH failed AS (
        UPDATE deltallm_accounting_terminal_journal j SET status='failed',lease_owner=NULL,lease_token=NULL,
            lease_expires_at=NULL,last_error_code='lease_attempts_exhausted',updated_at=clock_timestamp()
        WHERE j.sequence=ANY(candidate_ids) AND j.attempts>=5
        RETURNING j.accounting_partition
    ), deltas AS (
        SELECT accounting_partition,count(*)::integer AS entries FROM failed GROUP BY accounting_partition
    ) UPDATE deltallm_accounting_terminal_capacity c SET failed_entries=c.failed_entries+d.entries
        FROM deltas d WHERE c.protocol_name='primary' AND c.generation=p_generation AND c.accounting_partition=d.accounting_partition;
    SELECT array_agg(pick.sequence ORDER BY pick.sequence) INTO chosen_ids FROM (
        SELECT j.sequence,sum(j.payload_bytes) OVER (ORDER BY j.sequence) AS bytes,
            row_number() OVER (ORDER BY j.sequence) AS ordinal
        FROM deltallm_accounting_terminal_journal j WHERE j.sequence=ANY(candidate_ids) AND j.attempts<5
    ) pick WHERE pick.ordinal<=p_limit AND pick.bytes<=1048576;
    UPDATE deltallm_accounting_terminal_journal j SET status='processing',attempts=j.attempts+1,
        lease_owner=p_worker_id,lease_token=p_lease_token,lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),
        last_error_code=NULL,updated_at=clock_timestamp() WHERE j.sequence=ANY(chosen_ids);
    RETURN QUERY SELECT j.sequence FROM deltallm_accounting_terminal_journal j WHERE j.sequence=ANY(chosen_ids) ORDER BY j.sequence;
END;
$$;

CREATE FUNCTION deltallm_accounting_fail_terminal_journal(
    p_generation BIGINT,p_worker_id TEXT,p_lease_token UUID,p_sequences BIGINT[],p_error TEXT
) RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE
    changed INTEGER;
BEGIN
    IF p_generation IS NULL OR p_generation<1 OR COALESCE(length(p_worker_id),0) NOT BETWEEN 1 AND 256
       OR p_lease_token IS NULL OR COALESCE(cardinality(p_sequences),0) NOT BETWEEN 1 AND 256
       OR COALESCE(p_error,'') NOT IN ('invalid_payload','persistence_unavailable') THEN
        RAISE EXCEPTION 'accounting_terminal_failure_shape' USING ERRCODE='P0001';
    END IF;
    PERFORM 1 FROM deltallm_accounting_terminal_journal j WHERE j.sequence=ANY(p_sequences)
        ORDER BY j.sequence FOR UPDATE;
    -- Failed entry counts use the same sorted partition locks as a commit.
    PERFORM 1 FROM deltallm_accounting_terminal_capacity c WHERE c.protocol_name='primary' AND c.generation=p_generation
      AND c.accounting_partition=ANY(ARRAY(SELECT j.accounting_partition FROM deltallm_accounting_terminal_journal j
        WHERE j.sequence=ANY(p_sequences))) ORDER BY c.accounting_partition FOR UPDATE;
    WITH updated AS (
        UPDATE deltallm_accounting_terminal_journal j SET
            status=CASE WHEN j.attempts>=5 THEN 'failed' ELSE 'pending' END,
            lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,last_error_code=p_error,updated_at=clock_timestamp()
        WHERE j.sequence=ANY(p_sequences) AND j.generation=p_generation AND j.status='processing'
          AND j.lease_owner=p_worker_id AND j.lease_token=p_lease_token AND j.lease_expires_at>clock_timestamp()
        RETURNING j.accounting_partition,j.status
    ), deltas AS MATERIALIZED (
        SELECT accounting_partition,count(*)::integer AS entries FROM updated WHERE status='failed' GROUP BY accounting_partition
    ), charged AS (
        UPDATE deltallm_accounting_terminal_capacity c SET failed_entries=c.failed_entries+d.entries
        FROM deltas d WHERE c.protocol_name='primary' AND c.generation=p_generation AND c.accounting_partition=d.accounting_partition
        RETURNING c.accounting_partition
    ) SELECT count(*)::integer INTO changed FROM updated CROSS JOIN (SELECT count(*) FROM charged) effects;
    RETURN changed;
END;
$$;

CREATE FUNCTION deltallm_accounting_materialize_terminal_journal(
    p_generation BIGINT,p_worker_id TEXT,p_lease_token UUID,p_sequences BIGINT[]
) RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE
    entry deltallm_accounting_terminal_journal%ROWTYPE;
    reservation JSONB;
    terminal JSONB;
    reservation_text TEXT;
    terminal_text TEXT;
    grant_row deltallm_accounting_grants%ROWTYPE;
    committed NUMERIC(38,18);
    provisional NUMERIC(38,18);
    released NUMERIC(38,18);
    count_value INTEGER;
    earliest_deadline TIMESTAMPTZ;
    prepared JSONB[]:=ARRAY[]::jsonb[];
BEGIN
    IF p_generation IS NULL OR p_generation<1 OR COALESCE(length(p_worker_id),0) NOT BETWEEN 1 AND 256 OR p_lease_token IS NULL
       OR COALESCE(cardinality(p_sequences),0) NOT BETWEEN 1 AND 256
       OR EXISTS (SELECT 1 FROM unnest(p_sequences) key WHERE key IS NULL OR key<1)
       OR (SELECT count(DISTINCT key) FROM unnest(p_sequences) key)<>cardinality(p_sequences) THEN
        RAISE EXCEPTION 'accounting_terminal_materialize_shape' USING ERRCODE='P0001';
    END IF;
    PERFORM 1 FROM deltallm_accounting_terminal_journal j WHERE j.sequence=ANY(p_sequences) ORDER BY j.sequence FOR UPDATE;
    SELECT count(*)::integer INTO count_value FROM deltallm_accounting_terminal_journal j
        WHERE j.sequence=ANY(p_sequences) AND j.generation=p_generation AND j.status='completed';
    IF count_value=cardinality(p_sequences) THEN RETURN count_value; END IF;
    SELECT count(*)::integer INTO count_value FROM deltallm_accounting_terminal_journal j
        WHERE j.sequence=ANY(p_sequences) AND j.generation=p_generation AND j.status='processing'
          AND j.lease_owner=p_worker_id AND j.lease_token=p_lease_token AND j.lease_expires_at>clock_timestamp();
    IF count_value<>cardinality(p_sequences) THEN RETURN 0; END IF;
    IF (SELECT sum(j.payload_bytes) FROM deltallm_accounting_terminal_journal j WHERE j.sequence=ANY(p_sequences))>1048576 THEN
        RAISE EXCEPTION 'accounting_terminal_materialize_bytes' USING ERRCODE='P0001';
    END IF;
    SELECT min(j.lease_expires_at) INTO STRICT earliest_deadline FROM deltallm_accounting_terminal_journal j WHERE j.sequence=ANY(p_sequences);
    -- Fence operations before globally sorted grant and partition locks.
    FOR entry IN SELECT j.* FROM deltallm_accounting_terminal_journal j WHERE j.sequence=ANY(p_sequences) ORDER BY j.operation_id LOOP
        PERFORM pg_advisory_xact_lock(hashtextextended('accounting-permit-claim:'||entry.operation_id,0));
    END LOOP;
    PERFORM 1 FROM deltallm_accounting_grants g WHERE g.grant_id=ANY(ARRAY(
        SELECT j.grant_id FROM deltallm_accounting_terminal_journal j WHERE j.sequence=ANY(p_sequences))) ORDER BY g.grant_id FOR UPDATE;
    PERFORM 1 FROM deltallm_accounting_terminal_capacity c WHERE c.protocol_name='primary' AND c.generation=p_generation
        AND c.accounting_partition=ANY(ARRAY(SELECT j.accounting_partition FROM deltallm_accounting_terminal_journal j WHERE j.sequence=ANY(p_sequences)))
        ORDER BY c.accounting_partition FOR UPDATE;
    FOR entry IN SELECT j.* FROM deltallm_accounting_terminal_journal j WHERE j.sequence=ANY(p_sequences) ORDER BY j.sequence LOOP
        SELECT p.reservation_payload,p.finalization_payload INTO STRICT reservation_text,terminal_text
            FROM deltallm_accounting_terminal_payloads p WHERE p.journal_sequence=entry.sequence;
        IF sha256(convert_to(reservation_text,'UTF8')) IS DISTINCT FROM entry.reservation_sha256
           OR sha256(convert_to(terminal_text,'UTF8')) IS DISTINCT FROM entry.finalization_sha256 THEN
            RAISE EXCEPTION 'accounting_terminal_payload_hash' USING ERRCODE='P0001';
        END IF;
        reservation:=reservation_text::jsonb; terminal:=terminal_text::jsonb;
        SELECT * INTO STRICT grant_row FROM deltallm_accounting_grants g WHERE g.grant_id=entry.grant_id;
        IF grant_row.generation IS DISTINCT FROM p_generation OR NOT grant_row.local_dispatch OR grant_row.state NOT IN ('active','draining')
           OR grant_row.grantee_id IS DISTINCT FROM entry.grantee_id OR grant_row.fence_token IS DISTINCT FROM entry.fence_token
           OR grant_row.subject_key IS DISTINCT FROM deltallm_accounting_grant_subject(reservation)
           OR entry.permit_ordinal>=grant_row.operation_limit-grant_row.returned_operations
           OR (reservation->>'protocol_generation')::bigint IS DISTINCT FROM p_generation
           OR (terminal->>'protocol_generation')::bigint IS DISTINCT FROM p_generation
           OR reservation->>'operation_id' IS DISTINCT FROM entry.operation_id OR terminal->>'operation_id' IS DISTINCT FROM entry.operation_id
           OR reservation->>'owner_token' IS NULL OR reservation->>'owner_token' IS DISTINCT FROM terminal->>'owner_token'
           OR COALESCE(reservation->>'request_fingerprint','') !~ '^[0-9a-f]{64}$'
           OR reservation->>'request_fingerprint' IS DISTINCT FROM terminal->>'request_fingerprint'
           OR (reservation->>'allowance')::numeric IS DISTINCT FROM entry.allowance_exact
           OR terminal->>'outcome' IS DISTINCT FROM entry.outcome
           OR COALESCE(length(terminal->>'component_id'),0) NOT BETWEEN 1 AND 256 OR terminal->>'event_id' IS NULL
           OR terminal->>'occurred_at' IS NULL OR reservation->>'expires_at' IS NULL
           OR COALESCE(jsonb_typeof(reservation->'pricing_snapshot'),'null')<>'object'
           OR COALESCE(jsonb_typeof(reservation->'audit_envelope'),'null')<>'object'
           OR COALESCE(jsonb_typeof(terminal->'audit_envelope'),'null')<>'object'
           OR COALESCE((terminal->>'unresolved_attempts')::integer,-1) NOT BETWEEN 0 AND 127 THEN
            RAISE EXCEPTION 'accounting_terminal_payload_identity' USING ERRCODE='P0001';
        END IF;
        committed:=0; provisional:=0; released:=0;
        IF entry.outcome='completed' THEN
            IF terminal->>'exact_charge' IS NULL OR (terminal->>'exact_charge')::numeric<0
               OR (terminal->>'exact_charge')::numeric>entry.allowance_exact
               OR (terminal->>'exact_charge')::numeric IS DISTINCT FROM (terminal->>'exact_charge')::numeric(38,18)
               OR COALESCE(jsonb_typeof(terminal->'spend_payload'),'null')<>'object'
               OR terminal->>'uncertainty_reason' IS NOT NULL THEN
                RAISE EXCEPTION 'accounting_terminal_completed_shape' USING ERRCODE='P0001';
            END IF;
            committed:=(terminal->>'exact_charge')::numeric;
            IF (terminal->>'unresolved_attempts')::integer>0 THEN provisional:=entry.allowance_exact-committed;
            ELSE released:=entry.allowance_exact-committed; END IF;
        ELSIF entry.outcome='uncertain' THEN
            IF terminal->>'exact_charge' IS NOT NULL OR COALESCE(terminal->'spend_payload','null'::jsonb)<>'null'::jsonb
               OR COALESCE(length(terminal->>'uncertainty_reason'),0) NOT BETWEEN 1 AND 256
               OR (terminal->>'unresolved_attempts')::integer<>0 THEN
                RAISE EXCEPTION 'accounting_terminal_uncertain_shape' USING ERRCODE='P0001';
            END IF;
            provisional:=entry.allowance_exact;
        ELSE
            IF COALESCE((terminal->>'exact_charge')::numeric,0)<>0 OR COALESCE(terminal->'spend_payload','null'::jsonb)<>'null'::jsonb
               OR terminal->>'uncertainty_reason' IS NOT NULL OR (terminal->>'unresolved_attempts')::integer<>0 THEN
                RAISE EXCEPTION 'accounting_terminal_released_shape' USING ERRCODE='P0001';
            END IF;
            released:=entry.allowance_exact;
        END IF;
        prepared:=array_append(prepared,jsonb_build_object(
            'sequence',entry.sequence,'operation_id',entry.operation_id,'grant_id',entry.grant_id,
            'partition',entry.accounting_partition,'fence',entry.fence_token,'ordinal',entry.permit_ordinal,
            'allowance',entry.allowance_exact::text,'committed',committed::text,'provisional',provisional::text,
            'released',released::text,'accepted_at',entry.accepted_at,'reservation',reservation,'terminal',terminal
        ));
    END LOOP;
    -- Accepted facts can be processed after expiry. These bulk writes do not
    -- create another provider admission or release a pending result early.
    WITH raw AS MATERIALIZED (
        SELECT v,v->'reservation' AS reservation_json,v->'terminal' AS terminal_json,
            nextval(pg_get_serial_sequence('deltallm_accounting_events','sequence')::regclass) AS event_sequence
        FROM unnest(prepared) v
    ), operations AS (
        INSERT INTO deltallm_billing_operations AS op(
            operation_id,owner_token,api_key,user_id,team_id,organization_id,model,snapshot,
            selector_event_id,selector_allowance,answer_allowance,selector_state,answer_state,answer_receipt,
            closed_at,expires_at,created_at,accounting_protocol,accounting_generation,accounting_partition,
            request_fingerprint,accounting_state,accounting_grant_id,accounting_permit_ordinal,
            accounting_grant_fence_token,provisional_debit_exact,final_event_sequence
        )
        SELECT v->>'operation_id',reservation_json->>'owner_token',reservation_json#>>'{attribution,api_key}',
            reservation_json#>>'{attribution,user_id}',reservation_json#>>'{attribution,team_id}',
            reservation_json#>>'{attribution,organization_id}',reservation_json#>>'{attribution,model}',reservation_json,
            (v->>'operation_id')||':selector',0,(v->>'allowance')::numeric,'unattempted',
            CASE terminal_json->>'outcome' WHEN 'completed' THEN 'settled' WHEN 'not_dispatched' THEN 'unattempted' ELSE 'pending' END,
            CASE WHEN terminal_json->>'outcome'='completed' THEN
                jsonb_build_object('event_id',terminal_json->>'event_id','cost_exact',v->>'committed') ELSE NULL END,
            clock_timestamp(),(reservation_json->>'expires_at')::timestamptz,
            least((v->>'accepted_at')::timestamptz,(reservation_json->>'expires_at')::timestamptz-interval '1 microsecond'),
            'primary',p_generation,(v->>'partition')::integer,reservation_json->>'request_fingerprint',
            CASE WHEN terminal_json->>'outcome'='uncertain' OR (terminal_json->>'unresolved_attempts')::integer>0 THEN 'provisional'
                 WHEN terminal_json->>'outcome'='not_dispatched' THEN 'released' ELSE 'finalized' END,
            v->>'grant_id',(v->>'ordinal')::integer,(v->>'fence')::uuid,(v->>'provisional')::numeric,event_sequence
        FROM raw RETURNING op.operation_id,op.accounting_grant_id,op.final_event_sequence
    ), reservations AS (
        INSERT INTO deltallm_accounting_reservations AS reservation_row(
            operation_id,window_id,grant_id,allowance_exact,committed_exact,provisional_exact,released_exact
        )
        SELECT op.operation_id,gw.window_id,op.accounting_grant_id,(raw.v->>'allowance')::numeric,
            (raw.v->>'committed')::numeric,(raw.v->>'provisional')::numeric,(raw.v->>'released')::numeric
        FROM operations op JOIN raw ON raw.v->>'operation_id'=op.operation_id
        CROSS JOIN LATERAL (SELECT gw.window_id FROM deltallm_accounting_grant_windows gw
            WHERE gw.grant_id=op.accounting_grant_id OFFSET 0) gw
        RETURNING reservation_row.operation_id
    ), reservation_effects AS MATERIALIZED (SELECT count(*) FROM reservations), events AS (
        INSERT INTO deltallm_accounting_events AS event(
            sequence,event_id,protocol_name,generation,accounting_partition,operation_id,component_id,
            event_type,outcome,payload_json,audit_envelope_json,occurred_at
        )
        SELECT op.final_event_sequence,raw.terminal_json->>'event_id','primary',p_generation,(raw.v->>'partition')::integer,
            op.operation_id,raw.terminal_json->>'component_id','finalized',raw.terminal_json->>'outcome',
            jsonb_build_object('spend',raw.terminal_json->'spend_payload','exact_charge',raw.terminal_json->'exact_charge',
                'uncertainty_reason',raw.terminal_json->'uncertainty_reason','unresolved_attempts',(raw.terminal_json->>'unresolved_attempts')::integer,
                'grant_id',op.accounting_grant_id,'terminal_journal_sequence',(raw.v->>'sequence')::bigint),
            raw.terminal_json->'audit_envelope',(raw.terminal_json->>'occurred_at')::timestamptz
        FROM operations op JOIN raw ON raw.v->>'operation_id'=op.operation_id CROSS JOIN reservation_effects
        RETURNING event.operation_id,event.sequence
    ), completed AS (
        UPDATE deltallm_accounting_terminal_journal j SET status='completed',materialized_event_sequence=e.sequence,
            completed_at=clock_timestamp(),updated_at=clock_timestamp(),lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,last_error_code=NULL
        FROM events e WHERE j.sequence=ANY(p_sequences) AND j.operation_id=e.operation_id
        RETURNING j.sequence,j.grant_id,j.accounting_partition,j.allowance_exact,j.payload_bytes
    ), grant_deltas AS MATERIALIZED (
        SELECT grant_id,count(*)::integer AS entries,sum(allowance_exact)::numeric AS allowance FROM completed GROUP BY grant_id
    ), grants AS (
        UPDATE deltallm_accounting_grants g SET consumed_operations=g.consumed_operations+d.entries,consumed_exact=g.consumed_exact+d.allowance,
            state=CASE WHEN g.consumed_operations+d.entries+g.returned_operations=g.operation_limit THEN 'draining' ELSE g.state END,
            updated_at=clock_timestamp() FROM grant_deltas d WHERE g.grant_id=d.grant_id RETURNING g.grant_id
    ), capacity_deltas AS MATERIALIZED (
        SELECT accounting_partition,count(*)::integer AS entries,sum(payload_bytes)::bigint AS bytes FROM completed GROUP BY accounting_partition
    ), capacity AS (
        UPDATE deltallm_accounting_terminal_capacity c SET pending_entries=c.pending_entries-d.entries,pending_bytes=c.pending_bytes-d.bytes
        FROM capacity_deltas d WHERE c.protocol_name='primary' AND c.generation=p_generation AND c.accounting_partition=d.accounting_partition
        RETURNING c.accounting_partition
    ), deleted AS (
        DELETE FROM deltallm_accounting_terminal_payloads p USING completed WHERE p.journal_sequence=completed.sequence
        RETURNING p.journal_sequence
    ), effects AS MATERIALIZED (
        SELECT (SELECT count(*) FROM grants) AS grants,(SELECT count(*) FROM capacity) AS partitions,(SELECT count(*) FROM deleted) AS payloads
    ) SELECT count(*)::integer INTO count_value FROM completed CROSS JOIN effects;
    IF count_value<>cardinality(p_sequences) OR clock_timestamp()>=earliest_deadline THEN
        RAISE EXCEPTION 'accounting_terminal_materialize_fence' USING ERRCODE='P0001';
    END IF;
    RETURN count_value;
END;
$$;

COMMIT;
