DO $native_reporting_verify$
BEGIN
    IF (SELECT count(*) FROM information_schema.columns
      WHERE table_schema='public' AND table_name='deltallm_accounting_terminal_journal'
        AND column_name IN ('receipt_only','committed_exact','provisional_exact','released_exact'))<>4
      OR NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conrelid='deltallm_accounting_terminal_journal'::regclass
          AND conname='deltallm_terminal_receipt_split')
      OR pg_get_functiondef('deltallm_accounting_reconcile_grants(bigint,integer)'::regprocedure)
        NOT LIKE '%compact_receipts%'
      OR pg_get_functiondef('deltallm_accounting_materialize_terminal_journal(bigint,text,uuid,bigint[])'::regprocedure)
        NOT LIKE '%document.journal_sequence=(raw.v->>''sequence'')::bigint OFFSET 0%' THEN
        RAISE EXCEPTION 'native compact receipt settlement is missing';
    END IF;
    IF to_regclass('deltallm_accounting_usage_facts_v2') IS NULL
      OR to_regclass('deltallm_accounting_usage_rollups_v2') IS NULL
      OR to_regclass('deltallm_spend_read_events_v2') IS NULL THEN
        RAISE EXCEPTION 'native reporting relations are missing';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
      WHERE table_schema='public' AND table_name='deltallm_accounting_usage_facts_v2'
        AND column_name='spend_exact' AND numeric_precision=38 AND numeric_scale=18)
      OR NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conrelid='deltallm_accounting_usage_facts_v2'::regclass AND contype='u'
          AND pg_get_constraintdef(oid)='UNIQUE (accounting_sequence)')
      OR NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conrelid='deltallm_accounting_usage_rollups_v2'::regclass AND contype='p'
          AND pg_get_constraintdef(oid) LIKE '%rollup_shard)%') THEN
        RAISE EXCEPTION 'native reporting exact amount or identity is missing';
    END IF;
    IF (SELECT count(*) FROM pg_index
      WHERE indrelid='deltallm_accounting_usage_facts_v2'::regclass AND indisvalid)<>11
      OR (SELECT count(*) FROM pg_index
        WHERE indrelid='deltallm_accounting_usage_rollups_v2'::regclass AND indisvalid)<>4
      OR NOT EXISTS (SELECT 1 FROM pg_index
        WHERE indexrelid='deltallm_accounting_terminal_projection_idx'::regclass
          AND indisvalid AND indisready AND indpred IS NOT NULL) THEN
        RAISE EXCEPTION 'native reporting index budget is not valid';
    END IF;
    IF to_regprocedure('deltallm_accounting_project_read_models(bigint,text,uuid,integer,bigint,bigint[])') IS NULL
      OR NOT EXISTS (SELECT 1 FROM pg_proc
        WHERE oid='deltallm_accounting_backlog_snapshot(bigint)'::regprocedure
          AND proconfig @> ARRAY['enable_seqscan=off','enable_bitmapscan=off'])
      OR NOT EXISTS (SELECT 1 FROM pg_proc
        WHERE oid='deltallm_accounting_project_read_models(bigint,text,uuid,integer,bigint,bigint[])'::regprocedure
          AND proconfig @> ARRAY['enable_seqscan=off','enable_bitmapscan=off'])
      OR pg_get_functiondef('deltallm_accounting_project_read_models(bigint,text,uuid,integer,bigint,bigint[])'::regprocedure)
        NOT LIKE '%accounting_read_model_operation_index%'
      OR pg_get_viewdef('deltallm_spend_read_events_v2'::regclass) NOT LIKE '%NOT (EXISTS%'
    THEN
        RAISE EXCEPTION 'native projection or bounded health function is missing';
    END IF;
END
$native_reporting_verify$;

DO $native_batch_verify$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='deltallm_batch_item'
          AND column_name='accounting_checkpoint' AND data_type='jsonb'
          AND is_nullable='YES'
    ) OR (
        SELECT count(*) FROM pg_constraint
        WHERE conrelid='deltallm_batch_item'::regclass
          AND conname IN ('batch_accounting_checkpoint_bound','batch_accounting_checkpoint_envelope')
    )<>2 THEN
        RAISE EXCEPTION 'native batch recovery proof is missing';
    END IF;
END
$native_batch_verify$;
