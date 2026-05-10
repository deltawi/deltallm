ALTER TABLE "deltallm_batch_job"
ADD COLUMN "scheduler_version" TEXT,
ADD COLUMN "scheduling_model" TEXT,
ADD COLUMN "scheduling_model_group" TEXT,
ADD COLUMN "scheduling_endpoint" TEXT,
ADD COLUMN "tenant_scope_type" TEXT,
ADD COLUMN "tenant_scope_id" TEXT,
ADD COLUMN "service_tier" TEXT NOT NULL DEFAULT 'standard',
ADD COLUMN "estimated_work_units" INTEGER NOT NULL DEFAULT 0,
ADD COLUMN "remaining_work_units" INTEGER NOT NULL DEFAULT 0,
ADD COLUMN "size_class" TEXT NOT NULL DEFAULT 'xs',
ADD COLUMN "queue_entered_at" TIMESTAMP(3),
ADD COLUMN "first_claimed_at" TIMESTAMP(3),
ADD COLUMN "last_claimed_at" TIMESTAMP(3),
ADD COLUMN "last_scheduled_at" TIMESTAMP(3),
ADD COLUMN "scheduler_debug" JSONB;

ALTER TABLE "deltallm_batch_item"
ADD COLUMN "scheduling_model" TEXT,
ADD COLUMN "scheduling_model_group" TEXT,
ADD COLUMN "estimated_work_units" INTEGER NOT NULL DEFAULT 1,
ADD COLUMN "not_before_at" TIMESTAMP(3),
ADD COLUMN "last_scheduled_at" TIMESTAMP(3);

-- Existing rows are backfilled in bounded batches by BatchMaintenanceRepository.
-- Keeping this migration additive avoids long table rewrites on gateway databases.

CREATE INDEX IF NOT EXISTS "deltallm_batch_job_sched_model_status_idx"
ON "deltallm_batch_job"("status", "scheduling_model_group", "service_tier", "queue_entered_at");

CREATE INDEX IF NOT EXISTS "deltallm_batch_job_sched_tenant_status_idx"
ON "deltallm_batch_job"("status", "tenant_scope_type", "tenant_scope_id", "queue_entered_at");

CREATE INDEX IF NOT EXISTS "deltallm_batch_job_sched_size_status_idx"
ON "deltallm_batch_job"("status", "size_class", "queue_entered_at");

CREATE INDEX IF NOT EXISTS "deltallm_batch_item_sched_batch_status_idx"
ON "deltallm_batch_item"("batch_id", "status", "not_before_at", "line_number");

CREATE INDEX IF NOT EXISTS "deltallm_batch_item_sched_model_status_idx"
ON "deltallm_batch_item"("scheduling_model_group", "status", "not_before_at");
