CREATE TABLE IF NOT EXISTS "deltallm_spend_ingestion_outbox" (
  "event_id" TEXT NOT NULL,
  "event_type" TEXT NOT NULL,
  "payload_json" JSONB NOT NULL,
  "status" TEXT NOT NULL DEFAULT 'queued',
  "attempt_count" INTEGER NOT NULL DEFAULT 0,
  "max_attempts" INTEGER NOT NULL DEFAULT 10,
  "next_attempt_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "last_error" TEXT,
  "locked_by" TEXT,
  "lease_expires_at" TIMESTAMP(3),
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "processed_at" TIMESTAMP(3),
  CONSTRAINT "deltallm_spend_ingestion_outbox_pkey" PRIMARY KEY ("event_id")
);

CREATE INDEX IF NOT EXISTS "deltallm_spendingestionoutbox_status_due_idx"
  ON "deltallm_spend_ingestion_outbox" ("status", "next_attempt_at");

CREATE INDEX IF NOT EXISTS "deltallm_spendingestionoutbox_lease_idx"
  ON "deltallm_spend_ingestion_outbox" ("lease_expires_at");

CREATE INDEX IF NOT EXISTS "deltallm_spendingestionoutbox_created_idx"
  ON "deltallm_spend_ingestion_outbox" ("created_at");
