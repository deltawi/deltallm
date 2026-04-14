DO $$
DECLARE
    existing_labels text[];
    old_labels constant text[] := ARRAY[
        'validating',
        'queued',
        'in_progress',
        'finalizing',
        'completed',
        'failed',
        'cancelled',
        'expired'
    ]::text[];
    final_labels constant text[] := ARRAY[
        'queued',
        'in_progress',
        'finalizing',
        'completed',
        'failed',
        'cancelled',
        'expired'
    ]::text[];
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_type
        WHERE typname = 'DeltaLLM_BatchJobStatus'
    ) THEN
        CREATE TYPE "DeltaLLM_BatchJobStatus" AS ENUM (
            'validating',
            'queued',
            'in_progress',
            'finalizing',
            'completed',
            'failed',
            'cancelled',
            'expired'
        );
    END IF;

    SELECT array_agg(e.enumlabel ORDER BY e.enumsortorder)
    INTO existing_labels
    FROM pg_enum e
    JOIN pg_type t ON t.oid = e.enumtypid
    WHERE t.typname = 'DeltaLLM_BatchJobStatus';

    IF existing_labels IS DISTINCT FROM old_labels
       AND existing_labels IS DISTINCT FROM final_labels THEN
        RAISE EXCEPTION 'Cannot migrate DeltaLLM_BatchJobStatus: existing enum labels do not match the expected old or final set';
    END IF;

    IF existing_labels = old_labels THEN
        IF EXISTS (
            SELECT 1
            FROM "deltallm_batch_job"
            WHERE "status" NOT IN ('validating', 'queued', 'in_progress', 'finalizing', 'completed', 'failed', 'cancelled', 'expired')
        ) THEN
            RAISE EXCEPTION 'Cannot migrate deltallm_batch_job.status to enum: found invalid existing values';
        END IF;
    ELSE
        IF EXISTS (
            SELECT 1
            FROM "deltallm_batch_job"
            WHERE "status" NOT IN ('queued', 'in_progress', 'finalizing', 'completed', 'failed', 'cancelled', 'expired')
        ) THEN
            RAISE EXCEPTION 'Cannot migrate deltallm_batch_job.status to enum: found invalid existing values for the final enum shape';
        END IF;
    END IF;
END $$;

ALTER TABLE "deltallm_batch_job"
DROP CONSTRAINT IF EXISTS "deltallm_batch_job_status_chk";

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'deltallm_batch_job'
          AND column_name = 'status'
          AND udt_name <> 'DeltaLLM_BatchJobStatus'
    ) THEN
        EXECUTE '
            ALTER TABLE "deltallm_batch_job"
            ALTER COLUMN "status" TYPE "DeltaLLM_BatchJobStatus"
            USING ("status"::"DeltaLLM_BatchJobStatus")
        ';
    END IF;
END $$;
