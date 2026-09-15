BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

-- Bounded, coalesced organization alerts; no payload history or recipient PII.
CREATE TABLE deltallm_budgetnotification (
    organization_id TEXT PRIMARY KEY REFERENCES deltallm_organizationtable(organization_id) ON DELETE CASCADE,
    notification_id TEXT NOT NULL UNIQUE,
    spend NUMERIC(38,18) NOT NULL CHECK (spend >= 0 AND spend < 'Infinity'::numeric),
    soft_budget NUMERIC(38,18) NOT NULL CHECK (soft_budget >= 0 AND soft_budget < 'Infinity'::numeric),
    hard_budget NUMERIC(38,18),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','processing','dispatching','completed','failed')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 5),
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    dedupe_until TIMESTAMPTZ NOT NULL,
    claim_token TEXT,
    lease_expires_at TIMESTAMPTZ,
    outcome TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (octet_length(organization_id) <= 256),
    CHECK (hard_budget IS NULL OR (hard_budget >= 0 AND hard_budget < 'Infinity'::numeric))
);
CREATE INDEX deltallm_budgetnotification_due_idx
    ON deltallm_budgetnotification (available_at, organization_id)
    WHERE status IN ('pending','processing','dispatching');

-- One RPC on control capacity. Nonblocking admission sheds optional work under
-- contention; accepted work has one durable row and cannot grow beyond 10,000.
CREATE FUNCTION deltallm_enqueue_budget_notification(
    p_org TEXT, p_id TEXT, p_spend NUMERIC, p_soft NUMERIC, p_hard NUMERIC, p_ttl INTEGER
) RETURNS TEXT LANGUAGE plpgsql AS $$
DECLARE existing deltallm_budgetnotification%ROWTYPE;
BEGIN
    IF p_ttl < 60 OR p_ttl > 2147483647 THEN
        RAISE EXCEPTION 'invalid notification dedupe duration';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM deltallm_organizationtable
                   WHERE organization_id = p_org AND lifecycle_state = 'active') THEN
        RETURN 'inactive';
    END IF;
    SELECT * INTO existing FROM deltallm_budgetnotification WHERE organization_id = p_org;
    IF FOUND AND (existing.status IN ('pending','processing','dispatching') OR existing.dedupe_until > NOW()) THEN
        RETURN 'throttled';
    END IF;
    IF NOT pg_try_advisory_xact_lock(hashtextextended('deltallm:budget-notification-capacity:v1', 0)) THEN
        RETURN 'busy';
    END IF;
    -- This statement runs after lock acquisition with a fresh READ COMMITTED snapshot.
    SELECT * INTO existing FROM deltallm_budgetnotification WHERE organization_id = p_org FOR UPDATE;
    IF FOUND AND (existing.status IN ('pending','processing','dispatching') OR existing.dedupe_until > NOW()) THEN
        RETURN 'throttled';
    END IF;
    IF NOT FOUND AND (SELECT count(*) FROM (SELECT 1 FROM deltallm_budgetnotification LIMIT 10000) bounded) >= 10000 THEN
        RETURN 'full';
    END IF;
    INSERT INTO deltallm_budgetnotification
        (organization_id, notification_id, spend, soft_budget, hard_budget, dedupe_until)
    VALUES (p_org, p_id, p_spend, p_soft, p_hard, NOW() + make_interval(secs => p_ttl))
    ON CONFLICT (organization_id) DO UPDATE SET
        notification_id = EXCLUDED.notification_id, spend = EXCLUDED.spend,
        soft_budget = EXCLUDED.soft_budget, hard_budget = EXCLUDED.hard_budget,
        status = 'pending', attempt_count = 0, available_at = NOW(),
        dedupe_until = EXCLUDED.dedupe_until, claim_token = NULL,
        lease_expires_at = NULL, outcome = NULL, updated_at = NOW();
    RETURN 'queued';
END;
$$;
COMMIT;
