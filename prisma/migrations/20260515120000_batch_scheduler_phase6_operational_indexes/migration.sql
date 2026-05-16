-- Keep this concurrent because deltallm_batch_item is write-hot while batch
-- workers are running. Do not wrap this migration in an explicit transaction.
ALTER TABLE "deltallm_batch_item"
ADD COLUMN IF NOT EXISTS "claim_epoch" INTEGER NOT NULL DEFAULT 0;

CREATE INDEX CONCURRENTLY IF NOT EXISTS "deltallm_batch_item_in_progress_capacity_idx"
ON "deltallm_batch_item"("scheduling_model_group", "lease_expires_at", "batch_id")
WHERE "status" = 'in_progress';
