-- Additive, inactive accounting-v2 protocol. No existing writer is changed by
-- this migration. Activation is a separate, generation-fenced operator action.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

ALTER TABLE deltallm_billing_operations
    ADD COLUMN accounting_protocol TEXT,
    ADD COLUMN accounting_generation BIGINT,
    ADD COLUMN accounting_partition INTEGER,
    ADD COLUMN request_fingerprint TEXT,
    ADD COLUMN accounting_state TEXT,
    ADD COLUMN provisional_debit_exact NUMERIC(38,18),
    ADD COLUMN final_event_sequence BIGINT,
    ADD CONSTRAINT deltallm_billing_operation_accounting_shape CHECK (
        (accounting_protocol IS NULL AND accounting_generation IS NULL
            AND accounting_partition IS NULL AND request_fingerprint IS NULL
            AND accounting_state IS NULL AND provisional_debit_exact IS NULL
            AND final_event_sequence IS NULL)
        OR (accounting_protocol = 'primary' AND accounting_generation > 0
            AND accounting_partition >= 0
            AND request_fingerprint ~ '^[0-9a-f]{64}$'
            AND accounting_state IN ('reserved','finalized','released','provisional')
            AND provisional_debit_exact >= 0)
    ) NOT VALID;

CREATE INDEX deltallm_billing_operations_accounting_recovery_idx
    ON deltallm_billing_operations(accounting_generation, accounting_state, expires_at, operation_id)
    WHERE accounting_generation IS NOT NULL AND accounting_state IN ('reserved','provisional');

CREATE TABLE deltallm_accounting_protocols (
    protocol_name TEXT NOT NULL,
    generation BIGINT NOT NULL,
    writer_version INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'prepared',
    partition_count INTEGER NOT NULL,
    max_outstanding_per_partition INTEGER NOT NULL,
    activated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (protocol_name, generation),
    CHECK (protocol_name = 'primary'),
    CHECK (generation > 0),
    CHECK (writer_version = 2),
    CHECK (state IN ('prepared','active','draining','fenced')),
    CHECK ((state = 'prepared') = (activated_at IS NULL)),
    CHECK (partition_count BETWEEN 1 AND 64),
    CHECK (max_outstanding_per_partition BETWEEN 1 AND 1000000)
);
CREATE UNIQUE INDEX deltallm_accounting_one_active_generation
    ON deltallm_accounting_protocols(protocol_name) WHERE state = 'active';

CREATE TABLE deltallm_accounting_partitions (
    protocol_name TEXT NOT NULL,
    generation BIGINT NOT NULL,
    partition_id INTEGER NOT NULL,
    max_outstanding INTEGER NOT NULL,
    outstanding_count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (protocol_name, generation, partition_id),
    FOREIGN KEY (protocol_name, generation)
        REFERENCES deltallm_accounting_protocols(protocol_name, generation)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CHECK (partition_id BETWEEN 0 AND 63),
    CHECK (max_outstanding BETWEEN 1 AND 1000000),
    CHECK (outstanding_count BETWEEN 0 AND max_outstanding)
);

CREATE TABLE deltallm_accounting_budget_windows (
    window_id TEXT PRIMARY KEY,
    protocol_name TEXT NOT NULL,
    generation BIGINT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    period_key TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    policy_generation BIGINT NOT NULL,
    limit_exact NUMERIC(38,18) NOT NULL,
    committed_exact NUMERIC(38,18) NOT NULL DEFAULT 0,
    reserved_exact NUMERIC(38,18) NOT NULL DEFAULT 0,
    provisional_exact NUMERIC(38,18) NOT NULL DEFAULT 0,
    window_starts_at TIMESTAMPTZ NOT NULL,
    window_ends_at TIMESTAMPTZ NOT NULL,
    renewal_spec TEXT,
    renewal_anchor_day INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (protocol_name, generation)
        REFERENCES deltallm_accounting_protocols(protocol_name, generation)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT deltallm_accounting_budget_window_scope_key
        UNIQUE (protocol_name, generation, scope_type, scope_id, period_key),
    CHECK (scope_type IN ('api_key','user','team','organization','team_model')),
    CHECK (length(scope_id) BETWEEN 1 AND 256),
    CHECK (length(period_key) BETWEEN 1 AND 128),
    CHECK (currency = 'USD'),
    CHECK (policy_generation >= 0),
    CHECK (limit_exact >= 0 AND committed_exact >= 0
        AND reserved_exact >= 0 AND provisional_exact >= 0),
    CHECK (committed_exact + reserved_exact + provisional_exact <= limit_exact),
    CHECK (window_starts_at < window_ends_at),
    CHECK (renewal_spec IS NULL OR renewal_spec ~ '^([1-9][0-9]{0,3}|10000)(h|d|mo)$'),
    CHECK ((renewal_spec IS NULL AND renewal_anchor_day IS NULL)
        OR (renewal_spec !~ 'mo$' AND renewal_anchor_day IS NULL)
        OR (renewal_spec ~ 'mo$' AND renewal_anchor_day BETWEEN 1 AND 31))
);
CREATE INDEX deltallm_accounting_budget_window_expiry_idx
    ON deltallm_accounting_budget_windows(protocol_name, generation, window_ends_at);
CREATE INDEX deltallm_accounting_budget_window_renewal_idx
    ON deltallm_accounting_budget_windows(protocol_name,generation,window_ends_at,window_id)
    WHERE renewal_spec IS NOT NULL;

