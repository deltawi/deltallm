DO $$
DECLARE
    existing_labels text[];
BEGIN
    SELECT array_agg(e.enumlabel ORDER BY e.enumsortorder)
    INTO existing_labels
    FROM pg_enum e
    JOIN pg_type t ON t.oid = e.enumtypid
    WHERE t.typname = 'DeltaLLM_BatchJobStatus';

    IF existing_labels IS NULL THEN
        RAISE EXCEPTION 'DeltaLLM_BatchJobStatus enum type is missing; apply prior batch job status migrations first';
    END IF;

    IF existing_labels = ARRAY[
        'queued',
        'in_progress',
        'finalizing',
        'completed',
        'failed',
        'cancelled',
        'expired'
    ]::text[] THEN
        RETURN;
    END IF;

    IF existing_labels IS DISTINCT FROM ARRAY[
        'validating',
        'queued',
        'in_progress',
        'finalizing',
        'completed',
        'failed',
        'cancelled',
        'expired'
    ]::text[] THEN
        RAISE EXCEPTION 'Cannot remove validating from DeltaLLM_BatchJobStatus: unexpected enum labels %', existing_labels;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM "deltallm_batch_job"
        WHERE "status"::text = 'validating'
    ) THEN
        RAISE EXCEPTION 'Cannot remove validating from DeltaLLM_BatchJobStatus: found deltallm_batch_job rows still using validating';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_type
        WHERE typname = 'DeltaLLM_BatchJobStatus_next'
    ) THEN
        EXECUTE 'DROP TYPE "DeltaLLM_BatchJobStatus_next"';
    END IF;

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

    EXECUTE '
        ALTER TABLE "deltallm_batch_job"
        ALTER COLUMN "status" DROP DEFAULT
    ';

    EXECUTE '
        ALTER TABLE "deltallm_batch_job"
        ALTER COLUMN "status" TYPE "DeltaLLM_BatchJobStatus_next"
        USING ("status"::text::"DeltaLLM_BatchJobStatus_next")
    ';

    EXECUTE 'DROP TYPE "DeltaLLM_BatchJobStatus"';
    EXECUTE 'ALTER TYPE "DeltaLLM_BatchJobStatus_next" RENAME TO "DeltaLLM_BatchJobStatus"';
END $$;
