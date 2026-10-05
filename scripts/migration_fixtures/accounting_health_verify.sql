DO $health_verify$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_index i
        WHERE i.indexrelid=to_regclass('deltallm_accounting_terminal_oldest_work_idx')
          AND i.indrelid='deltallm_accounting_terminal_journal'::regclass AND i.indisvalid
          AND pg_get_indexdef(i.indexrelid) LIKE '%(generation, accepted_at, sequence)%'
          AND pg_get_expr(i.indpred,i.indrelid) LIKE '%protocol_name = ''primary''%'
          AND pg_get_expr(i.indpred,i.indrelid) LIKE '%status <> ''completed''%'
    ) THEN
        RAISE EXCEPTION 'accounting oldest terminal work index is missing';
    END IF;
    IF (SELECT count(*) FROM information_schema.columns
        WHERE table_schema='public' AND table_name='deltallm_accounting_terminal_capacity'
          AND column_name IN ('max_entries','max_bytes','pending_entries','pending_bytes','failed_entries')
    )<>5 THEN
        RAISE EXCEPTION 'accounting terminal health counters are missing';
    END IF;
END
$health_verify$;
