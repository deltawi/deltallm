-- Normalize expired compatibility counters during control-plane policy edits.
-- Native recovery remains the only owner of automatic window renewal.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

-- Recurrence is a UTC contract, independent of the database session timezone.
ALTER FUNCTION deltallm_accounting_next_window_end(TEXT,TIMESTAMPTZ,INTEGER,TIMESTAMPTZ)
    SET timezone = 'UTC';

CREATE FUNCTION deltallm_accounting_policy_charge(
    p_scope_type TEXT,p_scope_id TEXT,p_period_start TIMESTAMPTZ,p_legacy_projected BOOLEAN
) RETURNS NUMERIC LANGUAGE plpgsql AS $$
DECLARE
    amount NUMERIC;
    entries BIGINT;
    priced_entries BIGINT;
BEGIN
    -- Policy edits only: this is not a request-path or recovery-loop scan.
    SELECT COALESCE(sum((e.payload_json->>'exact_charge')::numeric),0),
           count(*),count(e.payload_json->>'exact_charge')
    INTO amount,entries,priced_entries
    FROM deltallm_billing_operations b
    JOIN deltallm_accounting_events e ON e.operation_id=b.operation_id
    WHERE b.accounting_protocol='primary' AND e.protocol_name='primary'
      AND e.event_type IN ('finalized','reconciled') AND e.outcome='completed'
      AND CASE p_scope_type
          WHEN 'api_key' THEN b.api_key
          WHEN 'user' THEN b.user_id
          WHEN 'team' THEN b.team_id
          WHEN 'organization' THEN b.organization_id
          WHEN 'team_model' THEN b.team_id||':'||b.model
      END=p_scope_id
      AND (p_period_start IS NULL OR
           COALESCE(NULLIF(e.payload_json#>>'{spend,start_time}','')::timestamptz,
                    b.created_at)>=p_period_start)
      AND EXISTS (
          SELECT 1 FROM deltallm_spendlog_events legacy WHERE legacy.id=e.event_id
      )=p_legacy_projected;
    IF entries<>priced_entries OR amount<0
       OR amount IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric) THEN
        RAISE EXCEPTION 'accounting_budget_policy_history_invalid' USING ERRCODE='P0001';
    END IF;
    RETURN amount;
END;
$$;

-- Keep the existing signature and exclusion contract for new native windows.
CREATE OR REPLACE FUNCTION deltallm_accounting_policy_native_charge(
    p_scope_type TEXT,p_scope_id TEXT,p_period_start TIMESTAMPTZ
) RETURNS NUMERIC LANGUAGE sql AS $$
    SELECT deltallm_accounting_policy_charge($1,$2,$3,FALSE);
$$;

CREATE FUNCTION deltallm_accounting_budget_period_trigger() RETURNS TRIGGER
LANGUAGE plpgsql SET timezone = 'UTC' AS $$
DECLARE
    previous JSONB := to_jsonb(OLD);
    item JSONB := to_jsonb(NEW);
    spec TEXT := NULLIF(previous->>'budget_duration','');
    reset_at TIMESTAMP := NULLIF(previous->>'budget_reset_at','')::timestamp;
    next_end TIMESTAMPTZ;
    period_start TIMESTAMPTZ;
    previous_month TIMESTAMP;
    amount INTEGER;
    anchor_day INTEGER;
    committed NUMERIC;
