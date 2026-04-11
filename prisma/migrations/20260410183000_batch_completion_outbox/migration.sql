CREATE TABLE IF NOT EXISTS "deltallm_batch_completion_outbox" (
  "completion_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "batch_id" TEXT NOT NULL,
  "item_id" TEXT NOT NULL,
  "payload_json" JSONB NOT NULL,
  "status" TEXT NOT NULL DEFAULT 'queued',
  "attempt_count" INTEGER NOT NULL DEFAULT 0,
  "max_attempts" INTEGER NOT NULL DEFAULT 5,
  "next_attempt_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "last_error" TEXT,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "processed_at" TIMESTAMP(3),
  CONSTRAINT "deltallm_batch_completion_outbox_pkey" PRIMARY KEY ("completion_id")
);

CREATE UNIQUE INDEX IF NOT EXISTS "deltallm_batch_completion_outbox_item_id_key"
  ON "deltallm_batch_completion_outbox" ("item_id");

CREATE INDEX IF NOT EXISTS "deltallm_batchcompletionoutbox_status_next_attempt_idx"
  ON "deltallm_batch_completion_outbox" ("status", "next_attempt_at");

CREATE INDEX IF NOT EXISTS "deltallm_batchcompletionoutbox_batch_created_idx"
  ON "deltallm_batch_completion_outbox" ("batch_id", "created_at");