CREATE TABLE deltallm_accounting_reservations (
    operation_id TEXT NOT NULL,
    window_id TEXT NOT NULL,
    allowance_exact NUMERIC(38,18) NOT NULL,
    committed_exact NUMERIC(38,18) NOT NULL DEFAULT 0,
    provisional_exact NUMERIC(38,18) NOT NULL DEFAULT 0,
    released_exact NUMERIC(38,18) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (operation_id, window_id),
    FOREIGN KEY (operation_id) REFERENCES deltallm_billing_operations(operation_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    FOREIGN KEY (window_id) REFERENCES deltallm_accounting_budget_windows(window_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CHECK (allowance_exact >= 0 AND committed_exact >= 0
        AND provisional_exact >= 0 AND released_exact >= 0),
    CHECK (committed_exact + provisional_exact + released_exact <= allowance_exact)
);
CREATE INDEX deltallm_accounting_reservation_window_idx
    ON deltallm_accounting_reservations(window_id, operation_id);

CREATE TABLE deltallm_accounting_events (
    sequence BIGSERIAL PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    protocol_name TEXT NOT NULL,
    generation BIGINT NOT NULL,
    accounting_partition INTEGER NOT NULL,
    operation_id TEXT NOT NULL,
    component_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    outcome TEXT,
    payload_json JSONB NOT NULL,
    audit_envelope_json JSONB NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (operation_id) REFERENCES deltallm_billing_operations(operation_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT deltallm_accounting_event_component_key
        UNIQUE (operation_id, component_id, event_type),
    CHECK (generation > 0),
    CHECK (accounting_partition BETWEEN 0 AND 63),
    CHECK (event_type IN ('reserved','finalized','reconciled')),
    CHECK (outcome IS NULL OR outcome IN ('completed','not_dispatched','uncertain')),
    CHECK (jsonb_typeof(payload_json) = 'object' AND octet_length(payload_json::text) <= 262144),
    CHECK (jsonb_typeof(audit_envelope_json) = 'object'
        AND octet_length(audit_envelope_json::text) <= 65536)
);
CREATE INDEX deltallm_accounting_event_projection_idx
    ON deltallm_accounting_events(protocol_name, generation, accounting_partition, sequence);

CREATE TABLE deltallm_accounting_projection_checkpoints (
    projection_name TEXT NOT NULL,
    protocol_name TEXT NOT NULL,
    generation BIGINT NOT NULL,
    accounting_partition INTEGER NOT NULL,
    last_sequence BIGINT NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_token TEXT,
    lease_expires_at TIMESTAMPTZ,
    last_error_code TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (projection_name, protocol_name, generation, accounting_partition),
    CHECK (length(projection_name) BETWEEN 1 AND 64),
    CHECK (generation > 0),
    CHECK (accounting_partition BETWEEN 0 AND 63),
    CHECK (last_sequence >= 0),
    CHECK ((lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL)
        OR (lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL))
);
CREATE INDEX deltallm_accounting_projection_lease_idx
    ON deltallm_accounting_projection_checkpoints(lease_expires_at);

-- Keep the active protocol's hard-budget policy authoritative when control-plane
-- writers change legacy budget columns. The advisory fence also closes the race
-- between the preparation snapshot/activation and a concurrent policy update.
CREATE FUNCTION deltallm_accounting_sync_budget(
    p_scope_type TEXT,
    p_scope_id TEXT,
    p_limit NUMERIC,
    p_committed NUMERIC,
    p_renewal_spec TEXT,
    p_reset_at TIMESTAMP,
    p_metadata JSONB
) RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE
    protocol_row deltallm_accounting_protocols%ROWTYPE;
    active_window deltallm_accounting_budget_windows%ROWTYPE;
    window_end TIMESTAMPTZ;
    anchor_day INTEGER;
    next_policy_generation BIGINT;
    current_window_count INTEGER;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('deltallm:accounting-protocol',0));
    SELECT * INTO protocol_row FROM deltallm_accounting_protocols
    WHERE protocol_name='primary' AND state='active' FOR SHARE;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    IF p_scope_type NOT IN ('api_key','user','team','organization','team_model')
       OR length(p_scope_id) NOT BETWEEN 1 AND 256 THEN
        RAISE EXCEPTION 'accounting_budget_policy_identity' USING ERRCODE = 'P0001';
    END IF;

    SELECT count(*)::integer INTO current_window_count
    FROM deltallm_accounting_budget_windows w
    WHERE w.protocol_name='primary' AND w.generation=protocol_row.generation
      AND w.scope_type=p_scope_type AND w.scope_id=p_scope_id
      AND w.window_starts_at<=CURRENT_TIMESTAMP AND w.window_ends_at>CURRENT_TIMESTAMP;
    IF current_window_count>1 THEN
        RAISE EXCEPTION 'accounting_budget_policy_overlap' USING ERRCODE = 'P0001';
    END IF;
    SELECT * INTO active_window FROM deltallm_accounting_budget_windows w
    WHERE w.protocol_name='primary' AND w.generation=protocol_row.generation
      AND w.scope_type=p_scope_type AND w.scope_id=p_scope_id
      AND w.window_starts_at<=CURRENT_TIMESTAMP AND w.window_ends_at>CURRENT_TIMESTAMP
    ORDER BY w.window_id FOR UPDATE;

    IF p_limit IS NULL OR p_limit<0 OR p_limit>=1e20::numeric THEN
        IF FOUND THEN
            UPDATE deltallm_accounting_budget_windows SET
                window_ends_at=GREATEST(
                    CURRENT_TIMESTAMP,
                    active_window.window_starts_at+interval '1 microsecond'
                ),
                renewal_spec=NULL,renewal_anchor_day=NULL,
                policy_generation=policy_generation+1,updated_at=NOW()
            WHERE window_id=active_window.window_id;
        END IF;
        RETURN;
    END IF;
    IF p_committed IS NULL OR p_committed<0 OR p_committed>p_limit THEN
        RAISE EXCEPTION 'accounting_budget_policy_balance' USING ERRCODE = 'P0001';
    END IF;
    IF p_renewal_spec IS NULL THEN
        window_end := '9999-12-31 00:00:00+00'::timestamptz;
        anchor_day := NULL;
    ELSE
        IF p_renewal_spec !~ '^([1-9][0-9]{0,3}|10000)(h|d|mo)$'
           OR p_reset_at IS NULL
           OR p_reset_at AT TIME ZONE 'UTC'<=CURRENT_TIMESTAMP THEN
            RAISE EXCEPTION 'accounting_budget_policy_renewal' USING ERRCODE = 'P0001';
        END IF;
        window_end := p_reset_at AT TIME ZONE 'UTC';
        anchor_day := CASE WHEN p_renewal_spec~'mo$' THEN
            CASE WHEN p_metadata#>>'{_budget_reset,monthly_anchor_day}'
                          ~'^([1-9]|[12][0-9]|3[01])$'
                 THEN (p_metadata#>>'{_budget_reset,monthly_anchor_day}')::integer
                 ELSE extract(day FROM p_reset_at)::integer END
            ELSE NULL END;
    END IF;

    IF active_window.window_id IS NOT NULL THEN
        IF active_window.committed_exact+active_window.reserved_exact
             +active_window.provisional_exact>p_limit THEN
            RAISE EXCEPTION 'accounting_budget_policy_below_debits' USING ERRCODE = 'P0001';
        END IF;
        UPDATE deltallm_accounting_budget_windows SET
            limit_exact=p_limit,window_ends_at=window_end,
            renewal_spec=p_renewal_spec,renewal_anchor_day=anchor_day,
            policy_generation=policy_generation+1,updated_at=NOW()
        WHERE window_id=active_window.window_id;
        RETURN;
    END IF;

    SELECT COALESCE(max(policy_generation),-1)+1 INTO next_policy_generation
    FROM deltallm_accounting_budget_windows
    WHERE protocol_name='primary' AND generation=protocol_row.generation
      AND scope_type=p_scope_type AND scope_id=p_scope_id;
    INSERT INTO deltallm_accounting_budget_windows(
        window_id,protocol_name,generation,scope_type,scope_id,period_key,
        policy_generation,limit_exact,committed_exact,window_starts_at,window_ends_at,
        renewal_spec,renewal_anchor_day
    ) VALUES (
        md5('accounting-v2:policy:'||protocol_row.generation||':'||p_scope_type||':'||
            p_scope_id||':'||next_policy_generation||':'||txid_current()),
        'primary',protocol_row.generation,p_scope_type,p_scope_id,
        'policy:v1:'||next_policy_generation,next_policy_generation,p_limit,p_committed,
        CURRENT_TIMESTAMP,window_end,p_renewal_spec,anchor_day
    );
END;
$$;

CREATE FUNCTION deltallm_accounting_budget_policy_trigger() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE
    item JSONB := to_jsonb(NEW);
BEGIN
    PERFORM deltallm_accounting_sync_budget(
        TG_ARGV[0],item->>TG_ARGV[1],NULLIF(item->>'max_budget','')::numeric,
        COALESCE(NULLIF(item->>'spend_exact','')::numeric,
                 NULLIF(item->>'spend','')::numeric,0),
        NULLIF(item->>'budget_duration',''),
        NULLIF(item->>'budget_reset_at','')::timestamp,
        item->'metadata'
    );
    RETURN NEW;
END;
$$;

CREATE FUNCTION deltallm_accounting_team_model_policy_trigger() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE
    model_name TEXT;
    model_limit NUMERIC;
    committed NUMERIC;
    old_budgets JSONB := CASE WHEN TG_OP='INSERT' THEN '{}'::jsonb
                              ELSE COALESCE(OLD.model_max_budget,'{}'::jsonb) END;
BEGIN
    FOR model_name IN
        SELECT key FROM jsonb_object_keys(old_budgets) key
        UNION
        SELECT key FROM jsonb_object_keys(COALESCE(NEW.model_max_budget,'{}'::jsonb)) key
        ORDER BY 1
    LOOP
        model_limit := NULLIF(NEW.model_max_budget->>model_name,'')::numeric;
        SELECT COALESCE(spend_exact,spend::numeric,0) INTO committed
        FROM deltallm_teammodelspend
        WHERE team_id=NEW.team_id AND model=model_name;
        PERFORM deltallm_accounting_sync_budget(
            'team_model',NEW.team_id||':'||model_name,model_limit,COALESCE(committed,0),
            NULL,NULL,NULL
        );
    END LOOP;
    RETURN NEW;
END;
$$;

CREATE TRIGGER deltallm_accounting_key_budget_policy
AFTER INSERT OR UPDATE OF max_budget,budget_duration,budget_reset_at,metadata
ON deltallm_verificationtoken FOR EACH ROW
EXECUTE FUNCTION deltallm_accounting_budget_policy_trigger('api_key','token');
CREATE TRIGGER deltallm_accounting_user_budget_policy
AFTER INSERT OR UPDATE OF max_budget,budget_duration,budget_reset_at,metadata
ON deltallm_usertable FOR EACH ROW
EXECUTE FUNCTION deltallm_accounting_budget_policy_trigger('user','user_id');
CREATE TRIGGER deltallm_accounting_team_budget_policy
AFTER INSERT OR UPDATE OF max_budget,budget_duration,budget_reset_at,metadata
ON deltallm_teamtable FOR EACH ROW
EXECUTE FUNCTION deltallm_accounting_budget_policy_trigger('team','team_id');
CREATE TRIGGER deltallm_accounting_organization_budget_policy
AFTER INSERT OR UPDATE OF max_budget,budget_duration,budget_reset_at,metadata
ON deltallm_organizationtable FOR EACH ROW
EXECUTE FUNCTION deltallm_accounting_budget_policy_trigger('organization','organization_id');
CREATE TRIGGER deltallm_accounting_team_model_budget_policy
AFTER INSERT OR UPDATE OF model_max_budget ON deltallm_teamtable FOR EACH ROW
EXECUTE FUNCTION deltallm_accounting_team_model_policy_trigger();

CREATE FUNCTION deltallm_accounting_next_window_end(
    p_spec TEXT,
    p_previous_end TIMESTAMPTZ,
    p_anchor_day INTEGER,
    p_now TIMESTAMPTZ
) RETURNS TIMESTAMPTZ
LANGUAGE plpgsql STABLE AS $$
DECLARE
    amount INTEGER;
    unit_name TEXT;
    candidate TIMESTAMPTZ := p_previous_end;
    month_start TIMESTAMPTZ;
    days_in_month INTEGER;
    iterations INTEGER := 0;
BEGIN
    IF p_spec !~ '^([1-9][0-9]{0,3}|10000)(h|d|mo)$' THEN
        RAISE EXCEPTION 'accounting_window_renewal_spec' USING ERRCODE = 'P0001';
    END IF;
    amount := substring(p_spec FROM '^[0-9]+')::integer;
    unit_name := substring(p_spec FROM '(h|d|mo)$');
    WHILE candidate<=p_now LOOP
        iterations := iterations + 1;
        IF iterations>10000 THEN
            RAISE EXCEPTION 'accounting_window_renewal_iterations' USING ERRCODE = 'P0001';
        END IF;
        IF unit_name='h' THEN
            candidate := candidate+make_interval(hours=>amount);
        ELSIF unit_name='d' THEN
            candidate := candidate+make_interval(days=>amount);
        ELSE
            month_start := date_trunc('month',candidate)+make_interval(months=>amount);
            days_in_month := extract(day FROM (
                month_start+interval '1 month'-interval '1 day'
            ))::integer;
            candidate := month_start
                + make_interval(days=>least(p_anchor_day,days_in_month)-1)
                + (candidate-date_trunc('day',candidate));
        END IF;
    END LOOP;
    RETURN candidate;
END;
$$;

CREATE FUNCTION deltallm_accounting_roll_windows(p_generation BIGINT, p_limit INTEGER)
RETURNS INTEGER
LANGUAGE plpgsql AS $$
DECLARE
    previous_window deltallm_accounting_budget_windows%ROWTYPE;
    next_end TIMESTAMPTZ;
    rolled_count INTEGER := 0;
BEGIN
    IF p_limit NOT BETWEEN 1 AND 256 THEN
        RAISE EXCEPTION 'accounting_window_roll_limit' USING ERRCODE = 'P0001';
    END IF;
    FOR previous_window IN
        SELECT w.* FROM deltallm_accounting_budget_windows w
        WHERE w.protocol_name='primary' AND w.generation=p_generation
          AND w.renewal_spec IS NOT NULL AND w.window_ends_at<=CURRENT_TIMESTAMP
          AND w.reserved_exact=0
          AND NOT EXISTS (
              SELECT 1 FROM deltallm_accounting_budget_windows newer
              WHERE newer.protocol_name=w.protocol_name AND newer.generation=w.generation
                AND newer.scope_type=w.scope_type AND newer.scope_id=w.scope_id
                AND newer.window_starts_at>=w.window_ends_at
          )
        ORDER BY w.window_ends_at,w.window_id
        FOR UPDATE SKIP LOCKED LIMIT p_limit
    LOOP
        next_end := deltallm_accounting_next_window_end(
            previous_window.renewal_spec,
            previous_window.window_ends_at,
            previous_window.renewal_anchor_day,
            CURRENT_TIMESTAMP
        );
        INSERT INTO deltallm_accounting_budget_windows(
            window_id,protocol_name,generation,scope_type,scope_id,period_key,currency,
            policy_generation,limit_exact,window_starts_at,window_ends_at,
            renewal_spec,renewal_anchor_day
        ) VALUES (
            md5('accounting-v2:'||p_generation||':'||previous_window.scope_type||':'||
                previous_window.scope_id||':'||previous_window.window_ends_at::text),
            'primary',p_generation,previous_window.scope_type,previous_window.scope_id,
            'renewal:v1:'||extract(epoch FROM previous_window.window_ends_at)::bigint,
            previous_window.currency,previous_window.policy_generation,
            previous_window.limit_exact,previous_window.window_ends_at,next_end,
            previous_window.renewal_spec,previous_window.renewal_anchor_day
        ) ON CONFLICT DO NOTHING;
        IF FOUND THEN
            rolled_count := rolled_count+1;
        END IF;
    END LOOP;
    RETURN rolled_count;
END;
$$;

-- Reservation is also the durable dispatch transition. A replay never receives
-- a token and therefore cannot repeat provider work to repair accounting.
CREATE FUNCTION deltallm_accounting_reserve_batch(p_generation BIGINT, p_items JSONB)
RETURNS TABLE(operation_id TEXT, decision TEXT, dispatch_token TEXT, accounting_partition INTEGER)
LANGUAGE plpgsql AS $$
DECLARE
    protocol_row deltallm_accounting_protocols%ROWTYPE;
    item JSONB;
    op deltallm_billing_operations%ROWTYPE;
    allowance NUMERIC(38,18);
    candidate_partition INTEGER;
    requested_windows INTEGER;
    matched_windows INTEGER;
    exhausted BOOLEAN;
BEGIN
    IF jsonb_typeof(p_items) <> 'array' OR jsonb_array_length(p_items) NOT BETWEEN 1 AND 64 THEN
        RAISE EXCEPTION 'accounting_reservation_batch_shape' USING ERRCODE = 'P0001';
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

    -- All paths use operation -> budget window -> partition lock order.
    PERFORM 1 FROM deltallm_billing_operations b
        WHERE b.operation_id IN (
            SELECT value->>'operation_id' FROM jsonb_array_elements(p_items) value
        ) ORDER BY b.operation_id FOR UPDATE;
    PERFORM 1 FROM deltallm_accounting_budget_windows w
        WHERE w.protocol_name='primary' AND w.generation=p_generation AND (
            w.window_id IN (
                SELECT ref->>'window_id'
                FROM jsonb_array_elements(p_items) value,
                     jsonb_array_elements(value->'windows') ref
            ) OR EXISTS (
                SELECT 1 FROM jsonb_array_elements(p_items) value
                WHERE jsonb_array_length(value->'windows')=0
                  AND w.window_starts_at<=CURRENT_TIMESTAMP
                  AND w.window_ends_at>CURRENT_TIMESTAMP
                  AND CASE w.scope_type
                        WHEN 'api_key' THEN w.scope_id=value#>>'{attribution,api_key}'
                        WHEN 'user' THEN w.scope_id=value#>>'{attribution,user_id}'
                        WHEN 'team' THEN w.scope_id=value#>>'{attribution,team_id}'
                        WHEN 'organization' THEN w.scope_id=value#>>'{attribution,organization_id}'
                        WHEN 'team_model' THEN w.scope_id=(value#>>'{attribution,team_id}')||':'||
                                                           (value#>>'{attribution,model}')
                      END
            )
        ) ORDER BY w.window_id FOR UPDATE;
    PERFORM 1 FROM deltallm_accounting_partitions p
        WHERE p.protocol_name='primary' AND p.generation=p_generation
          AND p.partition_id IN (
              SELECT mod((('x'||substr(md5(value->>'operation_id'),1,8))::bit(32)::bigint),
                         protocol_row.partition_count)::integer
              FROM jsonb_array_elements(p_items) value
          ) ORDER BY p.partition_id FOR UPDATE;

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
        candidate_partition := mod(
            (('x'||substr(md5(item->>'operation_id'),1,8))::bit(32)::bigint),
            protocol_row.partition_count
        )::integer;

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
            operation_id := op.operation_id;
            decision := 'replay';
            dispatch_token := NULL;
            accounting_partition := NULL;
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
        IF requested_windows > 0 THEN
            SELECT count(*)::integer INTO matched_windows
            FROM jsonb_array_elements(item->'windows') ref
            JOIN deltallm_accounting_budget_windows w ON w.window_id=ref->>'window_id'
            WHERE w.protocol_name='primary' AND w.generation=p_generation
              AND w.scope_type=ref->>'scope_type' AND w.scope_id=ref->>'scope_id'
              AND w.policy_generation=(ref->>'policy_generation')::bigint
              AND w.window_starts_at<=CURRENT_TIMESTAMP
              AND w.window_ends_at>CURRENT_TIMESTAMP
              AND CASE w.scope_type
                    WHEN 'api_key' THEN w.scope_id=item#>>'{attribution,api_key}'
                    WHEN 'user' THEN w.scope_id=item#>>'{attribution,user_id}'
                    WHEN 'team' THEN w.scope_id=item#>>'{attribution,team_id}'
                    WHEN 'organization' THEN w.scope_id=item#>>'{attribution,organization_id}'
                    WHEN 'team_model' THEN w.scope_id=(item#>>'{attribution,team_id}')||':'||
                                                       (item#>>'{attribution,model}')
                  END;
        ELSE
            SELECT count(*)::integer INTO matched_windows
            FROM deltallm_accounting_budget_windows w
            WHERE w.protocol_name='primary' AND w.generation=p_generation
              AND w.window_starts_at<=CURRENT_TIMESTAMP
              AND w.window_ends_at>CURRENT_TIMESTAMP
              AND CASE w.scope_type
                    WHEN 'api_key' THEN w.scope_id=item#>>'{attribution,api_key}'
                    WHEN 'user' THEN w.scope_id=item#>>'{attribution,user_id}'
                    WHEN 'team' THEN w.scope_id=item#>>'{attribution,team_id}'
                    WHEN 'organization' THEN w.scope_id=item#>>'{attribution,organization_id}'
                    WHEN 'team_model' THEN w.scope_id=(item#>>'{attribution,team_id}')||':'||
                                                       (item#>>'{attribution,model}')
                  END;
        END IF;
        IF requested_windows > 0 AND matched_windows <> requested_windows THEN
            RAISE EXCEPTION 'accounting_budget_window_unavailable' USING ERRCODE = 'P0001';
        END IF;

        SELECT EXISTS (
            SELECT 1 FROM deltallm_accounting_budget_windows w
            WHERE w.protocol_name='primary' AND w.generation=p_generation
              AND w.window_starts_at<=CURRENT_TIMESTAMP
              AND w.window_ends_at>CURRENT_TIMESTAMP
              AND (
                (requested_windows > 0 AND w.window_id IN (
                    SELECT ref->>'window_id' FROM jsonb_array_elements(item->'windows') ref
                )) OR (requested_windows=0 AND CASE w.scope_type
                    WHEN 'api_key' THEN w.scope_id=item#>>'{attribution,api_key}'
                    WHEN 'user' THEN w.scope_id=item#>>'{attribution,user_id}'
                    WHEN 'team' THEN w.scope_id=item#>>'{attribution,team_id}'
                    WHEN 'organization' THEN w.scope_id=item#>>'{attribution,organization_id}'
                    WHEN 'team_model' THEN w.scope_id=(item#>>'{attribution,team_id}')||':'||
                                                       (item#>>'{attribution,model}')
                  END)
              )
              AND w.committed_exact+w.reserved_exact+w.provisional_exact+allowance>w.limit_exact
        ) INTO exhausted;
        IF exhausted THEN
            operation_id := item->>'operation_id';
            decision := 'budget_exhausted';
            dispatch_token := NULL;
            accounting_partition := NULL;
            RETURN NEXT;
            CONTINUE;
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM deltallm_accounting_partitions p
            WHERE p.protocol_name='primary' AND p.generation=p_generation
              AND p.partition_id=candidate_partition
              AND p.max_outstanding=protocol_row.max_outstanding_per_partition
              AND p.outstanding_count<p.max_outstanding
        ) THEN
            operation_id := item->>'operation_id';
            decision := 'capacity_exhausted';
            dispatch_token := NULL;
            accounting_partition := NULL;
            RETURN NEXT;
            CONTINUE;
        END IF;

        INSERT INTO deltallm_billing_operations (
            operation_id,owner_token,api_key,user_id,team_id,organization_id,model,snapshot,
            selector_event_id,selector_allowance,answer_allowance,selector_state,answer_state,
            expires_at,accounting_protocol,accounting_generation,accounting_partition,
            request_fingerprint,accounting_state,provisional_debit_exact
        ) VALUES (
            item->>'operation_id',item->>'owner_token',item#>>'{attribution,api_key}',
            item#>>'{attribution,user_id}',item#>>'{attribution,team_id}',
            item#>>'{attribution,organization_id}',item#>>'{attribution,model}',item,
            (item->>'operation_id')||':selector',0,allowance,'unattempted','dispatched',
            (item->>'expires_at')::timestamptz,'primary',p_generation,candidate_partition,
            item->>'request_fingerprint','reserved',0
        );
        INSERT INTO deltallm_accounting_reservations(operation_id,window_id,allowance_exact)
            SELECT item->>'operation_id',w.window_id,allowance
            FROM deltallm_accounting_budget_windows w
            WHERE w.protocol_name='primary' AND w.generation=p_generation
              AND w.window_starts_at<=CURRENT_TIMESTAMP
              AND w.window_ends_at>CURRENT_TIMESTAMP
              AND (
                (requested_windows > 0 AND w.window_id IN (
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
            SET reserved_exact=w.reserved_exact+allowance,updated_at=NOW()
            WHERE w.protocol_name='primary' AND w.generation=p_generation
              AND w.window_starts_at<=CURRENT_TIMESTAMP
              AND w.window_ends_at>CURRENT_TIMESTAMP
              AND (
                (requested_windows > 0 AND w.window_id IN (
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
        UPDATE deltallm_accounting_partitions
            SET outstanding_count=outstanding_count+1,updated_at=NOW()
            WHERE protocol_name='primary' AND generation=p_generation
              AND partition_id=candidate_partition;
        INSERT INTO deltallm_accounting_events(
            event_id,protocol_name,generation,accounting_partition,operation_id,
            component_id,event_type,payload_json,audit_envelope_json,occurred_at
        ) VALUES (
            (item->>'operation_id')||':reserved','primary',p_generation,candidate_partition,
            item->>'operation_id','provider','reserved',
            jsonb_build_object('allowance',allowance::text,'pricing_snapshot',item->'pricing_snapshot'),
            item->'audit_envelope',CURRENT_TIMESTAMP
        );
        operation_id := item->>'operation_id';
        decision := 'dispatch';
        dispatch_token := item->>'owner_token';
        accounting_partition := candidate_partition;
        RETURN NEXT;
    END LOOP;
END;
$$;

-- Finalization stores spend and required audit in the same append-only event.
-- Uncertain dispatch consumes the full allowance as a provisional hard-budget
-- debit without presenting it as invoiceable committed spend.
CREATE FUNCTION deltallm_accounting_finalize_batch(p_generation BIGINT, p_items JSONB)
RETURNS TABLE(operation_id TEXT, event_sequence BIGINT, outcome TEXT, replayed BOOLEAN)
LANGUAGE plpgsql AS $$
DECLARE
    protocol_row deltallm_accounting_protocols%ROWTYPE;
    item JSONB;
    op deltallm_billing_operations%ROWTYPE;
    existing_event deltallm_accounting_events%ROWTYPE;
    reservation deltallm_accounting_reservations%ROWTYPE;
    exact_charge NUMERIC(38,18);
    sequence_value BIGINT;
BEGIN
    IF jsonb_typeof(p_items) <> 'array' OR jsonb_array_length(p_items) NOT BETWEEN 1 AND 64 THEN
        RAISE EXCEPTION 'accounting_finalization_batch_shape' USING ERRCODE = 'P0001';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_items) value
        GROUP BY value->>'operation_id' HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'accounting_finalization_duplicate_operation' USING ERRCODE = 'P0001';
    END IF;
    SELECT * INTO STRICT protocol_row FROM deltallm_accounting_protocols
        WHERE protocol_name='primary' AND generation=p_generation FOR SHARE;
    IF protocol_row.state NOT IN ('active','draining') OR protocol_row.writer_version <> 2 THEN
        RAISE EXCEPTION 'accounting_protocol_not_finalizable' USING ERRCODE = 'P0001';
    END IF;

    PERFORM 1 FROM deltallm_billing_operations b
        WHERE b.operation_id IN (
            SELECT value->>'operation_id' FROM jsonb_array_elements(p_items) value
        ) ORDER BY b.operation_id FOR UPDATE;
    PERFORM 1 FROM deltallm_accounting_budget_windows w
        WHERE w.window_id IN (
            SELECT r.window_id FROM deltallm_accounting_reservations r
            WHERE r.operation_id IN (
                SELECT value->>'operation_id' FROM jsonb_array_elements(p_items) value
            )
        ) ORDER BY w.window_id FOR UPDATE;
    PERFORM 1 FROM deltallm_accounting_partitions p
        WHERE p.protocol_name='primary' AND p.generation=p_generation
          AND p.partition_id IN (
              SELECT b.accounting_partition FROM deltallm_billing_operations b
              WHERE b.operation_id IN (
                  SELECT value->>'operation_id' FROM jsonb_array_elements(p_items) value
              )
          ) ORDER BY p.partition_id FOR UPDATE;

    FOR item IN SELECT value FROM jsonb_array_elements(p_items) value
                ORDER BY value->>'operation_id'
    LOOP
        IF (item->>'protocol_generation')::bigint <> p_generation
           OR item->>'request_fingerprint' !~ '^[0-9a-f]{64}$'
           OR item->>'outcome' NOT IN ('completed','not_dispatched','uncertain')
           OR COALESCE((item->>'unresolved_attempts')::integer,0) NOT BETWEEN 0 AND 127
           OR jsonb_typeof(item->'audit_envelope') <> 'object'
           OR octet_length((item->'audit_envelope')::text) > 65536 THEN
            RAISE EXCEPTION 'accounting_finalization_item_shape' USING ERRCODE = 'P0001';
        END IF;
        SELECT * INTO STRICT op FROM deltallm_billing_operations
            WHERE deltallm_billing_operations.operation_id=item->>'operation_id';
        IF op.accounting_protocol <> 'primary' OR op.accounting_generation <> p_generation
           OR op.owner_token <> item->>'owner_token'
           OR op.request_fingerprint <> item->>'request_fingerprint' THEN
            RAISE EXCEPTION 'accounting_finalization_identity_conflict' USING ERRCODE = 'P0001';
        END IF;
        IF op.accounting_state <> 'reserved' THEN
            SELECT e.* INTO STRICT existing_event FROM deltallm_accounting_events e
                WHERE e.operation_id=op.operation_id AND e.component_id=item->>'component_id'
                  AND e.event_type='finalized' AND e.event_id=item->>'event_id'
                  AND e.outcome=item->>'outcome';
            IF existing_event.payload_json->'spend'
                    IS DISTINCT FROM item->'spend_payload'
               OR existing_event.payload_json->>'exact_charge'
                    IS DISTINCT FROM item->>'exact_charge'
               OR existing_event.payload_json->>'uncertainty_reason'
                    IS DISTINCT FROM item->>'uncertainty_reason'
               OR COALESCE((existing_event.payload_json->>'unresolved_attempts')::integer,0)
                    <> COALESCE((item->>'unresolved_attempts')::integer,0)
               OR existing_event.audit_envelope_json<>item->'audit_envelope' THEN
                RAISE EXCEPTION 'accounting_finalization_replay_conflict'
                    USING ERRCODE = 'P0001';
            END IF;
            operation_id := op.operation_id;
            event_sequence := existing_event.sequence;
            outcome := item->>'outcome';
            replayed := TRUE;
            RETURN NEXT;
            CONTINUE;
        END IF;

        exact_charge := CASE WHEN item->>'exact_charge' IS NULL THEN NULL
                             ELSE (item->>'exact_charge')::numeric END;
        IF item->>'outcome'='completed' AND (
                exact_charge IS NULL OR exact_charge < 0 OR item->'spend_payload' IS NULL
                OR item->'spend_payload' = 'null'::jsonb
                OR jsonb_typeof(item->'spend_payload') <> 'object'
                OR octet_length((item->'spend_payload')::text) > 262144
                OR exact_charge > op.answer_allowance) THEN
            RAISE EXCEPTION 'accounting_completed_receipt_invalid' USING ERRCODE = 'P0001';
        ELSIF item->>'outcome'='not_dispatched' AND (
                COALESCE(exact_charge,0) <> 0
                OR COALESCE(item->'spend_payload','null'::jsonb) <> 'null'::jsonb
                OR item->>'uncertainty_reason' IS NOT NULL
                OR COALESCE((item->>'unresolved_attempts')::integer,0)<>0) THEN
            RAISE EXCEPTION 'accounting_not_dispatched_receipt_invalid' USING ERRCODE = 'P0001';
        ELSIF item->>'outcome'='uncertain' AND (
                exact_charge IS NOT NULL
                OR COALESCE(item->'spend_payload','null'::jsonb) <> 'null'::jsonb
                OR item->>'uncertainty_reason' IS NULL
                OR COALESCE((item->>'unresolved_attempts')::integer,0)<>0) THEN
            RAISE EXCEPTION 'accounting_uncertain_receipt_invalid' USING ERRCODE = 'P0001';
        END IF;

        FOR reservation IN SELECT * FROM deltallm_accounting_reservations r
                           WHERE r.operation_id=op.operation_id ORDER BY r.window_id
        LOOP
            IF item->>'outcome'='completed' THEN
                UPDATE deltallm_accounting_budget_windows
                    SET reserved_exact=reserved_exact-reservation.allowance_exact,
                        committed_exact=committed_exact+exact_charge,
                        provisional_exact=provisional_exact+CASE
                            WHEN COALESCE((item->>'unresolved_attempts')::integer,0)>0
                            THEN reservation.allowance_exact-exact_charge ELSE 0 END,
                        updated_at=NOW()
                    WHERE window_id=reservation.window_id;
                UPDATE deltallm_accounting_reservations ar
                    SET committed_exact=exact_charge,
                        provisional_exact=CASE
                            WHEN COALESCE((item->>'unresolved_attempts')::integer,0)>0
                            THEN allowance_exact-exact_charge ELSE 0 END,
                        released_exact=CASE
                            WHEN COALESCE((item->>'unresolved_attempts')::integer,0)>0
                            THEN 0 ELSE allowance_exact-exact_charge END,
                        updated_at=NOW()
                    WHERE ar.operation_id=op.operation_id
                      AND ar.window_id=reservation.window_id;
            ELSIF item->>'outcome'='not_dispatched' THEN
                UPDATE deltallm_accounting_budget_windows
                    SET reserved_exact=reserved_exact-reservation.allowance_exact,updated_at=NOW()
                    WHERE window_id=reservation.window_id;
                UPDATE deltallm_accounting_reservations ar
                    SET released_exact=allowance_exact,updated_at=NOW()
                    WHERE ar.operation_id=op.operation_id
                      AND ar.window_id=reservation.window_id;
            ELSE
                UPDATE deltallm_accounting_budget_windows
                    SET reserved_exact=reserved_exact-reservation.allowance_exact,
                        provisional_exact=provisional_exact+reservation.allowance_exact,
                        updated_at=NOW()
                    WHERE window_id=reservation.window_id;
                UPDATE deltallm_accounting_reservations ar
                    SET provisional_exact=allowance_exact,updated_at=NOW()
                    WHERE ar.operation_id=op.operation_id
                      AND ar.window_id=reservation.window_id;
            END IF;
        END LOOP;

        INSERT INTO deltallm_accounting_events(
            event_id,protocol_name,generation,accounting_partition,operation_id,
            component_id,event_type,outcome,payload_json,audit_envelope_json,occurred_at
        ) VALUES (
            item->>'event_id','primary',p_generation,op.accounting_partition,op.operation_id,
            item->>'component_id','finalized',item->>'outcome',
            jsonb_build_object(
                'spend',item->'spend_payload','exact_charge',item->'exact_charge',
                'uncertainty_reason',item->'uncertainty_reason',
                'unresolved_attempts',COALESCE((item->>'unresolved_attempts')::integer,0)
            ),item->'audit_envelope',(item->>'occurred_at')::timestamptz
        ) RETURNING sequence INTO sequence_value;
        UPDATE deltallm_billing_operations SET
            accounting_state=CASE item->>'outcome'
                WHEN 'completed' THEN CASE
                    WHEN COALESCE((item->>'unresolved_attempts')::integer,0)>0
                    THEN 'provisional' ELSE 'finalized' END
                WHEN 'not_dispatched' THEN 'released'
                ELSE 'provisional' END,
            answer_state=CASE item->>'outcome'
                WHEN 'not_dispatched' THEN 'unattempted'
                WHEN 'uncertain' THEN 'pending'
                ELSE 'settled' END,
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
        UPDATE deltallm_accounting_partitions
            SET outstanding_count=outstanding_count-1,updated_at=NOW()
            WHERE protocol_name='primary' AND generation=p_generation
              AND partition_id=op.accounting_partition AND outstanding_count>0;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'accounting_partition_drift' USING ERRCODE = 'P0001';
        END IF;
        operation_id := op.operation_id;
        event_sequence := sequence_value;
        outcome := item->>'outcome';
        replayed := FALSE;
        RETURN NEXT;
    END LOOP;
END;
$$;

-- A process crash after provider dispatch cannot safely release money. The
-- worker converts expired reservations to a visible provisional debit without
CREATE FUNCTION deltallm_accounting_reconcile_expired(p_generation BIGINT, p_limit INTEGER)
RETURNS INTEGER
LANGUAGE plpgsql AS $$
DECLARE
    op deltallm_billing_operations%ROWTYPE;
    reservation deltallm_accounting_reservations%ROWTYPE;
    sequence_value BIGINT;
    reconciled_count INTEGER := 0;
    expiry_event_id TEXT;
    expiry_audit JSONB;
BEGIN
    IF p_limit NOT BETWEEN 1 AND 256 THEN
        RAISE EXCEPTION 'accounting_recovery_limit' USING ERRCODE = 'P0001';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM deltallm_accounting_protocols
        WHERE protocol_name='primary' AND generation=p_generation
          AND state IN ('active','draining') AND writer_version=2
    ) THEN
        RAISE EXCEPTION 'accounting_protocol_not_recoverable' USING ERRCODE = 'P0001';
    END IF;

    FOR op IN
        SELECT b.* FROM deltallm_billing_operations b
        WHERE b.accounting_protocol='primary'
          AND b.accounting_generation=p_generation
          AND b.accounting_state='reserved'
          AND b.expires_at<=CURRENT_TIMESTAMP
        ORDER BY b.operation_id
        FOR UPDATE SKIP LOCKED LIMIT p_limit
    LOOP
        -- Preserve the global operation -> window -> partition lock order.
        PERFORM 1 FROM deltallm_accounting_budget_windows w
        JOIN deltallm_accounting_reservations r ON r.window_id=w.window_id
        WHERE r.operation_id=op.operation_id ORDER BY w.window_id FOR UPDATE OF w;
        PERFORM 1 FROM deltallm_accounting_partitions p
        WHERE p.protocol_name='primary' AND p.generation=p_generation
          AND p.partition_id=op.accounting_partition FOR UPDATE;

        FOR reservation IN SELECT * FROM deltallm_accounting_reservations r
                           WHERE r.operation_id=op.operation_id ORDER BY r.window_id
        LOOP
            UPDATE deltallm_accounting_budget_windows
                SET reserved_exact=reserved_exact-reservation.allowance_exact,
                    provisional_exact=provisional_exact+reservation.allowance_exact,
                    updated_at=NOW()
                WHERE window_id=reservation.window_id;
            UPDATE deltallm_accounting_reservations ar
                SET provisional_exact=allowance_exact,updated_at=NOW()
                WHERE ar.operation_id=op.operation_id
                  AND ar.window_id=reservation.window_id;
        END LOOP;

        expiry_event_id := op.operation_id||':expired:v2';
        expiry_audit := jsonb_build_object(
            'event_id',expiry_event_id||':audit',
            'record_type','audit_event',
            'organization_id',op.organization_id,
            'payload',jsonb_build_object(
                'event',jsonb_build_object(
                    'action','ACCOUNTING_PROVIDER_OUTCOME_UNKNOWN',
                    'organization_id',op.organization_id,
                    'actor_type','system',
                    'actor_id','accounting-recovery',
                    'api_key',op.api_key,
                    'resource_type','model',
                    'resource_id',op.model,
                    'request_id',op.operation_id,
                    'correlation_id',op.operation_id,
                    'status','error',
                    'error_type','AccountingReservationExpired',
                    'error_code','provider_outcome_unknown',
                    'metadata',jsonb_build_object('allowance_exact',op.answer_allowance::text),
                    'event_id',expiry_event_id
                ),'payloads','[]'::jsonb,'critical',TRUE
            )
        );
        expiry_audit := expiry_audit || jsonb_build_object(
            'redacted_payload',expiry_audit->'payload'
        );
        INSERT INTO deltallm_accounting_events(
            event_id,protocol_name,generation,accounting_partition,operation_id,
            component_id,event_type,outcome,payload_json,audit_envelope_json,occurred_at
        ) VALUES (
            expiry_event_id,'primary',p_generation,op.accounting_partition,op.operation_id,
            'provider','finalized','uncertain',jsonb_build_object(
                'spend',NULL,'exact_charge',NULL,
                'uncertainty_reason','reservation_expired','unresolved_attempts',0
            ),expiry_audit,NOW()
        ) RETURNING sequence INTO sequence_value;
        UPDATE deltallm_billing_operations SET
            accounting_state='provisional',answer_state='pending',
            provisional_debit_exact=answer_allowance,
            final_event_sequence=sequence_value,closed_at=NOW(),updated_at=NOW()
            WHERE operation_id=op.operation_id;
        UPDATE deltallm_accounting_partitions SET
            outstanding_count=outstanding_count-1,updated_at=NOW()
            WHERE protocol_name='primary' AND generation=p_generation
              AND partition_id=op.accounting_partition AND outstanding_count>0;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'accounting_partition_drift' USING ERRCODE = 'P0001';
        END IF;
        reconciled_count := reconciled_count + 1;
    END LOOP;
    RETURN reconciled_count;
END;
$$;

-- Reviewed provider evidence resolves a provisional debit without resending the
-- request. Any confirmed additional charge becomes a separate projected spend
-- event; the rest of the hold is released atomically from every scope.
CREATE FUNCTION deltallm_accounting_resolve_provisional(
    p_generation BIGINT,
    p_operation_id TEXT,
    p_additional_charge NUMERIC,
    p_spend_payload JSONB,
    p_evidence_reason TEXT
) RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE
    op deltallm_billing_operations%ROWTYPE;
    reservation deltallm_accounting_reservations%ROWTYPE;
    sequence_value BIGINT;
    resolution_event_id TEXT;
    resolution_audit JSONB;
    resolution_outcome TEXT;
BEGIN
    SELECT * INTO STRICT op FROM deltallm_billing_operations
        WHERE operation_id=p_operation_id FOR UPDATE;
    IF op.accounting_protocol<>'primary' OR op.accounting_generation<>p_generation THEN
        RAISE EXCEPTION 'accounting_provisional_resolution_identity'
            USING ERRCODE = 'P0001';
    END IF;
    resolution_event_id := op.operation_id||':reconciled:v2';
    SELECT e.sequence INTO sequence_value FROM deltallm_accounting_events e
    WHERE e.event_id=resolution_event_id
      AND e.operation_id=op.operation_id
      AND e.component_id='operator-reconciliation'
      AND e.event_type='reconciled'
      AND (e.payload_json->>'exact_charge')::numeric=p_additional_charge
      AND e.payload_json->'spend' IS NOT DISTINCT FROM p_spend_payload
      AND e.audit_envelope_json#>>'{payload,event,metadata,evidence_reason}'=p_evidence_reason;
    IF FOUND THEN
        RETURN sequence_value;
    ELSIF EXISTS (
        SELECT 1 FROM deltallm_accounting_events e WHERE e.event_id=resolution_event_id
    ) THEN
        RAISE EXCEPTION 'accounting_provisional_resolution_replay_conflict'
            USING ERRCODE = 'P0001';
    END IF;
    IF op.accounting_state<>'provisional' OR op.provisional_debit_exact<=0
       OR p_additional_charge<0 OR p_additional_charge>op.provisional_debit_exact
       OR length(p_evidence_reason) NOT BETWEEN 1 AND 256
       OR (p_additional_charge=0 AND p_spend_payload<>'null'::jsonb)
       OR (p_additional_charge>0 AND (
            p_spend_payload='null'::jsonb OR jsonb_typeof(p_spend_payload)<>'object'
            OR octet_length(p_spend_payload::text)>262144
       )) THEN
        RAISE EXCEPTION 'accounting_provisional_resolution_invalid' USING ERRCODE = 'P0001';
    END IF;

    PERFORM 1 FROM deltallm_accounting_budget_windows w
    JOIN deltallm_accounting_reservations r ON r.window_id=w.window_id
    WHERE r.operation_id=op.operation_id ORDER BY w.window_id FOR UPDATE OF w;
    FOR reservation IN SELECT * FROM deltallm_accounting_reservations r
                       WHERE r.operation_id=op.operation_id ORDER BY r.window_id
    LOOP
        IF reservation.provisional_exact< p_additional_charge THEN
            RAISE EXCEPTION 'accounting_provisional_resolution_exceeds_scope'
                USING ERRCODE = 'P0001';
        END IF;
        UPDATE deltallm_accounting_budget_windows SET
            provisional_exact=provisional_exact-reservation.provisional_exact,
            committed_exact=committed_exact+p_additional_charge,
            updated_at=NOW()
            WHERE window_id=reservation.window_id;
        UPDATE deltallm_accounting_reservations ar SET
            committed_exact=committed_exact+p_additional_charge,
            released_exact=released_exact+(provisional_exact-p_additional_charge),
            provisional_exact=0,updated_at=NOW()
            WHERE ar.operation_id=op.operation_id AND ar.window_id=reservation.window_id;
    END LOOP;

    resolution_outcome := CASE WHEN p_additional_charge>0 THEN 'completed' ELSE 'not_dispatched' END;
    resolution_audit := jsonb_build_object(
        'event_id',resolution_event_id||':audit','record_type','audit_event',
        'organization_id',op.organization_id,
        'payload',jsonb_build_object(
            'event',jsonb_build_object(
                'action','ACCOUNTING_PROVISIONAL_RECONCILED',
                'organization_id',op.organization_id,'actor_type','operator',
                'actor_id','reviewed-provider-evidence','api_key',op.api_key,
                'resource_type','model','resource_id',op.model,
                'request_id',op.operation_id,'correlation_id',op.operation_id,
                'status','success','metadata',jsonb_build_object(
                    'additional_charge_exact',p_additional_charge::text,
                    'evidence_reason',p_evidence_reason
                ),'event_id',resolution_event_id
            ),'payloads','[]'::jsonb,'critical',TRUE
        )
    );
    resolution_audit := resolution_audit || jsonb_build_object(
        'redacted_payload',resolution_audit->'payload'
    );
    INSERT INTO deltallm_accounting_events(
        event_id,protocol_name,generation,accounting_partition,operation_id,
        component_id,event_type,outcome,payload_json,audit_envelope_json,occurred_at
    ) VALUES (
        resolution_event_id,'primary',p_generation,op.accounting_partition,op.operation_id,
        'operator-reconciliation','reconciled',resolution_outcome,
        jsonb_build_object(
            'spend',p_spend_payload,'exact_charge',p_additional_charge::text,
            'uncertainty_reason',NULL,'unresolved_attempts',0
        ),resolution_audit,NOW()
    ) RETURNING sequence INTO sequence_value;
    UPDATE deltallm_billing_operations SET
        accounting_state='finalized',provisional_debit_exact=0,
        final_event_sequence=sequence_value,updated_at=NOW()
        WHERE operation_id=op.operation_id;
    RETURN sequence_value;
END;
$$;

-- The preparation script already owns table fences and calls the locked form.
-- The public wrapper acquires the policy fence for direct operator use.
CREATE FUNCTION deltallm_activate_accounting_protocol_locked(p_generation BIGINT) RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE
    protocol_row deltallm_accounting_protocols%ROWTYPE;
BEGIN
    SELECT * INTO STRICT protocol_row FROM deltallm_accounting_protocols
        WHERE protocol_name='primary' AND generation=p_generation FOR UPDATE;
    IF protocol_row.state <> 'prepared' OR EXISTS (
        SELECT 1 FROM deltallm_accounting_protocols
        WHERE protocol_name='primary' AND state IN ('active','draining')
    ) OR (SELECT count(*) FROM deltallm_accounting_partitions
          WHERE protocol_name='primary' AND generation=p_generation)
          <> protocol_row.partition_count
       OR EXISTS (
          SELECT 1 FROM deltallm_accounting_partitions
          WHERE protocol_name='primary' AND generation=p_generation
            AND (max_outstanding<>protocol_row.max_outstanding_per_partition
                 OR outstanding_count<>0)
       ) THEN
        RAISE EXCEPTION 'accounting_protocol_not_ready' USING ERRCODE = 'P0001';
    END IF;
    UPDATE deltallm_accounting_protocols
        SET state='active',activated_at=NOW(),updated_at=NOW()
        WHERE protocol_name='primary' AND generation=p_generation;
END;
$$;

CREATE FUNCTION deltallm_activate_accounting_protocol(p_generation BIGINT) RETURNS VOID
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('deltallm:accounting-protocol',0));
    PERFORM deltallm_activate_accounting_protocol_locked(p_generation);
END;
$$;

COMMIT;
