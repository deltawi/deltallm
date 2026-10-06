DO $presence_verify$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='deltallm_accounting_projection_presence'::regclass
          AND contype='p' AND pg_get_constraintdef(oid)='PRIMARY KEY (protocol_name, generation, slot)'
    ) THEN
        RAISE EXCEPTION 'projection presence identity is missing';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='deltallm_accounting_projection_presence'::regclass
          AND contype='f' AND confrelid='deltallm_accounting_protocols'::regclass
          AND confdeltype='c' AND confupdtype='r'
    ) THEN
        RAISE EXCEPTION 'projection presence generation fence is missing';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='deltallm_accounting_projection_presence'::regclass
          AND contype='c' AND pg_get_constraintdef(oid) LIKE '%slot >= 0%slot <= 63%'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='deltallm_accounting_projection_presence'::regclass
          AND contype='c' AND pg_get_constraintdef(oid) LIKE '%isfinite(expires_at)%'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='deltallm_accounting_projection_presence'::regclass
          AND contype='c' AND pg_get_constraintdef(oid) LIKE '%NOT ready%owner_token IS NOT NULL%'
    ) THEN
        RAISE EXCEPTION 'projection presence bounds are missing';
    END IF;
    IF (SELECT count(*) FROM pg_index
        WHERE indrelid='deltallm_accounting_projection_presence'::regclass AND indisvalid)<>1 THEN
        RAISE EXCEPTION 'projection presence must have only its fixed-slot primary index';
    END IF;
END
$presence_verify$;
