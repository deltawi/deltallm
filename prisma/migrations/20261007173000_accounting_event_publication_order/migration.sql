-- Publish terminal event keys in commit order within each accounting partition.
-- Financial locks precede these locks. Acquire each batch's keys in sorted order.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

CREATE FUNCTION deltallm_accounting_lock_event_publication(
    p_generation BIGINT,p_partitions INTEGER[]
) RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE partition_value INTEGER;
BEGIN
    IF p_generation IS NULL OR p_generation<1 OR p_partitions IS NULL
       OR cardinality(p_partitions)>256
       OR EXISTS (SELECT 1 FROM unnest(p_partitions) n WHERE n IS NULL OR n NOT BETWEEN 0 AND 63) THEN
        RAISE EXCEPTION 'accounting_event_publication_shape' USING ERRCODE='P0001';
    END IF;
    FOR partition_value IN SELECT DISTINCT n FROM unnest(p_partitions) n ORDER BY n LOOP
        PERFORM pg_advisory_xact_lock(hashtextextended(
            'deltallm:accounting-event-publication:'||p_generation||':'||partition_value,0
        ));
    END LOOP;
END;
$$;

-- Patch the existing bounded writers before their first terminal sequence is
-- allocated. A BEFORE INSERT trigger is too late: column defaults run first.
DO $migration$
DECLARE
    target RECORD;
    definition TEXT;
    batch_marker TEXT := $marker$    FOR item IN SELECT value FROM jsonb_array_elements(p_items) value
                ORDER BY value->>'operation_id'
    LOOP$marker$;
    batch_fence TEXT := $fence$    PERFORM deltallm_accounting_lock_event_publication(p_generation,ARRAY(
        SELECT b.accounting_partition FROM jsonb_array_elements(p_items) requested
        CROSS JOIN LATERAL (
            SELECT b.accounting_partition FROM deltallm_billing_operations b
            WHERE b.operation_id=requested->>'operation_id' OFFSET 0
        ) b
    ));
$fence$;
    journal_marker TEXT := '    WITH raw AS MATERIALIZED (';
    journal_fence TEXT := $fence$    PERFORM deltallm_accounting_lock_event_publication(p_generation,ARRAY(
        SELECT (v->>'partition')::integer FROM unnest(prepared) v
    ));
$fence$;
    resolve_marker TEXT := '    resolution_outcome := CASE WHEN p_additional_charge>0';
    resolve_fence TEXT := $fence$    PERFORM deltallm_accounting_lock_event_publication(
        p_generation,ARRAY[op.accounting_partition]
    );
$fence$;
BEGIN
    FOR target IN SELECT * FROM (VALUES
        ('deltallm_accounting_finalize_batch(bigint,jsonb)',batch_marker,batch_fence),
        ('deltallm_accounting_finalize_grant_batch(bigint,jsonb)',batch_marker,batch_fence),
        ('deltallm_accounting_materialize_terminal_journal(bigint,text,uuid,bigint[])',journal_marker,journal_fence),
        ('deltallm_accounting_resolve_provisional_without_grant_guard(bigint,text,numeric,jsonb,text)',resolve_marker,resolve_fence)
    ) change(signature,marker,fence)
    LOOP
        SELECT pg_get_functiondef(target.signature::regprocedure) INTO STRICT definition;
        IF length(definition)-length(replace(definition,target.marker,''))<>length(target.marker) THEN
            RAISE EXCEPTION 'accounting_event_publication_definition';
        END IF;
        EXECUTE replace(definition,target.marker,target.fence||target.marker);
    END LOOP;
END;
$migration$;

