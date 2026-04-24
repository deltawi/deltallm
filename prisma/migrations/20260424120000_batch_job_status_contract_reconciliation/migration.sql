DO $$
DECLARE
    final_labels constant text[] := ARRAY[
        'queued',
        'in_progress',
        'finalizing',
        'completed',
        'failed',
        'cancelled',
        'expired'
    ]::text[];
    legacy_labels constant text[] := ARRAY[
        'validating',
        'queued',
        'in_progress',
        'finalizing',
        'completed',
        'failed',
        'cancelled',
        'expired'
    ]::text[];
    existing_labels text[];
    next_labels text[];
    status_udt_name text;
    invalid_statuses text[];
    existing_type_reference_count integer;
    next_type_reference_count integer;
BEGIN
    SELECT c.udt_name
    INTO status_udt_name
    FROM information_schema.columns c
    WHERE c.table_schema = 'public'
      AND c.table_name = 'deltallm_batch_job'
      AND c.column_name = 'status';

    IF status_udt_name IS NULL THEN
        RAISE EXCEPTION 'Cannot reconcile deltallm_batch_job.status: column is missing';
    END IF;

    SELECT COALESCE(array_agg(DISTINCT status::text ORDER BY status::text), ARRAY[]::text[])
    INTO invalid_statuses
    FROM "deltallm_batch_job"
    WHERE status::text <> ALL(final_labels);

    IF COALESCE(array_length(invalid_statuses, 1), 0) > 0 THEN
        RAISE EXCEPTION 'Cannot reconcile deltallm_batch_job.status: found invalid existing values %', invalid_statuses;
    END IF;

    SELECT array_agg(e.enumlabel ORDER BY e.enumsortorder)
    INTO existing_labels
    FROM pg_enum e
    JOIN pg_type t ON t.oid = e.enumtypid
    WHERE t.typname = 'DeltaLLM_BatchJobStatus';

    SELECT array_agg(e.enumlabel ORDER BY e.enumsortorder)
    INTO next_labels
    FROM pg_enum e
    JOIN pg_type t ON t.oid = e.enumtypid
    WHERE t.typname = 'DeltaLLM_BatchJobStatus_next';

    SELECT COUNT(*)
    INTO existing_type_reference_count
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND udt_name = 'DeltaLLM_BatchJobStatus';

    SELECT COUNT(*)
    INTO next_type_reference_count
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND udt_name = 'DeltaLLM_BatchJobStatus_next';

    IF existing_labels IS NOT NULL
       AND existing_labels IS DISTINCT FROM final_labels
       AND existing_labels IS DISTINCT FROM legacy_labels THEN
        RAISE EXCEPTION 'Cannot reconcile DeltaLLM_BatchJobStatus: unexpected enum labels %', existing_labels;
    END IF;

    IF next_labels IS NOT NULL
       AND next_labels IS DISTINCT FROM final_labels THEN
        RAISE EXCEPTION 'Cannot reconcile DeltaLLM_BatchJobStatus_next: unexpected enum labels %', next_labels;
    END IF;

    ALTER TABLE "deltallm_batch_job"
    DROP CONSTRAINT IF EXISTS "deltallm_batch_job_status_chk";

    IF status_udt_name = 'DeltaLLM_BatchJobStatus'
       AND existing_labels IS NOT DISTINCT FROM final_labels THEN
        IF next_labels IS NOT NULL
           AND next_type_reference_count = 0 THEN
            EXECUTE 'DROP TYPE "DeltaLLM_BatchJobStatus_next"';
        END IF;
        RETURN;
    END IF;

    IF status_udt_name = 'DeltaLLM_BatchJobStatus_next' THEN
        IF next_labels IS DISTINCT FROM final_labels THEN
            RAISE EXCEPTION 'Cannot reconcile deltallm_batch_job.status: expected DeltaLLM_BatchJobStatus_next to have final labels, found %', next_labels;
        END IF;
        IF existing_labels IS NOT NULL THEN
            IF existing_type_reference_count > 0 THEN
                RAISE EXCEPTION 'Cannot reconcile deltallm_batch_job.status: DeltaLLM_BatchJobStatus is still referenced by % columns while status uses DeltaLLM_BatchJobStatus_next', existing_type_reference_count;
            END IF;
            EXECUTE 'DROP TYPE "DeltaLLM_BatchJobStatus"';
        END IF;
        EXECUTE 'ALTER TYPE "DeltaLLM_BatchJobStatus_next" RENAME TO "DeltaLLM_BatchJobStatus"';
        RETURN;
    END IF;

    IF existing_labels IS NULL
       AND next_labels IS NOT NULL THEN
        IF next_type_reference_count > 0 THEN
            RAISE EXCEPTION 'Cannot reconcile deltallm_batch_job.status: DeltaLLM_BatchJobStatus_next is still referenced by % columns before rename', next_type_reference_count;
        END IF;
        EXECUTE 'ALTER TYPE "DeltaLLM_BatchJobStatus_next" RENAME TO "DeltaLLM_BatchJobStatus"';
        existing_labels := final_labels;
        next_labels := NULL;
    END IF;

    IF existing_labels IS NULL THEN
        CREATE TYPE "DeltaLLM_BatchJobStatus" AS ENUM (
            'queued',
            'in_progress',
            'finalizing',
            'completed',
            'failed',
            'cancelled',
            'expired'
        );
        existing_labels := final_labels;
    END IF;

    IF existing_labels IS NOT DISTINCT FROM legacy_labels THEN
        IF next_labels IS NULL THEN
            EXECUTE '
                CREATE TYPE "DeltaLLM_BatchJobStatus_next" AS ENUM (
                    ''queued'',
                    ''in_progress'',
                    ''finalizing'',
                    ''completed'',
                    ''failed'',
                    ''cancelled'',
                    ''expired''
                )
            ';
            next_labels := final_labels;
        END IF;

        EXECUTE '
            ALTER TABLE "deltallm_batch_job"
            ALTER COLUMN "status" DROP DEFAULT
        ';
        EXECUTE '
            ALTER TABLE "deltallm_batch_job"
            ALTER COLUMN "status" TYPE "DeltaLLM_BatchJobStatus_next"
            USING ("status"::text::"DeltaLLM_BatchJobStatus_next")
        ';
        SELECT COUNT(*)
        INTO existing_type_reference_count
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND udt_name = 'DeltaLLM_BatchJobStatus';
        IF existing_type_reference_count > 0 THEN
            RAISE EXCEPTION 'Cannot reconcile deltallm_batch_job.status: DeltaLLM_BatchJobStatus is still referenced by % columns after moving status to DeltaLLM_BatchJobStatus_next', existing_type_reference_count;
        END IF;
        EXECUTE 'DROP TYPE "DeltaLLM_BatchJobStatus"';
        EXECUTE 'ALTER TYPE "DeltaLLM_BatchJobStatus_next" RENAME TO "DeltaLLM_BatchJobStatus"';
        RETURN;
    END IF;

    EXECUTE '
        ALTER TABLE "deltallm_batch_job"
        ALTER COLUMN "status" DROP DEFAULT
    ';
    EXECUTE '
        ALTER TABLE "deltallm_batch_job"
        ALTER COLUMN "status" TYPE "DeltaLLM_BatchJobStatus"
        USING ("status"::text::"DeltaLLM_BatchJobStatus")
    ';
END $$;
