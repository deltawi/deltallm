-- Additive dormant billing prerequisites. All mutations use short primary transactions.
BEGIN;
SET lock_timeout = '2s';
SET statement_timeout = '30s';

ALTER TABLE deltallm_verificationtoken ADD COLUMN reserved_spend_exact NUMERIC(38,18) NOT NULL DEFAULT 0
    CHECK (reserved_spend_exact >= 0);
ALTER TABLE deltallm_usertable ADD COLUMN reserved_spend_exact NUMERIC(38,18) NOT NULL DEFAULT 0
    CHECK (reserved_spend_exact >= 0);
ALTER TABLE deltallm_teamtable ADD COLUMN reserved_spend_exact NUMERIC(38,18) NOT NULL DEFAULT 0
    CHECK (reserved_spend_exact >= 0);
ALTER TABLE deltallm_organizationtable ADD COLUMN reserved_spend_exact NUMERIC(38,18) NOT NULL DEFAULT 0
    CHECK (reserved_spend_exact >= 0);
ALTER TABLE deltallm_teammodelspend ADD COLUMN reserved_spend_exact NUMERIC(38,18) NOT NULL DEFAULT 0
    CHECK (reserved_spend_exact >= 0);

CREATE TABLE deltallm_billing_operations (
    operation_id TEXT PRIMARY KEY,
    owner_token TEXT NOT NULL,
    api_key TEXT NOT NULL,
    user_id TEXT,
    team_id TEXT,
    organization_id TEXT,
    model TEXT NOT NULL,
    snapshot JSONB NOT NULL,
    selector_event_id TEXT NOT NULL UNIQUE,
    selector_allowance NUMERIC(38,18) NOT NULL CHECK (selector_allowance >= 0),
    answer_allowance NUMERIC(38,18) NOT NULL CHECK (answer_allowance >= 0),
    selector_state TEXT NOT NULL DEFAULT 'reserved',
    answer_state TEXT NOT NULL DEFAULT 'reserved',
    selector_receipt JSONB,
    answer_receipt JSONB,
    closed_at TIMESTAMPTZ,
    recovery_blocked_at TIMESTAMPTZ,
    recovery_error_code TEXT CHECK (recovery_error_code IN ('receipt_conflict','integrity_failure')),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (selector_state IN ('reserved','dispatched','pending','accepted','settled','unattempted')),
    CHECK (answer_state IN ('reserved','dispatched','pending','accepted','settled','unattempted')),
    CHECK ((selector_state IN ('accepted','settled')) = (selector_receipt IS NOT NULL)),
    CHECK ((answer_state IN ('accepted','settled')) = (answer_receipt IS NOT NULL)),
    CHECK ((recovery_blocked_at IS NULL) = (recovery_error_code IS NULL)),
    CHECK (expires_at > created_at AND expires_at <= created_at + interval '15 minutes')
);
-- Round-robin bounded open-row recovery also finds late receipts after process loss.
CREATE INDEX deltallm_billing_operations_recovery_idx ON deltallm_billing_operations(updated_at, operation_id)
    WHERE closed_at IS NULL AND recovery_blocked_at IS NULL;
CREATE INDEX deltallm_billing_operations_receipt_idx ON deltallm_billing_operations(updated_at, operation_id)
    WHERE selector_state='accepted' AND recovery_blocked_at IS NULL;
