-- Audit an operator's release of a closed grant's unbilled remainder.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

CREATE TABLE deltallm_accounting_grant_reconciliations (
    grant_id TEXT PRIMARY KEY REFERENCES deltallm_accounting_grants(grant_id) ON DELETE RESTRICT,
    generation BIGINT NOT NULL CHECK (generation>0),
    released_exact NUMERIC(38,18) NOT NULL CHECK (released_exact>0),
    evidence_reason TEXT NOT NULL CHECK (length(evidence_reason) BETWEEN 1 AND 256),
    reconciled_by TEXT NOT NULL,
    reconciled_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE FUNCTION deltallm_accounting_release_grant_hold(
    p_generation BIGINT, p_grant_id TEXT, p_expected NUMERIC, p_evidence_reason TEXT
) RETURNS NUMERIC LANGUAGE plpgsql AS $$
DECLARE
    grant_row deltallm_accounting_grants%ROWTYPE;
    prior deltallm_accounting_grant_reconciliations%ROWTYPE;
    window_ids TEXT[];
    changed INTEGER;
BEGIN
    IF p_generation IS NULL OR p_generation<1
       OR COALESCE(length(p_grant_id),0) NOT BETWEEN 1 AND 256
       OR COALESCE(length(btrim(p_evidence_reason)),0) NOT BETWEEN 1 AND 256
       OR p_evidence_reason IS DISTINCT FROM btrim(p_evidence_reason)
       OR p_expected IS NULL OR p_expected<=0 OR p_expected>=100000000000000000000
       OR scale(p_expected)>18 THEN
        RAISE EXCEPTION 'accounting_grant_reconciliation_shape' USING ERRCODE='P0001';
    END IF;
    SELECT * INTO grant_row FROM deltallm_accounting_grants
        WHERE grant_id=p_grant_id AND protocol_name='primary' AND generation=p_generation
        FOR UPDATE;
    IF NOT FOUND OR grant_row.state<>'closed' OR NOT grant_row.local_dispatch THEN
        RAISE EXCEPTION 'accounting_grant_reconciliation_state' USING ERRCODE='P0001';
    END IF;
    SELECT * INTO prior FROM deltallm_accounting_grant_reconciliations WHERE grant_id=p_grant_id;
    IF FOUND THEN
        IF prior.generation<>p_generation OR prior.released_exact<>p_expected
           OR prior.evidence_reason<>p_evidence_reason OR grant_row.unknown_provisional_exact<>0 THEN
            RAISE EXCEPTION 'accounting_grant_reconciliation_conflict' USING ERRCODE='P0001';
        END IF;
        RETURN prior.released_exact;
    END IF;
    IF grant_row.unknown_provisional_exact<>p_expected
       OR grant_row.settled_exact<>grant_row.consumed_exact+p_expected THEN
        RAISE EXCEPTION 'accounting_grant_reconciliation_amount' USING ERRCODE='P0001';
    END IF;
    window_ids:=ARRAY(
        SELECT gw.window_id FROM deltallm_accounting_grant_windows gw
        WHERE gw.grant_id=p_grant_id ORDER BY gw.window_id LIMIT 9
    );
    IF cardinality(window_ids)>8 THEN
        RAISE EXCEPTION 'accounting_grant_reconciliation_windows' USING ERRCODE='P0001';
    END IF;
    -- Settlement locks the grant first, then the same windows in key order.
    PERFORM 1 FROM deltallm_accounting_budget_windows w
        WHERE w.window_id=ANY(window_ids) ORDER BY w.window_id FOR UPDATE;
    UPDATE deltallm_accounting_budget_windows w
        SET provisional_exact=w.provisional_exact-p_expected,updated_at=CURRENT_TIMESTAMP
        WHERE w.window_id=ANY(window_ids) AND w.generation=p_generation
          AND w.protocol_name='primary' AND w.provisional_exact>=p_expected;
    GET DIAGNOSTICS changed=ROW_COUNT;
    IF changed<>cardinality(window_ids) THEN
        RAISE EXCEPTION 'accounting_grant_reconciliation_window_drift' USING ERRCODE='P0001';
    END IF;
    UPDATE deltallm_accounting_grants
        SET unknown_provisional_exact=0,settled_exact=consumed_exact,updated_at=CURRENT_TIMESTAMP
        WHERE grant_id=p_grant_id;
    INSERT INTO deltallm_accounting_grant_reconciliations(
        grant_id,generation,released_exact,evidence_reason,reconciled_by
    ) VALUES (p_grant_id,p_generation,p_expected,p_evidence_reason,CURRENT_USER);
    RETURN p_expected;
END;
$$;

REVOKE EXECUTE ON FUNCTION deltallm_accounting_release_grant_hold(BIGINT,TEXT,NUMERIC,TEXT) FROM PUBLIC;

COMMIT;