-- Recovery locks its complete bounded page before it takes publication locks.
-- Do not take a new financial lock after a publication lock on another row.
DO $migration$
DECLARE
    definition TEXT;
    declaration TEXT := '    op deltallm_billing_operations%ROWTYPE;';
    old_selection TEXT := $old$    FOR op IN
        SELECT b.* FROM deltallm_billing_operations b
        WHERE b.accounting_protocol='primary'
          AND b.accounting_generation=p_generation
          AND b.accounting_state='reserved'
          AND b.expires_at<=CURRENT_TIMESTAMP
        ORDER BY b.operation_id
        FOR UPDATE SKIP LOCKED LIMIT p_limit
    LOOP$old$;
    new_selection TEXT := $new$    SELECT array_agg(pick.operation_id ORDER BY pick.operation_id) INTO candidate_ids FROM (
        SELECT b.operation_id FROM deltallm_billing_operations b
        WHERE b.accounting_protocol='primary' AND b.accounting_generation=p_generation
          AND b.accounting_state='reserved' AND b.accounting_grant_id IS NULL
          AND b.expires_at<=CURRENT_TIMESTAMP
        ORDER BY b.operation_id FOR UPDATE SKIP LOCKED LIMIT p_limit
    ) pick;
    IF candidate_ids IS NULL THEN RETURN 0; END IF;
    PERFORM 1 FROM deltallm_accounting_budget_windows w
    WHERE w.window_id=ANY(ARRAY(
        SELECT r.window_id FROM unnest(candidate_ids) key(operation_id)
        CROSS JOIN LATERAL (
            SELECT r.window_id FROM deltallm_accounting_reservations r
            WHERE r.operation_id=key.operation_id OFFSET 0
        ) r
    )) ORDER BY w.window_id FOR UPDATE;
    PERFORM 1 FROM deltallm_accounting_partitions p
    WHERE p.protocol_name='primary' AND p.generation=p_generation
      AND p.partition_id=ANY(ARRAY(
          SELECT b.accounting_partition FROM deltallm_billing_operations b
          WHERE b.operation_id=ANY(candidate_ids)
      )) ORDER BY p.partition_id FOR UPDATE;
    PERFORM deltallm_accounting_lock_event_publication(p_generation,ARRAY(
        SELECT b.accounting_partition FROM deltallm_billing_operations b
        WHERE b.operation_id=ANY(candidate_ids)
    ));
    FOR op IN SELECT b.* FROM deltallm_billing_operations b
        WHERE b.operation_id=ANY(candidate_ids) ORDER BY b.operation_id
    LOOP$new$;
BEGIN
    SELECT pg_get_functiondef('deltallm_accounting_reconcile_expired(bigint,integer)'::regprocedure)
        INTO STRICT definition;
    IF length(definition)-length(replace(definition,declaration,''))<>length(declaration)
       OR length(definition)-length(replace(definition,old_selection,''))<>length(old_selection) THEN
        RAISE EXCEPTION 'accounting_event_expiry_definition';
    END IF;
    definition:=replace(definition,declaration,declaration||E'\n    candidate_ids TEXT[];');
    EXECUTE replace(definition,old_selection,new_selection);
END;
$migration$;

DO $migration$
DECLARE
    definition TEXT;
    declaration TEXT := '    op deltallm_billing_operations%ROWTYPE;';
    old_selection TEXT := $old$    FOR op IN
        SELECT b.* FROM deltallm_billing_operations b
        WHERE b.accounting_protocol='primary' AND b.accounting_generation=p_generation
          AND b.accounting_state='reserved' AND b.accounting_grant_id IS NOT NULL
          AND b.expires_at<=CURRENT_TIMESTAMP
        ORDER BY b.operation_id FOR UPDATE SKIP LOCKED LIMIT p_limit
    LOOP$old$;
    new_selection TEXT := $new$    SELECT array_agg(pick.operation_id ORDER BY pick.operation_id) INTO candidate_ids FROM (
        SELECT b.operation_id FROM deltallm_billing_operations b
        WHERE b.accounting_protocol='primary' AND b.accounting_generation=p_generation
          AND b.accounting_state='reserved' AND b.accounting_grant_id IS NOT NULL
          AND b.expires_at<=CURRENT_TIMESTAMP
        ORDER BY b.operation_id FOR UPDATE SKIP LOCKED LIMIT p_limit
    ) pick;
    IF candidate_ids IS NULL THEN RETURN 0; END IF;
    PERFORM 1 FROM deltallm_accounting_grants g
    WHERE g.grant_id=ANY(ARRAY(
        SELECT b.accounting_grant_id FROM deltallm_billing_operations b
        WHERE b.operation_id=ANY(candidate_ids)
    )) ORDER BY g.grant_id FOR UPDATE;
    PERFORM deltallm_accounting_lock_event_publication(p_generation,ARRAY(
        SELECT b.accounting_partition FROM deltallm_billing_operations b
        WHERE b.operation_id=ANY(candidate_ids)
    ));
    FOR op IN SELECT b.* FROM deltallm_billing_operations b
        WHERE b.operation_id=ANY(candidate_ids) ORDER BY b.operation_id
    LOOP$new$;
BEGIN
    SELECT pg_get_functiondef('deltallm_accounting_reconcile_expired_grants(bigint,integer)'::regprocedure)
        INTO STRICT definition;
    IF length(definition)-length(replace(definition,declaration,''))<>length(declaration)
       OR length(definition)-length(replace(definition,old_selection,''))<>length(old_selection) THEN
        RAISE EXCEPTION 'accounting_event_grant_expiry_definition';
    END IF;
    definition:=replace(definition,declaration,declaration||E'\n    candidate_ids TEXT[];');
    EXECUTE replace(definition,old_selection,new_selection);
END;
$migration$;

COMMIT;
