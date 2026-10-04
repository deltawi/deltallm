-- A later local issue must fit its terminal lifetime inside the funded lease.
-- Retain the first lifetime plus the short dispatch horizon. Allocation replay
-- never extends either stored deadline.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

CREATE OR REPLACE FUNCTION deltallm_accounting_allocate_local_permit_grant(
    p_generation BIGINT,
    p_grantee_id TEXT,
    p_fence_token UUID,
    p_target_operations INTEGER,
    p_ttl_seconds INTEGER,
    p_item JSONB
) RETURNS TABLE(
    decision TEXT,
    grant_id TEXT,
    grantee_id TEXT,
    fence_token UUID,
    accounting_partition INTEGER,
    allowance_exact NUMERIC,
    operation_limit INTEGER,
    expires_at TIMESTAMPTZ,
    dispatch_expires_at TIMESTAMPTZ
)
LANGUAGE plpgsql AS $$
DECLARE
    allocated RECORD;
    lease_expires_at TIMESTAMPTZ;
BEGIN
    lease_expires_at:=(p_item->>'expires_at')::timestamptz;
    IF lease_expires_at IS NULL OR lease_expires_at<=CURRENT_TIMESTAMP
       OR lease_expires_at>CURRENT_TIMESTAMP+interval '15 minutes' THEN
        RAISE EXCEPTION 'accounting_local_permit_lease_expiry' USING ERRCODE = 'P0001';
    END IF;

    SELECT * INTO STRICT allocated
    FROM deltallm_accounting_allocate_permit_grant(
        p_generation,p_grantee_id,p_fence_token,p_target_operations,p_ttl_seconds,p_item
    );
    IF allocated.decision<>'dispatch' THEN
        RETURN QUERY SELECT allocated.decision,NULL::text,NULL::text,NULL::uuid,
                            NULL::integer,NULL::numeric,NULL::integer,NULL::timestamptz,
                            NULL::timestamptz;
        RETURN;
    END IF;

    RETURN QUERY
    UPDATE deltallm_accounting_grants g SET
        local_dispatch=TRUE,
        dispatch_expires_at=COALESCE(g.dispatch_expires_at,g.expires_at),
        expires_at=CASE WHEN g.local_dispatch THEN g.expires_at
            ELSE lease_expires_at+greatest(
                g.expires_at-CURRENT_TIMESTAMP,interval '0 seconds'
            ) END,
        updated_at=NOW()
    WHERE g.grant_id=allocated.grant_id
      AND g.grantee_id=p_grantee_id
      AND g.fence_token=p_fence_token
      AND g.dispatch_mode='preissued'
      AND g.state='active'
      AND g.consumed_operations=0
      AND g.returned_operations=0
    RETURNING 'dispatch'::text,g.grant_id,g.grantee_id,g.fence_token,
              g.accounting_partition,g.unit_allowance_exact,g.operation_limit,g.expires_at,
              g.dispatch_expires_at;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'accounting_local_permit_allocation_lost' USING ERRCODE = 'P0001';
    END IF;
END;
$$;

COMMIT;
