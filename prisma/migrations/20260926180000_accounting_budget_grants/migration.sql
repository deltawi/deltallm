-- Short-lived, fenced budget grants amortize authoritative budget-window
-- locking without moving monetary authority out of PostgreSQL.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

CREATE TABLE deltallm_accounting_grants (
    grant_id TEXT PRIMARY KEY,
    protocol_name TEXT NOT NULL,
    generation BIGINT NOT NULL,
    grantee_id TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    accounting_partition INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'active',
    allocated_exact NUMERIC(38,18) NOT NULL,
    consumed_exact NUMERIC(38,18) NOT NULL DEFAULT 0,
    settled_exact NUMERIC(38,18) NOT NULL DEFAULT 0,
    operation_limit INTEGER NOT NULL,
    consumed_operations INTEGER NOT NULL DEFAULT 0,
    settled_operations INTEGER NOT NULL DEFAULT 0,
    expires_at TIMESTAMPTZ NOT NULL,
    reconciled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (protocol_name, generation)
        REFERENCES deltallm_accounting_protocols(protocol_name, generation)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    FOREIGN KEY (protocol_name, generation, accounting_partition)
        REFERENCES deltallm_accounting_partitions(protocol_name, generation, partition_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CHECK (length(grantee_id) BETWEEN 1 AND 256),
    CHECK (length(subject_key) BETWEEN 2 AND 2048),
    CHECK (state IN ('active','draining','closed')),
    CHECK (allocated_exact >= 0),
    CHECK (settled_exact >= 0 AND settled_exact <= consumed_exact),
    CHECK (consumed_exact >= 0 AND consumed_exact <= allocated_exact),
    CHECK (operation_limit BETWEEN 1 AND 1024),
    CHECK (settled_operations BETWEEN 0 AND consumed_operations),
    CHECK (consumed_operations BETWEEN 0 AND operation_limit),
    CHECK ((state = 'closed') = (reconciled_at IS NOT NULL))
);
CREATE INDEX deltallm_accounting_grant_lookup_idx
    ON deltallm_accounting_grants(
        protocol_name,generation,grantee_id,subject_key,state,expires_at
    );
CREATE INDEX deltallm_accounting_grant_reconcile_idx
    ON deltallm_accounting_grants(protocol_name,generation,state,expires_at);

CREATE TABLE deltallm_accounting_grant_windows (
    grant_id TEXT NOT NULL,
    window_id TEXT NOT NULL,
    allocated_exact NUMERIC(38,18) NOT NULL,
    PRIMARY KEY (grant_id, window_id),
    FOREIGN KEY (grant_id) REFERENCES deltallm_accounting_grants(grant_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    FOREIGN KEY (window_id) REFERENCES deltallm_accounting_budget_windows(window_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CHECK (allocated_exact >= 0)
);
CREATE INDEX deltallm_accounting_grant_window_idx
    ON deltallm_accounting_grant_windows(window_id,grant_id);

ALTER TABLE deltallm_billing_operations
    ADD COLUMN accounting_grant_id TEXT,
    ADD CONSTRAINT deltallm_billing_operation_accounting_grant_fk
        FOREIGN KEY (accounting_grant_id) REFERENCES deltallm_accounting_grants(grant_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT;
ALTER TABLE deltallm_accounting_reservations
    ADD COLUMN grant_id TEXT,
    ADD CONSTRAINT deltallm_accounting_reservation_grant_fk
        FOREIGN KEY (grant_id) REFERENCES deltallm_accounting_grants(grant_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT;
CREATE INDEX deltallm_accounting_reservation_grant_idx
    ON deltallm_accounting_reservations(grant_id,operation_id)
    WHERE grant_id IS NOT NULL;

CREATE FUNCTION deltallm_accounting_grant_subject(p_item JSONB)
RETURNS TEXT
LANGUAGE SQL IMMUTABLE PARALLEL SAFE AS $$
    SELECT encode(digest(convert_to(jsonb_build_array(
        p_item#>>'{attribution,api_key}',p_item#>>'{attribution,user_id}',
        p_item#>>'{attribution,team_id}',p_item#>>'{attribution,organization_id}',
        p_item#>>'{attribution,model}',p_item->'windows',p_item->>'allowance'
    )::text,'UTF8'),'sha256'),'hex')
$$;

-- Refill grants in a short transaction. The following reservation call can
-- then persist operations and events without holding a shared budget-window
-- lock for the duration of the full microbatch.
CREATE FUNCTION deltallm_accounting_ensure_grants_batch(
    p_generation BIGINT,
    p_grantee_id TEXT,
    p_target_operations INTEGER,
    p_ttl_seconds INTEGER,
    p_items JSONB
) RETURNS TABLE(operation_id TEXT, decision TEXT)
LANGUAGE plpgsql AS $$
DECLARE
    protocol_row deltallm_accounting_protocols%ROWTYPE;
    item JSONB;
    allowance NUMERIC(38,18);
    subject_key_value TEXT;
    subject_state JSONB;
    cached_subjects JSONB := '{}'::jsonb;
    requested_windows INTEGER;
    matched_windows INTEGER;
    window_count INTEGER;
    available_exact NUMERIC(38,18);
    grant_expires_at TIMESTAMPTZ;
    candidate_partition INTEGER;
    grant_operation_limit INTEGER;
    required_operations INTEGER;
    available_operations INTEGER;
    needed_operations INTEGER;
    ready_operations INTEGER;
    ready_remaining INTEGER;
    denial_decision TEXT;
    budget_operations INTEGER;
    grant_amount NUMERIC(38,18);
    grant_id_value TEXT;
BEGIN
    IF length(p_grantee_id) NOT BETWEEN 1 AND 256
       OR p_target_operations NOT BETWEEN 1 AND 1024
       OR p_ttl_seconds NOT BETWEEN 1 AND 300
       OR jsonb_typeof(p_items)<>'array'
       OR jsonb_array_length(p_items) NOT BETWEEN 1 AND 256 THEN
        RAISE EXCEPTION 'accounting_grant_batch_shape' USING ERRCODE = 'P0001';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_items) value
        GROUP BY value->>'operation_id' HAVING count(*)>1
    ) THEN
        RAISE EXCEPTION 'accounting_reservation_duplicate_operation' USING ERRCODE = 'P0001';
    END IF;
    SELECT * INTO STRICT protocol_row FROM deltallm_accounting_protocols
        WHERE protocol_name='primary' AND generation=p_generation FOR SHARE;
    IF protocol_row.state<>'active' OR protocol_row.writer_version<>2 THEN
        RAISE EXCEPTION 'accounting_protocol_not_active' USING ERRCODE = 'P0001';
    END IF;
    PERFORM 1 FROM deltallm_accounting_grants g
    WHERE g.protocol_name='primary' AND g.generation=p_generation
      AND g.grantee_id=p_grantee_id AND g.state='active'
      AND g.expires_at>CURRENT_TIMESTAMP
      AND g.subject_key IN (
          SELECT deltallm_accounting_grant_subject(value)
          FROM jsonb_array_elements(p_items) value
      ) ORDER BY g.grant_id FOR UPDATE;

    FOR item IN SELECT value FROM jsonb_array_elements(p_items) value
                ORDER BY deltallm_accounting_grant_subject(value),value->>'operation_id'
    LOOP
        IF (item->>'protocol_generation')::bigint<>p_generation
           OR item->>'request_fingerprint' !~ '^[0-9a-f]{64}$'
           OR jsonb_typeof(item->'windows')<>'array'
           OR jsonb_array_length(item->'windows')>8
           OR jsonb_typeof(item->'attribution')<>'object'
           OR jsonb_typeof(item->'pricing_snapshot')<>'object'
           OR jsonb_typeof(item->'audit_envelope')<>'object'
           OR octet_length((item->'pricing_snapshot')::text)>32768
           OR octet_length((item->'audit_envelope')::text)>65536
           OR (item->>'expires_at')::timestamptz<=CURRENT_TIMESTAMP
           OR (item->>'expires_at')::timestamptz>CURRENT_TIMESTAMP+interval '15 minutes' THEN
            RAISE EXCEPTION 'accounting_reservation_item_shape' USING ERRCODE = 'P0001';
        END IF;
        allowance := (item->>'allowance')::numeric;
        IF allowance<0 THEN
            RAISE EXCEPTION 'accounting_reservation_allowance' USING ERRCODE = 'P0001';
        END IF;
        operation_id := item->>'operation_id';
        IF EXISTS (
            SELECT 1 FROM deltallm_billing_operations existing
            WHERE existing.operation_id=item->>'operation_id'
        ) THEN
            decision := 'replay';
            RETURN NEXT;
            CONTINUE;
        END IF;

        subject_key_value := deltallm_accounting_grant_subject(item);
        IF cached_subjects ? subject_key_value THEN
            subject_state := cached_subjects->subject_key_value;
            ready_remaining := (subject_state->>'ready_remaining')::integer;
            IF ready_remaining>0 THEN
                decision := 'ready';
                cached_subjects := jsonb_set(
                    cached_subjects,
                    ARRAY[subject_key_value,'ready_remaining'],
                    to_jsonb(ready_remaining-1)
                );
            ELSE
                decision := subject_state->>'denial';
            END IF;
            RETURN NEXT;
            CONTINUE;
        END IF;

        SELECT count(*)::integer
        INTO required_operations
        FROM jsonb_array_elements(p_items) required
        WHERE deltallm_accounting_grant_subject(required)=subject_key_value
          AND NOT EXISTS (
              SELECT 1 FROM deltallm_billing_operations existing
              WHERE existing.operation_id=required->>'operation_id'
          );
        PERFORM 1 FROM deltallm_accounting_grants g
        WHERE g.protocol_name='primary' AND g.generation=p_generation
          AND g.grantee_id=p_grantee_id AND g.subject_key=subject_key_value
          AND g.state='active' AND g.expires_at>CURRENT_TIMESTAMP
        ORDER BY g.grant_id FOR UPDATE;
        SELECT COALESCE(sum(
            CASE WHEN allowance=0 THEN g.operation_limit-g.consumed_operations
                 ELSE least(
                     g.operation_limit-g.consumed_operations,
                     floor((g.allocated_exact-g.consumed_exact)/allowance)::integer
                 )
            END
        ),0)::integer
        INTO available_operations
        FROM deltallm_accounting_grants g
        WHERE g.protocol_name='primary' AND g.generation=p_generation
          AND g.grantee_id=p_grantee_id AND g.subject_key=subject_key_value
          AND g.state='active' AND g.expires_at>CURRENT_TIMESTAMP;
        ready_operations := least(required_operations,available_operations);
        needed_operations := required_operations-ready_operations;
        IF needed_operations<=0 THEN
            cached_subjects := cached_subjects||jsonb_build_object(
                subject_key_value,
                jsonb_build_object(
                    'ready_remaining',required_operations-1,
                    'denial','capacity_exhausted'
                )
            );
            decision := 'ready';
            RETURN NEXT;
            CONTINUE;
        END IF;

        requested_windows := jsonb_array_length(item->'windows');
        IF requested_windows=0 AND EXISTS (
            SELECT 1 FROM deltallm_accounting_budget_windows expired
            WHERE expired.protocol_name='primary' AND expired.generation=p_generation
              AND expired.renewal_spec IS NOT NULL
              AND expired.window_ends_at<=CURRENT_TIMESTAMP
              AND CASE expired.scope_type
                    WHEN 'api_key' THEN expired.scope_id=item#>>'{attribution,api_key}'
                    WHEN 'user' THEN expired.scope_id=item#>>'{attribution,user_id}'
                    WHEN 'team' THEN expired.scope_id=item#>>'{attribution,team_id}'
                    WHEN 'organization' THEN expired.scope_id=item#>>'{attribution,organization_id}'
                    WHEN 'team_model' THEN expired.scope_id=(item#>>'{attribution,team_id}')||':'||
                                                               (item#>>'{attribution,model}')
                  END
              AND NOT EXISTS (
                  SELECT 1 FROM deltallm_accounting_budget_windows active
                  WHERE active.protocol_name=expired.protocol_name
                    AND active.generation=expired.generation
                    AND active.scope_type=expired.scope_type
                    AND active.scope_id=expired.scope_id
                    AND active.window_starts_at<=CURRENT_TIMESTAMP
                    AND active.window_ends_at>CURRENT_TIMESTAMP
              )
        ) THEN
            RAISE EXCEPTION 'accounting_budget_window_renewal_pending' USING ERRCODE = 'P0001';
        END IF;
        IF requested_windows>0 THEN
            SELECT count(*)::integer INTO matched_windows
            FROM jsonb_array_elements(item->'windows') ref
            JOIN deltallm_accounting_budget_windows w ON w.window_id=ref->>'window_id'
            WHERE w.protocol_name='primary' AND w.generation=p_generation
              AND w.scope_type=ref->>'scope_type' AND w.scope_id=ref->>'scope_id'
              AND w.policy_generation=(ref->>'policy_generation')::bigint
              AND w.window_starts_at<=CURRENT_TIMESTAMP AND w.window_ends_at>CURRENT_TIMESTAMP
              AND CASE w.scope_type
                    WHEN 'api_key' THEN w.scope_id=item#>>'{attribution,api_key}'
                    WHEN 'user' THEN w.scope_id=item#>>'{attribution,user_id}'
                    WHEN 'team' THEN w.scope_id=item#>>'{attribution,team_id}'
                    WHEN 'organization' THEN w.scope_id=item#>>'{attribution,organization_id}'
                    WHEN 'team_model' THEN w.scope_id=(item#>>'{attribution,team_id}')||':'||
                                                       (item#>>'{attribution,model}')
                  END;
            IF matched_windows<>requested_windows THEN
                RAISE EXCEPTION 'accounting_budget_window_unavailable' USING ERRCODE = 'P0001';
            END IF;
        END IF;

        PERFORM 1 FROM deltallm_accounting_budget_windows w
        WHERE w.protocol_name='primary' AND w.generation=p_generation
          AND w.window_starts_at<=CURRENT_TIMESTAMP AND w.window_ends_at>CURRENT_TIMESTAMP
          AND (
            (requested_windows>0 AND w.window_id IN (
                SELECT ref->>'window_id' FROM jsonb_array_elements(item->'windows') ref
            )) OR (requested_windows=0 AND CASE w.scope_type
                WHEN 'api_key' THEN w.scope_id=item#>>'{attribution,api_key}'
                WHEN 'user' THEN w.scope_id=item#>>'{attribution,user_id}'
                WHEN 'team' THEN w.scope_id=item#>>'{attribution,team_id}'
                WHEN 'organization' THEN w.scope_id=item#>>'{attribution,organization_id}'
                WHEN 'team_model' THEN w.scope_id=(item#>>'{attribution,team_id}')||':'||
                                                   (item#>>'{attribution,model}')
              END)
          ) ORDER BY w.window_id FOR UPDATE;
        SELECT count(*)::integer,
               min(w.limit_exact-w.committed_exact-w.reserved_exact-w.provisional_exact),
               min(w.window_ends_at)
        INTO window_count,available_exact,grant_expires_at
        FROM deltallm_accounting_budget_windows w
        WHERE w.protocol_name='primary' AND w.generation=p_generation
          AND w.window_starts_at<=CURRENT_TIMESTAMP AND w.window_ends_at>CURRENT_TIMESTAMP
          AND (
            (requested_windows>0 AND w.window_id IN (
                SELECT ref->>'window_id' FROM jsonb_array_elements(item->'windows') ref
            )) OR (requested_windows=0 AND CASE w.scope_type
                WHEN 'api_key' THEN w.scope_id=item#>>'{attribution,api_key}'
                WHEN 'user' THEN w.scope_id=item#>>'{attribution,user_id}'
                WHEN 'team' THEN w.scope_id=item#>>'{attribution,team_id}'
                WHEN 'organization' THEN w.scope_id=item#>>'{attribution,organization_id}'
                WHEN 'team_model' THEN w.scope_id=(item#>>'{attribution,team_id}')||':'||
                                                   (item#>>'{attribution,model}')
              END)
          );
        budget_operations := NULL;
        IF window_count>0 AND allowance>0 THEN
            budget_operations := least(
                floor(GREATEST(available_exact,0)/allowance),2147483647
            )::integer;
        END IF;

        SELECT p.partition_id,
               least(
                   GREATEST(p_target_operations,needed_operations),
                   p.max_outstanding-p.outstanding_count,
                   COALESCE(budget_operations,2147483647)
               )
        INTO candidate_partition,grant_operation_limit
        FROM deltallm_accounting_partitions p
        WHERE p.protocol_name='primary' AND p.generation=p_generation
          AND p.max_outstanding=protocol_row.max_outstanding_per_partition
          AND p.max_outstanding>p.outstanding_count
          AND COALESCE(budget_operations,1)>0
        ORDER BY p.outstanding_count,p.partition_id
        FOR UPDATE SKIP LOCKED LIMIT 1;
        IF NOT FOUND THEN
            denial_decision := CASE
                WHEN COALESCE(budget_operations,1)<=0 THEN 'budget_exhausted'
                ELSE 'capacity_exhausted'
            END;
        ELSE
            grant_amount := allowance*grant_operation_limit;
            IF window_count>0 THEN
                grant_expires_at := least(
                    grant_expires_at,
                    CURRENT_TIMESTAMP+make_interval(secs=>p_ttl_seconds)
                );
            ELSE
                grant_expires_at := CURRENT_TIMESTAMP+make_interval(secs=>p_ttl_seconds);
            END IF;
            grant_id_value := md5(
                'accounting-grant:v2:'||p_generation||':'||p_grantee_id||':'||
                (item->>'operation_id')||':'||txid_current()||':'||clock_timestamp()::text
            );
            INSERT INTO deltallm_accounting_grants(
                grant_id,protocol_name,generation,grantee_id,subject_key,
                accounting_partition,state,allocated_exact,operation_limit,expires_at
            ) VALUES (
                grant_id_value,'primary',p_generation,p_grantee_id,subject_key_value,
                candidate_partition,'active',grant_amount,grant_operation_limit,grant_expires_at
            );
            INSERT INTO deltallm_accounting_grant_windows(grant_id,window_id,allocated_exact)
                SELECT grant_id_value,w.window_id,grant_amount
                FROM deltallm_accounting_budget_windows w
                WHERE w.protocol_name='primary' AND w.generation=p_generation
                  AND w.window_starts_at<=CURRENT_TIMESTAMP AND w.window_ends_at>CURRENT_TIMESTAMP
                  AND (
                    (requested_windows>0 AND w.window_id IN (
                        SELECT ref->>'window_id' FROM jsonb_array_elements(item->'windows') ref
                    )) OR (requested_windows=0 AND CASE w.scope_type
                        WHEN 'api_key' THEN w.scope_id=item#>>'{attribution,api_key}'
                        WHEN 'user' THEN w.scope_id=item#>>'{attribution,user_id}'
                        WHEN 'team' THEN w.scope_id=item#>>'{attribution,team_id}'
                        WHEN 'organization' THEN w.scope_id=item#>>'{attribution,organization_id}'
                        WHEN 'team_model' THEN w.scope_id=(item#>>'{attribution,team_id}')||':'||
                                                           (item#>>'{attribution,model}')
                      END)
                  );
            UPDATE deltallm_accounting_budget_windows w
                SET reserved_exact=reserved_exact+grant_amount,updated_at=NOW()
                WHERE w.window_id IN (
                    SELECT gw.window_id FROM deltallm_accounting_grant_windows gw
                    WHERE gw.grant_id=grant_id_value
                );
            UPDATE deltallm_accounting_partitions
                SET outstanding_count=outstanding_count+grant_operation_limit,updated_at=NOW()
                WHERE protocol_name='primary' AND generation=p_generation
                  AND partition_id=candidate_partition
                  AND outstanding_count+grant_operation_limit<=max_outstanding;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'accounting_partition_drift' USING ERRCODE = 'P0001';
            END IF;
            ready_operations := least(
                required_operations,
                ready_operations+grant_operation_limit
            );
            denial_decision := CASE
                WHEN budget_operations IS NOT NULL
                     AND budget_operations<needed_operations THEN 'budget_exhausted'
                ELSE 'capacity_exhausted'
            END;
        END IF;

        cached_subjects := cached_subjects||jsonb_build_object(
            subject_key_value,
            jsonb_build_object(
                'ready_remaining',GREATEST(ready_operations-1,0),
                'denial',denial_decision
            )
        );
        decision := CASE WHEN ready_operations>0 THEN 'ready' ELSE denial_decision END;
        RETURN NEXT;
    END LOOP;
END;
$$;

CREATE FUNCTION deltallm_accounting_reserve_grant_batch(
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
        WHERE b.operation_id IN (
            SELECT value->>'operation_id' FROM jsonb_array_elements(p_items) value
        ) ORDER BY b.operation_id FOR UPDATE;
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
        WHERE NOT EXISTS (
            SELECT 1 FROM deltallm_billing_operations existing
            WHERE existing.operation_id=raw_items.item->>'operation_id'
        )
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
    FROM deltallm_billing_operations existing
    JOIN raw_items ON existing.operation_id=raw_items.item->>'operation_id'
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

CREATE FUNCTION deltallm_accounting_finalize_grant_batch(
    p_generation BIGINT,
    p_items JSONB
) RETURNS TABLE(operation_id TEXT,event_sequence BIGINT,outcome TEXT,replayed BOOLEAN)
LANGUAGE plpgsql AS $$
DECLARE
    protocol_row deltallm_accounting_protocols%ROWTYPE;
    item JSONB;
    op deltallm_billing_operations%ROWTYPE;
    existing_event deltallm_accounting_events%ROWTYPE;
    exact_charge NUMERIC(38,18);
    sequence_value BIGINT;
BEGIN
    IF jsonb_typeof(p_items)<>'array' OR jsonb_array_length(p_items) NOT BETWEEN 1 AND 256 THEN
        RAISE EXCEPTION 'accounting_finalization_batch_shape' USING ERRCODE = 'P0001';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_items) value
        GROUP BY value->>'operation_id' HAVING count(*)>1
    ) THEN
        RAISE EXCEPTION 'accounting_finalization_duplicate_operation' USING ERRCODE = 'P0001';
    END IF;
    SELECT * INTO STRICT protocol_row FROM deltallm_accounting_protocols
        WHERE protocol_name='primary' AND generation=p_generation FOR SHARE;
    IF protocol_row.state NOT IN ('active','draining') OR protocol_row.writer_version<>2 THEN
        RAISE EXCEPTION 'accounting_protocol_not_finalizable' USING ERRCODE = 'P0001';
    END IF;

    PERFORM 1 FROM deltallm_billing_operations b
        WHERE b.operation_id IN (
            SELECT value->>'operation_id' FROM jsonb_array_elements(p_items) value
        ) ORDER BY b.operation_id FOR UPDATE;
    FOR item IN SELECT value FROM jsonb_array_elements(p_items) value
                ORDER BY value->>'operation_id'
    LOOP
        IF (item->>'protocol_generation')::bigint<>p_generation
           OR item->>'request_fingerprint' !~ '^[0-9a-f]{64}$'
           OR item->>'outcome' NOT IN ('completed','not_dispatched','uncertain')
           OR COALESCE((item->>'unresolved_attempts')::integer,0) NOT BETWEEN 0 AND 127
           OR jsonb_typeof(item->'audit_envelope')<>'object'
           OR octet_length((item->'audit_envelope')::text)>65536 THEN
            RAISE EXCEPTION 'accounting_finalization_item_shape' USING ERRCODE = 'P0001';
        END IF;
        SELECT * INTO STRICT op FROM deltallm_billing_operations
            WHERE deltallm_billing_operations.operation_id=item->>'operation_id';
        IF op.accounting_protocol<>'primary' OR op.accounting_generation<>p_generation
           OR op.accounting_grant_id IS NULL OR op.owner_token<>item->>'owner_token'
           OR op.request_fingerprint<>item->>'request_fingerprint' THEN
            RAISE EXCEPTION 'accounting_finalization_identity_conflict' USING ERRCODE = 'P0001';
        END IF;
        IF op.accounting_state<>'reserved' THEN
            SELECT e.* INTO STRICT existing_event FROM deltallm_accounting_events e
                WHERE e.operation_id=op.operation_id AND e.component_id=item->>'component_id'
                  AND e.event_type='finalized' AND e.event_id=item->>'event_id'
                  AND e.outcome=item->>'outcome';
            IF existing_event.payload_json->'spend' IS DISTINCT FROM item->'spend_payload'
               OR existing_event.payload_json->>'exact_charge' IS DISTINCT FROM item->>'exact_charge'
               OR existing_event.payload_json->>'uncertainty_reason'
                    IS DISTINCT FROM item->>'uncertainty_reason'
               OR COALESCE((existing_event.payload_json->>'unresolved_attempts')::integer,0)
                    <>COALESCE((item->>'unresolved_attempts')::integer,0)
               OR existing_event.audit_envelope_json<>item->'audit_envelope' THEN
                RAISE EXCEPTION 'accounting_finalization_replay_conflict' USING ERRCODE = 'P0001';
            END IF;
            operation_id:=op.operation_id;
            event_sequence:=existing_event.sequence;
            outcome:=item->>'outcome';
            replayed:=TRUE;
            RETURN NEXT;
            CONTINUE;
        END IF;

        exact_charge:=CASE WHEN item->>'exact_charge' IS NULL THEN NULL
                           ELSE (item->>'exact_charge')::numeric END;
        IF item->>'outcome'='completed' AND (
            exact_charge IS NULL OR exact_charge<0 OR exact_charge>op.answer_allowance
            OR item->'spend_payload' IS NULL OR item->'spend_payload'='null'::jsonb
            OR jsonb_typeof(item->'spend_payload')<>'object'
            OR octet_length((item->'spend_payload')::text)>262144
        ) THEN
            RAISE EXCEPTION 'accounting_completed_receipt_invalid' USING ERRCODE = 'P0001';
        ELSIF item->>'outcome'='not_dispatched' AND (
            COALESCE(exact_charge,0)<>0
            OR COALESCE(item->'spend_payload','null'::jsonb)<>'null'::jsonb
            OR item->>'uncertainty_reason' IS NOT NULL
            OR COALESCE((item->>'unresolved_attempts')::integer,0)<>0
        ) THEN
            RAISE EXCEPTION 'accounting_not_dispatched_receipt_invalid' USING ERRCODE = 'P0001';
        ELSIF item->>'outcome'='uncertain' AND (
            exact_charge IS NOT NULL
            OR COALESCE(item->'spend_payload','null'::jsonb)<>'null'::jsonb
            OR item->>'uncertainty_reason' IS NULL
            OR COALESCE((item->>'unresolved_attempts')::integer,0)<>0
        ) THEN
            RAISE EXCEPTION 'accounting_uncertain_receipt_invalid' USING ERRCODE = 'P0001';
        END IF;

        UPDATE deltallm_accounting_reservations ar SET
            committed_exact=CASE WHEN item->>'outcome'='completed' THEN exact_charge ELSE 0 END,
            provisional_exact=CASE
                WHEN item->>'outcome'='uncertain' THEN allowance_exact
                WHEN item->>'outcome'='completed'
                     AND COALESCE((item->>'unresolved_attempts')::integer,0)>0
                THEN allowance_exact-exact_charge ELSE 0 END,
            released_exact=CASE
                WHEN item->>'outcome'='not_dispatched' THEN allowance_exact
                WHEN item->>'outcome'='completed'
                     AND COALESCE((item->>'unresolved_attempts')::integer,0)=0
                THEN allowance_exact-exact_charge ELSE 0 END,
            updated_at=NOW()
        WHERE ar.operation_id=op.operation_id;
        INSERT INTO deltallm_accounting_events(
            event_id,protocol_name,generation,accounting_partition,operation_id,
            component_id,event_type,outcome,payload_json,audit_envelope_json,occurred_at
        ) VALUES (
            item->>'event_id','primary',p_generation,op.accounting_partition,op.operation_id,
            item->>'component_id','finalized',item->>'outcome',
            jsonb_build_object(
                'spend',item->'spend_payload','exact_charge',item->'exact_charge',
                'uncertainty_reason',item->'uncertainty_reason',
                'unresolved_attempts',COALESCE((item->>'unresolved_attempts')::integer,0),
                'grant_id',op.accounting_grant_id
            ),item->'audit_envelope',(item->>'occurred_at')::timestamptz
        ) RETURNING sequence INTO sequence_value;
        UPDATE deltallm_billing_operations SET
            accounting_state=CASE item->>'outcome'
                WHEN 'completed' THEN CASE
                    WHEN COALESCE((item->>'unresolved_attempts')::integer,0)>0
                    THEN 'provisional' ELSE 'finalized' END
                WHEN 'not_dispatched' THEN 'released' ELSE 'provisional' END,
            answer_state=CASE item->>'outcome'
                WHEN 'not_dispatched' THEN 'unattempted'
                WHEN 'uncertain' THEN 'pending' ELSE 'settled' END,
            answer_receipt=CASE WHEN item->>'outcome'='completed'
                THEN jsonb_build_object('event_id',item->>'event_id','cost_exact',exact_charge::text)
                ELSE NULL END,
            provisional_debit_exact=CASE
                WHEN item->>'outcome'='uncertain' THEN answer_allowance
                WHEN item->>'outcome'='completed'
                     AND COALESCE((item->>'unresolved_attempts')::integer,0)>0
                THEN answer_allowance-exact_charge ELSE 0 END,
            final_event_sequence=sequence_value,closed_at=NOW(),updated_at=NOW()
        WHERE deltallm_billing_operations.operation_id=op.operation_id;
        operation_id:=op.operation_id;
        event_sequence:=sequence_value;
        outcome:=item->>'outcome';
        replayed:=FALSE;
        RETURN NEXT;
    END LOOP;
END;
$$;

CREATE FUNCTION deltallm_accounting_reconcile_expired_grants(
    p_generation BIGINT,p_limit INTEGER
) RETURNS INTEGER
LANGUAGE plpgsql AS $$
DECLARE
    op deltallm_billing_operations%ROWTYPE;
    sequence_value BIGINT;
    reconciled_count INTEGER:=0;
    expiry_event_id TEXT;
    expiry_audit JSONB;
BEGIN
    IF p_limit NOT BETWEEN 1 AND 256 THEN
        RAISE EXCEPTION 'accounting_recovery_limit' USING ERRCODE = 'P0001';
    END IF;
    FOR op IN
        SELECT b.* FROM deltallm_billing_operations b
        WHERE b.accounting_protocol='primary' AND b.accounting_generation=p_generation
          AND b.accounting_state='reserved' AND b.accounting_grant_id IS NOT NULL
          AND b.expires_at<=CURRENT_TIMESTAMP
        ORDER BY b.operation_id FOR UPDATE SKIP LOCKED LIMIT p_limit
    LOOP
        UPDATE deltallm_accounting_reservations SET
            provisional_exact=allowance_exact,updated_at=NOW()
            WHERE operation_id=op.operation_id;
        UPDATE deltallm_accounting_grants SET state='draining',updated_at=NOW()
            WHERE grant_id=op.accounting_grant_id AND state='active';
        expiry_event_id:=op.operation_id||':expired:v2';
        expiry_audit:=jsonb_build_object(
            'event_id',expiry_event_id||':audit','record_type','audit_event',
            'organization_id',op.organization_id,
            'payload',jsonb_build_object(
                'event',jsonb_build_object(
                    'action','ACCOUNTING_PROVIDER_OUTCOME_UNKNOWN',
                    'organization_id',op.organization_id,'actor_type','system',
                    'actor_id','accounting-recovery','api_key',op.api_key,
                    'resource_type','model','resource_id',op.model,
                    'request_id',op.operation_id,'correlation_id',op.operation_id,
                    'status','error','error_type','AccountingReservationExpired',
                    'error_code','provider_outcome_unknown',
                    'metadata',jsonb_build_object('allowance_exact',op.answer_allowance::text),
                    'event_id',expiry_event_id
                ),'payloads','[]'::jsonb,'critical',TRUE
            )
        );
        expiry_audit:=expiry_audit||jsonb_build_object(
            'redacted_payload',expiry_audit->'payload'
        );
        INSERT INTO deltallm_accounting_events(
            event_id,protocol_name,generation,accounting_partition,operation_id,
            component_id,event_type,outcome,payload_json,audit_envelope_json,occurred_at
        ) VALUES (
            expiry_event_id,'primary',p_generation,op.accounting_partition,op.operation_id,
            'provider','finalized','uncertain',jsonb_build_object(
                'spend',NULL,'exact_charge',NULL,'uncertainty_reason','reservation_expired',
                'unresolved_attempts',0,'grant_id',op.accounting_grant_id
            ),expiry_audit,NOW()
        ) RETURNING sequence INTO sequence_value;
        UPDATE deltallm_billing_operations SET
            accounting_state='provisional',answer_state='pending',
            provisional_debit_exact=answer_allowance,final_event_sequence=sequence_value,
            closed_at=NOW(),updated_at=NOW()
            WHERE operation_id=op.operation_id;
        reconciled_count:=reconciled_count+1;
    END LOOP;
    RETURN reconciled_count;
END;
$$;

CREATE FUNCTION deltallm_accounting_reconcile_grants(
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
               COALESCE(sum(r.provisional_exact),0)::numeric AS provisional_exact
        FROM deltallm_accounting_grant_windows gw
        LEFT JOIN deltallm_accounting_reservations r
          ON r.grant_id=gw.grant_id AND r.window_id=gw.window_id
        WHERE gw.grant_id=ANY(candidate_ids)
        GROUP BY gw.grant_id,gw.window_id,gw.allocated_exact
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
            state='closed',settled_exact=consumed_exact,
            settled_operations=consumed_operations,
            reconciled_at=NOW(),updated_at=NOW()
        WHERE g.grant_id=ANY(candidate_ids)
        RETURNING g.grant_id
    ) SELECT count(*)::integer INTO reconciled_count FROM closed;
    RETURN reconciled_count;
END;
$$;

-- Operator reconciliation acts only after a grant has moved its aggregate
-- debit into the authoritative windows. This keeps the existing reviewed
-- evidence function correct during the additive rollout.
ALTER FUNCTION deltallm_accounting_resolve_provisional(BIGINT,TEXT,NUMERIC,JSONB,TEXT)
    RENAME TO deltallm_accounting_resolve_provisional_without_grant_guard;
CREATE FUNCTION deltallm_accounting_resolve_provisional(
    p_generation BIGINT,
    p_operation_id TEXT,
    p_additional_charge NUMERIC,
    p_spend_payload JSONB,
    p_evidence_reason TEXT
) RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE
    grant_state TEXT;
BEGIN
    SELECT g.state INTO grant_state
    FROM deltallm_billing_operations b
    LEFT JOIN deltallm_accounting_grants g ON g.grant_id=b.accounting_grant_id
    WHERE b.operation_id=p_operation_id;
    IF grant_state IS NOT NULL AND grant_state<>'closed' THEN
        RAISE EXCEPTION 'accounting_grant_not_reconciled' USING ERRCODE = 'P0001';
    END IF;
    RETURN deltallm_accounting_resolve_provisional_without_grant_guard(
        p_generation,p_operation_id,p_additional_charge,p_spend_payload,p_evidence_reason
    );
END;
$$;

COMMIT;