CREATE INDEX deltallm_billing_operations_org_time_idx ON deltallm_billing_operations(organization_id, created_at, operation_id);
CREATE INDEX deltallm_billing_operations_time_idx ON deltallm_billing_operations(created_at, operation_id);
CREATE INDEX deltallm_billing_operations_team_time_idx ON deltallm_billing_operations(team_id, created_at, operation_id);
CREATE INDEX deltallm_billing_operations_owner_time_idx ON deltallm_billing_operations(
    (snapshot #>> '{attribution,owner_account_id}'), created_at, operation_id);
CREATE INDEX deltallm_billing_operations_terminal_idx ON deltallm_billing_operations(closed_at, operation_id)
    WHERE closed_at IS NOT NULL;
INSERT INTO deltallm_telemetry_ingestion_capacity(queue_name,pending_count)
    VALUES ('billing_operations',0) ON CONFLICT DO NOTHING;

-- Revocation can still block/expire credentials. Economic holds cannot be orphaned
-- by physical entity deletion; settle/reconcile the existing operation first.
CREATE FUNCTION deltallm_guard_reserved_spend_delete() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.reserved_spend_exact <> 0 THEN
        RAISE EXCEPTION 'billing_operation_hold_requires_reconciliation';
    END IF;
    RETURN OLD;
END;
$$;
CREATE TRIGGER deltallm_key_hold_delete_guard BEFORE DELETE ON deltallm_verificationtoken
    FOR EACH ROW EXECUTE FUNCTION deltallm_guard_reserved_spend_delete();
CREATE TRIGGER deltallm_user_hold_delete_guard BEFORE DELETE ON deltallm_usertable
    FOR EACH ROW EXECUTE FUNCTION deltallm_guard_reserved_spend_delete();
CREATE TRIGGER deltallm_team_hold_delete_guard BEFORE DELETE ON deltallm_teamtable
    FOR EACH ROW EXECUTE FUNCTION deltallm_guard_reserved_spend_delete();
CREATE TRIGGER deltallm_org_hold_delete_guard BEFORE DELETE ON deltallm_organizationtable
    FOR EACH ROW EXECUTE FUNCTION deltallm_guard_reserved_spend_delete();
CREATE TRIGGER deltallm_model_hold_delete_guard BEFORE DELETE ON deltallm_teammodelspend
    FOR EACH ROW EXECUTE FUNCTION deltallm_guard_reserved_spend_delete();

-- Caller owns operation first, then ledger order: key, user, team, organization,
-- team/model. Global operation capacity is always acquired AFTER these account rows.
-- A missing authoritative counter is unavailable, never a history scan or implicit zero.
CREATE FUNCTION deltallm_adjust_operation_hold(p_id TEXT, p_delta NUMERIC) RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE
    op deltallm_billing_operations%ROWTYPE;
    row_key deltallm_verificationtoken%ROWTYPE;
    row_user deltallm_usertable%ROWTYPE;
    row_team deltallm_teamtable%ROWTYPE;
    row_org deltallm_organizationtable%ROWTYPE;
    row_model deltallm_teammodelspend%ROWTYPE;
    model_limit NUMERIC;
BEGIN
    SELECT * INTO STRICT op FROM deltallm_billing_operations WHERE operation_id = p_id;
    SELECT * INTO STRICT row_key FROM deltallm_verificationtoken WHERE token = op.api_key FOR UPDATE;
    IF op.user_id IS NOT NULL THEN
        SELECT * INTO STRICT row_user FROM deltallm_usertable WHERE user_id = op.user_id FOR UPDATE;
    END IF;
    IF op.team_id IS NOT NULL THEN
        SELECT * INTO STRICT row_team FROM deltallm_teamtable WHERE team_id = op.team_id FOR UPDATE;
    END IF;
    IF op.organization_id IS NOT NULL THEN
        SELECT * INTO STRICT row_org FROM deltallm_organizationtable WHERE organization_id = op.organization_id FOR UPDATE;
    END IF;
    IF op.team_id IS NOT NULL THEN
        SELECT * INTO STRICT row_model FROM deltallm_teammodelspend
            WHERE team_id = op.team_id AND model = op.model FOR UPDATE;
        model_limit := (row_team.model_max_budget ->> op.model)::NUMERIC;
    END IF;
    IF p_delta >= 0 THEN
        IF row_key.user_id IS DISTINCT FROM op.user_id OR row_key.team_id IS DISTINCT FROM op.team_id
           OR row_key.owner_account_id IS DISTINCT FROM (op.snapshot #>> '{attribution,owner_account_id}')
           OR row_team.organization_id IS DISTINCT FROM op.organization_id
           OR row_user.blocked IS TRUE OR row_team.blocked IS TRUE
           OR (op.organization_id IS NOT NULL AND row_org.lifecycle_state <> 'active')
           OR row_key.expires <= CURRENT_TIMESTAMP THEN
            RAISE EXCEPTION 'billing_operation_scope_unavailable' USING ERRCODE = 'P0001';
        END IF;
        IF COALESCE(row_key.spend_exact,row_key.spend::NUMERIC,0) + row_key.reserved_spend_exact + p_delta > row_key.max_budget::NUMERIC
           OR COALESCE(row_user.spend_exact,row_user.spend::NUMERIC,0) + row_user.reserved_spend_exact + p_delta > row_user.max_budget::NUMERIC
           OR COALESCE(row_team.spend_exact,row_team.spend::NUMERIC,0) + row_team.reserved_spend_exact + p_delta > row_team.max_budget::NUMERIC
           OR COALESCE(row_org.spend_exact,row_org.spend::NUMERIC,0) + row_org.reserved_spend_exact + p_delta > row_org.max_budget::NUMERIC
           OR COALESCE(row_model.spend_exact,row_model.spend::NUMERIC,0) + row_model.reserved_spend_exact + p_delta > model_limit THEN
            RAISE EXCEPTION 'billing_operation_budget_unavailable' USING ERRCODE = 'P0001';
        END IF;
    END IF;
    UPDATE deltallm_verificationtoken SET reserved_spend_exact = reserved_spend_exact + p_delta WHERE token = op.api_key;
    UPDATE deltallm_usertable SET reserved_spend_exact = reserved_spend_exact + p_delta WHERE user_id = op.user_id;
    UPDATE deltallm_teamtable SET reserved_spend_exact = reserved_spend_exact + p_delta WHERE team_id = op.team_id;
    UPDATE deltallm_organizationtable SET reserved_spend_exact = reserved_spend_exact + p_delta WHERE organization_id = op.organization_id;
    UPDATE deltallm_teammodelspend SET reserved_spend_exact = reserved_spend_exact + p_delta
        WHERE team_id = op.team_id AND model = op.model;
END;
$$;

-- Called only by the bounded existing spend worker/recovery owner. Never retries providers.
CREATE FUNCTION deltallm_recover_operation(p_id TEXT) RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE
    op deltallm_billing_operations%ROWTYPE;
    selector_event deltallm_spendlog_events%ROWTYPE;
    answer_event deltallm_spendlog_events%ROWTYPE;
    released NUMERIC := 0;
BEGIN
    SELECT * INTO STRICT op FROM deltallm_billing_operations WHERE operation_id=p_id FOR UPDATE;
    IF op.closed_at IS NOT NULL THEN RETURN; END IF;
    SELECT * INTO selector_event FROM deltallm_spendlog_events WHERE id=op.selector_event_id;
    SELECT * INTO answer_event FROM deltallm_spendlog_events WHERE id=op.operation_id;
    IF selector_event.id IS NOT NULL AND op.selector_state='accepted' THEN
        IF selector_event.api_key IS DISTINCT FROM op.api_key
           OR selector_event.organization_id IS DISTINCT FROM op.organization_id
           OR selector_event.user_id IS DISTINCT FROM op.user_id
           OR selector_event.team_id IS DISTINCT FROM op.team_id
           OR selector_event.owner_account_id IS DISTINCT FROM (op.snapshot #>> '{attribution,owner_account_id}')
           OR selector_event.model IS DISTINCT FROM op.model
           OR selector_event.call_type <> 'model_router_selector'
           OR selector_event.provider_cost_exact IS DISTINCT FROM (op.selector_receipt->>'provider_cost_exact')::numeric
           OR selector_event.spend_exact IS DISTINCT FROM (op.selector_receipt->>'cost_exact')::numeric THEN
            RAISE EXCEPTION 'billing_component_receipt_conflict' USING ERRCODE = 'PBR01';
        END IF;
        op.selector_state := 'settled';
        released := released + op.selector_allowance;
    END IF;
    -- Legacy failure rows/defaulted token counts are not proof of a free attempt.
    -- PR 4 must propagate a server-owned reported receipt through canonical billing.
    IF answer_event.id IS NOT NULL AND op.answer_state IN ('dispatched','pending')
       AND answer_event.status='success'
       AND answer_event.usage_snapshot->>'kind'='reported' THEN
        IF answer_event.api_key IS DISTINCT FROM op.api_key
           OR answer_event.organization_id IS DISTINCT FROM op.organization_id
           OR answer_event.user_id IS DISTINCT FROM op.user_id
           OR answer_event.team_id IS DISTINCT FROM op.team_id
           OR answer_event.owner_account_id IS DISTINCT FROM (op.snapshot #>> '{attribution,owner_account_id}')
           OR answer_event.model IS DISTINCT FROM op.model
           OR answer_event.spend_exact IS NULL OR answer_event.spend_exact > op.answer_allowance THEN
            RAISE EXCEPTION 'billing_component_receipt_conflict' USING ERRCODE = 'PBR01';
        END IF;
        op.answer_state := 'settled';
        op.answer_receipt := jsonb_build_object('cost_exact',answer_event.spend_exact::text,'event_id',answer_event.id);
        released := released + op.answer_allowance;
    END IF;
    IF op.expires_at <= NOW() THEN
        IF op.selector_state='reserved' THEN
            op.selector_state := 'unattempted'; released := released + op.selector_allowance;
        ELSIF op.selector_state='dispatched' THEN op.selector_state := 'pending'; END IF;
        IF op.answer_state='reserved' THEN
            op.answer_state := 'unattempted'; released := released + op.answer_allowance;
        ELSIF op.answer_state='dispatched' THEN op.answer_state := 'pending'; END IF;
    END IF;
    IF released > 0 THEN PERFORM deltallm_adjust_operation_hold(p_id,-released); END IF;
    IF op.selector_state IN ('settled','unattempted') AND op.answer_state IN ('settled','unattempted') THEN
        op.closed_at := NOW();
        UPDATE deltallm_telemetry_ingestion_capacity SET pending_count=pending_count-1
            WHERE queue_name='billing_operations';
    END IF;
    UPDATE deltallm_billing_operations SET selector_state=op.selector_state,answer_state=op.answer_state,
        answer_receipt=op.answer_receipt,closed_at=op.closed_at,updated_at=NOW() WHERE operation_id=p_id;
END;
$$;

-- Background-only isolation. The spend writer uses the strict function above so
-- a conflicting event/ledger transaction still rolls back. The exception block is
-- a subtransaction: no partial hold release can survive a quarantined operation.
CREATE FUNCTION deltallm_recover_operation_isolated(p_id TEXT) RETURNS TEXT LANGUAGE plpgsql AS $$
DECLARE
    failure_code TEXT;
BEGIN
    PERFORM 1 FROM deltallm_billing_operations WHERE operation_id=p_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'billing_operation_missing' USING ERRCODE = 'P0002'; END IF;
    BEGIN
        PERFORM deltallm_recover_operation(p_id);
        RETURN 'recovered';
    EXCEPTION
        WHEN SQLSTATE 'PBR01' THEN failure_code := 'receipt_conflict';
        WHEN integrity_constraint_violation OR data_exception OR no_data_found THEN
            failure_code := 'integrity_failure';
    END;
    -- Infrastructure failures, deadlocks and cancellation are deliberately NOT
    -- caught. Only a committed row-specific quarantine leaves the recovery indexes.
    UPDATE deltallm_billing_operations SET recovery_blocked_at=NOW(),
        recovery_error_code=failure_code,updated_at=NOW() WHERE operation_id=p_id;
    RETURN failure_code;
END;
$$;

RESET lock_timeout;
RESET statement_timeout;
COMMIT;
