DO $recovery_verify$
DECLARE
    definition TEXT;
    inspection_position INTEGER;
    eligibility_position INTEGER;
BEGIN
    IF (SELECT count(*) FROM information_schema.columns
        WHERE table_schema='public' AND table_name='deltallm_accounting_recovery_cursors'
    )<>4 OR NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='deltallm_accounting_recovery_cursors'::regclass
          AND contype='p' AND pg_get_constraintdef(oid)='PRIMARY KEY (protocol_name, generation)'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='deltallm_accounting_recovery_cursors'::regclass
          AND contype='f' AND confrelid='deltallm_accounting_protocols'::regclass
          AND confdeltype='c' AND confupdtype='r'
    ) THEN
        RAISE EXCEPTION 'accounting recovery cursor contract is missing';
    END IF;
    IF (SELECT count(*) FROM pg_index i
        WHERE i.indrelid='deltallm_accounting_grants'::regclass AND i.indisvalid
          AND pg_get_indexdef(i.indexrelid) LIKE '%(generation, expires_at, grant_id)%'
          AND (
              (i.indexrelid::regclass::text='deltallm_accounting_grant_expiry_work_idx'
               AND pg_get_expr(i.indpred,i.indrelid) LIKE '%state = ''active''%')
              OR (i.indexrelid::regclass::text='deltallm_accounting_grant_drain_work_idx'
                  AND pg_get_expr(i.indpred,i.indrelid) LIKE '%state = ''draining''%')
          ) AND pg_get_expr(i.indpred,i.indrelid) LIKE '%protocol_name = ''primary''%'
    )<>2 THEN
        RAISE EXCEPTION 'accounting recovery work indexes are missing';
    END IF;
    SELECT pg_get_functiondef('deltallm_accounting_reconcile_grants(bigint,integer)'::regprocedure)
        INTO STRICT definition;
    inspection_position:=strpos(definition,'WITH forward_scan AS MATERIALIZED');
    eligibility_position:=strpos(definition,'FROM unnest(inspected_ids) selected(grant_id)');
    IF inspection_position=0 OR eligibility_position<=inspection_position
       OR strpos(definition,'LIMIT (p_limit-(SELECT count(*) FROM forward_scan))')=0
       OR strpos(definition,'last_expires_at=next_expires_at,last_grant_id=next_grant_id')=0
       OR strpos(definition,'cursor_row.last_expires_at,cursor_row.last_grant_id')=0
       OR strpos(definition,'IF NOT FOUND THEN RETURN 0; END IF;')=0
       OR strpos(definition,'IF p_generation IS NULL OR COALESCE(p_limit,0) NOT BETWEEN 1 AND 256')=0
       OR strpos(definition,'WHERE j.grant_id=selected.grant_id AND j.status<>''completed'' OFFSET 0')=0
       OR strpos(definition,'b.accounting_state=''reserved'' OFFSET 0')=0
       OR strpos(definition,'reserved_exact=w.reserved_exact-d.allocated_exact')=0
       OR strpos(definition,'provisional_exact=w.provisional_exact+d.provisional_exact')=0
       OR strpos(definition,'outstanding_count=p.outstanding_count-d.slots')=0
       OR strpos(definition,'allocated_exact-consumed_exact-returned_exact,0')=0
       OR strpos(definition,'operation_limit-consumed_operations-returned_operations,0')=0 THEN
        RAISE EXCEPTION 'accounting recovery scan or financial guards are missing';
    END IF;
END
$recovery_verify$;
