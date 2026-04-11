ALTER TABLE "deltallm_batch_completion_outbox"
  ADD COLUMN IF NOT EXISTS "locked_by" TEXT,
  ADD COLUMN IF NOT EXISTS "lease_expires_at" TIMESTAMP(3);

CREATE INDEX IF NOT EXISTS "deltallm_batchcompletionoutbox_lease_expires_idx"
  ON "deltallm_batch_completion_outbox" ("lease_expires_at");

UPDATE "deltallm_batch_completion_outbox"
SET "status" = 'retrying',
    "locked_by" = NULL,
    "lease_expires_at" = NULL,
    "next_attempt_at" = CURRENT_TIMESTAMP,
    "updated_at" = CURRENT_TIMESTAMP
WHERE "status" = 'processing';
