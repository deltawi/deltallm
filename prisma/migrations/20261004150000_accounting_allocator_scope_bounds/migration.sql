-- Keep grant window work bounded by five request scopes and eight live windows.
-- Existing assigned and pre-issued grants retain one economic authority.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

CREATE INDEX deltallm_accounting_window_subject_renewal_idx
    ON deltallm_accounting_budget_windows(
        protocol_name,generation,scope_type,scope_id,window_ends_at
    ) WHERE renewal_spec IS NOT NULL;

CREATE FUNCTION deltallm_accounting_request_scopes(p_item JSONB)
RETURNS TABLE(scope_type TEXT,scope_id TEXT)
LANGUAGE SQL IMMUTABLE PARALLEL SAFE AS $$
    SELECT scope.scope_type,scope.scope_id FROM (VALUES
        ('api_key',p_item#>>'{attribution,api_key}'),
        ('user',p_item#>>'{attribution,user_id}'),
        ('team',p_item#>>'{attribution,team_id}'),
        ('organization',p_item#>>'{attribution,organization_id}'),
        ('team_model',(p_item#>>'{attribution,team_id}')||':'||
                      (p_item#>>'{attribution,model}'))
    ) AS scope(scope_type,scope_id)
    WHERE scope.scope_id IS NOT NULL
$$;

CREATE OR REPLACE FUNCTION deltallm_accounting_permit_window_ids(
    p_generation BIGINT,p_item JSONB
) RETURNS TABLE(window_id TEXT)
LANGUAGE SQL STABLE AS $$
    SELECT w.window_id
    FROM jsonb_array_elements(p_item->'windows') ref
    JOIN deltallm_accounting_budget_windows w ON w.window_id=ref->>'window_id'
    WHERE w.protocol_name='primary' AND w.generation=p_generation
      AND w.window_starts_at<=CURRENT_TIMESTAMP AND w.window_ends_at>CURRENT_TIMESTAMP
    UNION ALL
    SELECT found.window_id FROM deltallm_accounting_request_scopes(p_item) scope
    CROSS JOIN LATERAL (
        SELECT w.window_id FROM deltallm_accounting_budget_windows w
        WHERE w.protocol_name='primary' AND w.generation=p_generation
          AND w.scope_type=scope.scope_type AND w.scope_id=scope.scope_id
          AND w.window_starts_at<=CURRENT_TIMESTAMP AND w.window_ends_at>CURRENT_TIMESTAMP
        -- No ID tie sort: the parent locks IDs in order and rejects excess rows.
        ORDER BY w.window_ends_at,w.window_starts_at LIMIT 9
    ) found
    WHERE jsonb_array_length(p_item->'windows')=0
$$;

CREATE FUNCTION deltallm_accounting_pending_renewal_scopes(
    p_generation BIGINT,p_item JSONB
) RETURNS TABLE(scope_type TEXT,scope_id TEXT)
LANGUAGE SQL STABLE AS $$
    SELECT scope.scope_type,scope.scope_id
    FROM deltallm_accounting_request_scopes(p_item) scope
    CROSS JOIN LATERAL (
        -- Keep this scalar probe local to one scope. A global anti-join can read
        -- every other tenant's active windows before it returns no match.
        SELECT EXISTS (
            SELECT 1 FROM deltallm_accounting_budget_windows active
            WHERE active.protocol_name='primary' AND active.generation=p_generation
              AND active.scope_type=scope.scope_type AND active.scope_id=scope.scope_id
              AND active.window_starts_at<=CURRENT_TIMESTAMP
              AND active.window_ends_at>CURRENT_TIMESTAMP
        ) AS has_active OFFSET 0
    ) current_scope
    CROSS JOIN LATERAL (
        SELECT expired.window_id FROM deltallm_accounting_budget_windows expired
        WHERE expired.protocol_name='primary' AND expired.generation=p_generation
          AND expired.scope_type=scope.scope_type AND expired.scope_id=scope.scope_id
          AND expired.renewal_spec IS NOT NULL
          AND expired.window_ends_at<=CURRENT_TIMESTAMP
        ORDER BY expired.window_ends_at DESC LIMIT 1
    ) expired
    WHERE NOT current_scope.has_active
$$;

CREATE OR REPLACE FUNCTION deltallm_accounting_ensure_grants_batch(
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
            SELECT 1 FROM deltallm_accounting_pending_renewal_scopes(p_generation,item)
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
          AND w.window_id IN (
              SELECT resolved.window_id
              FROM deltallm_accounting_permit_window_ids(p_generation,item) resolved
          ) ORDER BY w.window_id FOR UPDATE;
        SELECT count(*)::integer,
               min(w.limit_exact-w.committed_exact-w.reserved_exact-w.provisional_exact),
               min(w.window_ends_at)
        INTO window_count,available_exact,grant_expires_at
        FROM deltallm_accounting_budget_windows w
        WHERE w.protocol_name='primary' AND w.generation=p_generation
          AND w.window_starts_at<=CURRENT_TIMESTAMP AND w.window_ends_at>CURRENT_TIMESTAMP
          AND w.window_id IN (
              SELECT resolved.window_id
              FROM deltallm_accounting_permit_window_ids(p_generation,item) resolved
          );
        IF window_count>8 THEN
            RAISE EXCEPTION 'accounting_budget_window_capacity' USING ERRCODE='P0001';
        END IF;
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
                  AND w.window_id IN (
              SELECT resolved.window_id
              FROM deltallm_accounting_permit_window_ids(p_generation,item) resolved
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

COMMIT;
