ALTER TABLE "deltallm_batch_item"
ADD COLUMN IF NOT EXISTS "claim_epoch" INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS "deltallm_batch_item_in_progress_capacity_idx"
ON "deltallm_batch_item"("scheduling_model_group", "lease_expires_at", "batch_id")
WHERE "status" = 'in_progress';
