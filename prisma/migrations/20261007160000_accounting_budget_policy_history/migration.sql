-- A new hard budget must retain charges from the current budget period.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

CREATE FUNCTION deltallm_accounting_policy_native_charge(
    p_scope_type TEXT,p_scope_id TEXT,p_period_start TIMESTAMPTZ
) RETURNS NUMERIC LANGUAGE plpgsql AS $$
DECLARE
    amount NUMERIC;
    entries BIGINT;
    priced_entries BIGINT;
BEGIN
    -- This control-plane read uses durable terminal events, not delayed reports.
    -- A legacy projection commits its event and counter deltas together. Exclude
    -- those event IDs because p_committed already contains their charges.
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
      AND NOT EXISTS (
          SELECT 1 FROM deltallm_spendlog_events legacy WHERE legacy.id=e.event_id
      );
    IF entries<>priced_entries OR amount<0 OR amount='NaN'::numeric THEN
        RAISE EXCEPTION 'accounting_budget_policy_history_invalid' USING ERRCODE='P0001';
    END IF;
    RETURN amount;
END;
$$;

CREATE FUNCTION deltallm_accounting_policy_require_resolved(
    p_scope_type TEXT,p_scope_id TEXT
) RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    -- New windows cannot receive later reconciliation of an older reservation.
    -- Resolve uncertain operations before a new window accepts more capacity.
    IF EXISTS (
        SELECT 1 FROM deltallm_billing_operations b
        WHERE b.accounting_protocol='primary' AND b.accounting_state='provisional'
          AND b.provisional_debit_exact>0
          AND CASE p_scope_type
              WHEN 'api_key' THEN b.api_key
              WHEN 'user' THEN b.user_id
              WHEN 'team' THEN b.team_id
              WHEN 'organization' THEN b.organization_id
              WHEN 'team_model' THEN b.team_id||':'||b.model
          END=p_scope_id
    ) OR EXISTS (
        SELECT 1 FROM deltallm_accounting_grants g
        WHERE g.protocol_name='primary' AND g.local_dispatch AND g.state='closed'
          AND g.allocated_exact>g.consumed_exact+g.returned_exact
          AND (g.policy_attribution IS NULL
               OR (p_scope_type='team_model' AND
                   (g.policy_attribution->>'team_id')||':'||(g.policy_attribution->>'model')=p_scope_id)
               OR (p_scope_type<>'team_model' AND g.policy_attribution @>
                   jsonb_build_object(CASE p_scope_type
                       WHEN 'user' THEN 'user_id' WHEN 'team' THEN 'team_id'
                       WHEN 'organization' THEN 'organization_id' ELSE p_scope_type END,p_scope_id)))
    ) THEN
        RAISE EXCEPTION 'accounting_budget_policy_requires_drain' USING ERRCODE='55000';
    END IF;
END;
$$;

DO $migration$
DECLARE
    definition TEXT;
    marker TEXT := '    SELECT COALESCE(max(policy_generation),-1)+1 INTO next_policy_generation';
    history TEXT := $history$
    PERFORM deltallm_accounting_policy_require_resolved(p_scope_type,p_scope_id);
    DECLARE
        period_start TIMESTAMPTZ;
        previous_month TIMESTAMP;
        period_amount INTEGER;
    BEGIN
        IF p_renewal_spec IS NOT NULL THEN
            period_amount:=substring(p_renewal_spec FROM '^[0-9]+')::integer;
            IF p_renewal_spec~'mo$' THEN
                previous_month:=(window_end AT TIME ZONE 'UTC')-make_interval(months=>period_amount);
                period_start:=(date_trunc('month',previous_month)
                    +make_interval(days=>least(anchor_day,extract(day FROM
                        date_trunc('month',previous_month)+interval '1 month'-interval '1 day')::integer)-1)
                    +(previous_month-date_trunc('day',previous_month))) AT TIME ZONE 'UTC';
            ELSE
                period_start:=((window_end AT TIME ZONE 'UTC')-
                    CASE WHEN p_renewal_spec~'h$' THEN make_interval(hours=>period_amount)
                         ELSE make_interval(days=>period_amount) END) AT TIME ZONE 'UTC';
            END IF;
        END IF;
        p_committed:=p_committed+
            deltallm_accounting_policy_native_charge(p_scope_type,p_scope_id,period_start);
        IF p_committed>p_limit THEN
            RAISE EXCEPTION 'accounting_budget_policy_below_debits' USING ERRCODE='P0001';
        END IF;
    END;

$history$;
BEGIN
    SELECT pg_get_functiondef(
        'deltallm_accounting_sync_budget(text,text,numeric,numeric,text,timestamp,jsonb)'::regprocedure
    ) INTO STRICT definition;
    IF (length(definition)-length(replace(definition,marker,'')))<>length(marker) THEN
        RAISE EXCEPTION 'accounting_budget_policy_history_definition';
    END IF;
    EXECUTE replace(definition,marker,history||marker);
END;
$migration$;

COMMIT;
