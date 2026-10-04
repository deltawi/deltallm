-- Missing generation, fence, or suffix identity must never authorize a return.
-- Existing applied migrations remain unchanged. Valid owned returns keep their behavior.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

CREATE OR REPLACE FUNCTION deltallm_accounting_return_local_permits(
    p_generation BIGINT,
    p_grant_id TEXT,
    p_fence_token UUID,
    p_first_unused_ordinal INTEGER
) RETURNS TABLE(returned_operations INTEGER)
LANGUAGE plpgsql AS $$
DECLARE
    grant_row deltallm_accounting_grants%ROWTYPE;
    returned_count INTEGER;
BEGIN
    SELECT * INTO STRICT grant_row FROM deltallm_accounting_grants g
    WHERE g.grant_id=p_grant_id FOR UPDATE;
    IF grant_row.protocol_name IS DISTINCT FROM 'primary'
       OR grant_row.generation IS DISTINCT FROM p_generation
       OR NOT grant_row.local_dispatch
       OR grant_row.fence_token IS DISTINCT FROM p_fence_token
       OR grant_row.state NOT IN ('active','draining')
       OR p_first_unused_ordinal IS NULL
       OR p_first_unused_ordinal NOT BETWEEN 0 AND grant_row.operation_limit THEN
        RAISE EXCEPTION 'accounting_local_permit_return_identity' USING ERRCODE = 'P0001';
    END IF;
    returned_count:=grant_row.operation_limit-p_first_unused_ordinal;
    IF EXISTS (
        SELECT 1 FROM deltallm_billing_operations b
        WHERE b.accounting_grant_id=p_grant_id
          AND b.accounting_permit_ordinal>=p_first_unused_ordinal
    ) THEN
        RAISE EXCEPTION 'accounting_local_permit_return_claimed' USING ERRCODE = 'P0001';
    END IF;
    IF grant_row.returned_operations NOT IN (0,returned_count) THEN
        RAISE EXCEPTION 'accounting_local_permit_return_conflict' USING ERRCODE = 'P0001';
    END IF;
    UPDATE deltallm_accounting_grants g SET
        returned_operations=returned_count,
        returned_exact=grant_row.unit_allowance_exact*returned_count,
        state=CASE
            WHEN consumed_operations+returned_count=operation_limit THEN 'draining'
            ELSE state
        END,
        updated_at=NOW()
    WHERE g.grant_id=p_grant_id;
    returned_operations:=returned_count;
    RETURN NEXT;
END;
$$;

CREATE OR REPLACE FUNCTION deltallm_accounting_return_local_permits_batch(
    p_generation BIGINT,p_owner_id TEXT,p_items JSONB
) RETURNS TABLE(
    grant_id TEXT,fence_token UUID,first_unused_ordinal INTEGER,returned_operations INTEGER
)
LANGUAGE plpgsql AS $$
DECLARE
    item JSONB;
    grant_row deltallm_accounting_grants%ROWTYPE;
    returned_count INTEGER;
BEGIN
    IF COALESCE(p_generation,0)<1
       OR COALESCE(length(p_owner_id),0) NOT BETWEEN 1 AND 219
       OR COALESCE(jsonb_typeof(p_items),'null')<>'array' THEN
        RAISE EXCEPTION 'accounting_local_return_batch_shape' USING ERRCODE='P0001';
    END IF;
    IF jsonb_array_length(p_items) NOT BETWEEN 1 AND 256
       OR octet_length(p_items::text)>2097152 THEN
        RAISE EXCEPTION 'accounting_local_return_batch_shape' USING ERRCODE='P0001';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_items) value
        WHERE COALESCE(length(value->>'grant_id'),0) NOT BETWEEN 1 AND 256
          OR value->>'fence_token' IS NULL
          OR COALESCE((value->>'first_unused_ordinal')::integer,-1) NOT BETWEEN 0 AND 1024
    ) OR EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_items) value
        GROUP BY value->>'grant_id' HAVING count(*)>1
    ) THEN
        RAISE EXCEPTION 'accounting_local_return_item_shape' USING ERRCODE='P0001';
    END IF;

    PERFORM 1 FROM deltallm_accounting_grants g
        WHERE g.grant_id=ANY(ARRAY(
            SELECT value->>'grant_id' FROM jsonb_array_elements(p_items) value
        )) ORDER BY g.grant_id FOR UPDATE;
    FOR item IN SELECT value FROM jsonb_array_elements(p_items) value
                ORDER BY value->>'grant_id'
    LOOP
        SELECT * INTO STRICT grant_row FROM deltallm_accounting_grants g
            WHERE g.grant_id=item->>'grant_id';
        returned_count:=grant_row.operation_limit-(item->>'first_unused_ordinal')::integer;
        IF grant_row.protocol_name IS DISTINCT FROM 'primary'
           OR grant_row.generation IS DISTINCT FROM p_generation
           OR NOT grant_row.local_dispatch
           OR grant_row.fence_token IS DISTINCT FROM (item->>'fence_token')::uuid
           OR grant_row.grantee_id<>p_owner_id||':'||grant_row.fence_token::text
           OR returned_count NOT BETWEEN 0 AND grant_row.operation_limit THEN
            RAISE EXCEPTION 'accounting_local_return_identity' USING ERRCODE='P0001';
        END IF;
        IF grant_row.state='closed' THEN
            IF grant_row.returned_operations<>returned_count
               OR grant_row.returned_exact<>grant_row.unit_allowance_exact*returned_count THEN
                RAISE EXCEPTION 'accounting_local_return_conflict' USING ERRCODE='P0001';
            END IF;
        ELSE
            PERFORM * FROM deltallm_accounting_return_local_permits(
                p_generation,grant_row.grant_id,grant_row.fence_token,
                (item->>'first_unused_ordinal')::integer
            );
        END IF;
        RETURN QUERY SELECT grant_row.grant_id,grant_row.fence_token,
                            (item->>'first_unused_ordinal')::integer,returned_count;
    END LOOP;
END;
$$;

COMMIT;