BEGIN
    -- Legacy mode and unexpired legacy baselines keep their existing semantics.
    IF spec IS NULL OR reset_at IS NULL
       OR reset_at AT TIME ZONE 'UTC'>CURRENT_TIMESTAMP
       OR NOT EXISTS (
           SELECT 1 FROM deltallm_accounting_protocols
           WHERE protocol_name='primary' AND state='active'
       ) THEN
        RETURN NEW;
    END IF;
    IF spec~'mo$' THEN
        anchor_day:=CASE WHEN previous#>>'{metadata,_budget_reset,monthly_anchor_day}'
                                  ~'^([1-9]|[12][0-9]|3[01])$'
                        THEN (previous#>>'{metadata,_budget_reset,monthly_anchor_day}')::integer
                        ELSE extract(day FROM reset_at)::integer END;
    END IF;
    next_end:=deltallm_accounting_next_window_end(
        spec,reset_at AT TIME ZONE 'UTC',anchor_day,CURRENT_TIMESTAMP
    );
    amount:=substring(spec FROM '^[0-9]+')::integer;
    IF spec~'mo$' THEN
        previous_month:=(next_end AT TIME ZONE 'UTC')-make_interval(months=>amount);
        period_start:=(date_trunc('month',previous_month)
            +make_interval(days=>least(anchor_day,extract(day FROM
                date_trunc('month',previous_month)+interval '1 month'-interval '1 day')::integer)-1)
            +(previous_month-date_trunc('day',previous_month))) AT TIME ZONE 'UTC';
    ELSE
        period_start:=next_end-CASE WHEN spec~'h$' THEN make_interval(hours=>amount)
                                  ELSE make_interval(days=>amount) END;
    END IF;

    -- The entity UPDATE already owns the row lock used by legacy projection.
    -- Keep only its current-period projected charges. The existing AFTER trigger
    -- adds unprojected charges when it must create a native budget window.
    committed:=deltallm_accounting_policy_charge(TG_ARGV[0],item->>TG_ARGV[1],period_start,TRUE);
    NEW.spend:=committed;
    NEW.spend_exact:=committed;
    -- Advance an unchanged schedule, but never repair an explicitly invalid date.
    IF NEW.budget_duration IS NOT DISTINCT FROM OLD.budget_duration
       AND NEW.budget_reset_at IS NOT DISTINCT FROM OLD.budget_reset_at THEN
        NEW.budget_reset_at:=next_end AT TIME ZONE 'UTC';
        IF anchor_day IS NOT NULL THEN
            NEW.metadata:=COALESCE(NEW.metadata,'{}'::jsonb)||jsonb_build_object(
                '_budget_reset',COALESCE(NEW.metadata->'_budget_reset','{}'::jsonb)
                    ||jsonb_build_object('monthly_anchor_day',anchor_day)
            );
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER deltallm_accounting_key_budget_period
BEFORE UPDATE OF max_budget,budget_duration,budget_reset_at,metadata
ON deltallm_verificationtoken FOR EACH ROW
EXECUTE FUNCTION deltallm_accounting_budget_period_trigger('api_key','token');
CREATE TRIGGER deltallm_accounting_user_budget_period
BEFORE UPDATE OF max_budget,budget_duration,budget_reset_at,metadata
ON deltallm_usertable FOR EACH ROW
EXECUTE FUNCTION deltallm_accounting_budget_period_trigger('user','user_id');
CREATE TRIGGER deltallm_accounting_team_budget_period
BEFORE UPDATE OF max_budget,budget_duration,budget_reset_at,metadata
ON deltallm_teamtable FOR EACH ROW
EXECUTE FUNCTION deltallm_accounting_budget_period_trigger('team','team_id');
CREATE TRIGGER deltallm_accounting_organization_budget_period
BEFORE UPDATE OF max_budget,budget_duration,budget_reset_at,metadata
ON deltallm_organizationtable FOR EACH ROW
EXECUTE FUNCTION deltallm_accounting_budget_period_trigger('organization','organization_id');

-- Use the allocator's protocol fence before locking a renewable window. This
-- prevents rollover from racing a policy edit that creates or removes a cap.
CREATE OR REPLACE FUNCTION deltallm_accounting_roll_windows(p_generation BIGINT,p_limit INTEGER)
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE
    previous_window deltallm_accounting_budget_windows%ROWTYPE;
    next_end TIMESTAMPTZ;
    rolled_count INTEGER := 0;
BEGIN
    IF p_limit NOT BETWEEN 1 AND 256 THEN
        RAISE EXCEPTION 'accounting_window_roll_limit' USING ERRCODE='P0001';
    END IF;
    PERFORM generation FROM deltallm_accounting_protocols
    WHERE protocol_name='primary' AND generation=p_generation AND state='active'
    FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'accounting_protocol_unavailable' USING ERRCODE='P0001';
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
        next_end:=deltallm_accounting_next_window_end(
            previous_window.renewal_spec,previous_window.window_ends_at,
            previous_window.renewal_anchor_day,CURRENT_TIMESTAMP
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
            rolled_count:=rolled_count+1;
        END IF;
    END LOOP;
    RETURN rolled_count;
END;
$$;

-- A cap removed before rollover must not return on the next maintenance cycle.
DO $migration$
DECLARE
    definition TEXT;
    marker TEXT := $marker$    IF p_limit IS NULL OR p_limit<0 OR p_limit>=1e20::numeric THEN
        IF FOUND THEN$marker$;
    replacement TEXT := $replacement$    IF p_limit IS NULL OR p_limit<0 OR p_limit>=1e20::numeric THEN
        IF active_window.window_id IS NULL THEN
            UPDATE deltallm_accounting_budget_windows SET
                renewal_spec=NULL,renewal_anchor_day=NULL,
                policy_generation=policy_generation+1,updated_at=NOW()
            WHERE window_id=(
                SELECT w.window_id FROM deltallm_accounting_budget_windows w
                WHERE w.protocol_name='primary' AND w.generation=protocol_row.generation
                  AND w.scope_type=p_scope_type AND w.scope_id=p_scope_id
                  AND w.renewal_spec IS NOT NULL AND w.window_ends_at<=CURRENT_TIMESTAMP
                  AND NOT EXISTS (
                      SELECT 1 FROM deltallm_accounting_budget_windows newer
                      WHERE newer.protocol_name=w.protocol_name AND newer.generation=w.generation
                        AND newer.scope_type=w.scope_type AND newer.scope_id=w.scope_id
                        AND newer.window_starts_at>=w.window_ends_at
                  )
                ORDER BY w.window_ends_at DESC,w.window_id LIMIT 1
            );
        END IF;
        IF active_window.window_id IS NOT NULL THEN$replacement$;
BEGIN
    SELECT pg_get_functiondef(
        'deltallm_accounting_sync_budget(text,text,numeric,numeric,text,timestamp,jsonb)'::regprocedure
    ) INTO STRICT definition;
    IF (length(definition)-length(replace(definition,marker,'')))<>length(marker) THEN
        RAISE EXCEPTION 'accounting_budget_period_definition';
    END IF;
    EXECUTE replace(definition,marker,replacement);
END;
$migration$;

COMMIT;
