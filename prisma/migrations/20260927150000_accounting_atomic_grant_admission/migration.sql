-- Keep grant assurance and reservation persistence inside one bounded database
-- acknowledgement. The existing functions remain independently callable for
-- rollback compatibility, while new writers use this atomic composition.
CREATE OR REPLACE FUNCTION deltallm_accounting_admit_grant_batch(
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
    ensured JSONB := '{}'::jsonb;
    persist_items JSONB;
    ensured_row RECORD;
    returned_count INTEGER := 0;
    query_count INTEGER := 0;
BEGIN
    FOR ensured_row IN
        SELECT * FROM deltallm_accounting_ensure_grants_batch(
            p_generation,
            p_grantee_id,
            p_target_operations,
            p_ttl_seconds,
            p_items
        )
    LOOP
        IF ensured ? ensured_row.operation_id THEN
            RAISE EXCEPTION 'accounting_grant_duplicate_result' USING ERRCODE = 'P0001';
        END IF;
        ensured := ensured || jsonb_build_object(
            ensured_row.operation_id,
            ensured_row.decision
        );
    END LOOP;

    IF (SELECT count(*) FROM jsonb_each(ensured)) <> jsonb_array_length(p_items) THEN
        RAISE EXCEPTION 'accounting_grant_incomplete_result' USING ERRCODE = 'P0001';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_each_text(ensured) result
        WHERE result.value NOT IN (
            'ready',
            'replay',
            'budget_exhausted',
            'capacity_exhausted'
        )
    ) THEN
        RAISE EXCEPTION 'accounting_grant_invalid_result' USING ERRCODE = 'P0001';
    END IF;

    SELECT COALESCE(jsonb_agg(item.value ORDER BY item.value->>'operation_id'), '[]'::jsonb)
    INTO persist_items
    FROM jsonb_array_elements(p_items) item
    WHERE ensured->>(item.value->>'operation_id') IN ('ready', 'replay');

    IF jsonb_array_length(persist_items) > 0 THEN
        RETURN QUERY
        SELECT reserved.operation_id,
               reserved.decision,
               reserved.dispatch_token,
               reserved.accounting_partition
        FROM deltallm_accounting_reserve_grant_batch(
            p_generation,
            p_grantee_id,
            p_target_operations,
            p_ttl_seconds,
            persist_items
        ) reserved;
        GET DIAGNOSTICS query_count = ROW_COUNT;
        returned_count := returned_count + query_count;
    END IF;

    RETURN QUERY
    SELECT item.value->>'operation_id',
           ensured->>(item.value->>'operation_id'),
           NULL::text,
           NULL::integer
    FROM jsonb_array_elements(p_items) item
    WHERE ensured->>(item.value->>'operation_id') IN (
        'budget_exhausted',
        'capacity_exhausted'
    )
    ORDER BY item.value->>'operation_id';
    GET DIAGNOSTICS query_count = ROW_COUNT;
    returned_count := returned_count + query_count;

    IF returned_count <> jsonb_array_length(p_items) THEN
        RAISE EXCEPTION 'accounting_grant_incomplete_result' USING ERRCODE = 'P0001';
    END IF;
END;
$$;
