ALTER TABLE "deltallm_batch_create_session"
ADD COLUMN "webhook_config_ciphertext" TEXT,
ADD COLUMN "webhook_config_fingerprint" TEXT;

ALTER TABLE "deltallm_batch_job"
ADD COLUMN "webhook_config_ciphertext" TEXT,
ADD COLUMN "webhook_config_fingerprint" TEXT;

CREATE TABLE "deltallm_batch_webhook_outbox" (
    "event_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
    "batch_id" TEXT NOT NULL,
    "event_type" TEXT NOT NULL,
    "target_config_ciphertext" TEXT NOT NULL,
    "payload_json" JSONB NOT NULL,
    "payload_sha256" TEXT NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'queued',
    "attempt_count" INTEGER NOT NULL DEFAULT 0,
    "max_attempts" INTEGER NOT NULL DEFAULT 8,
    "next_attempt_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "last_status_code" INTEGER,
    "last_error" VARCHAR(2048),
    "locked_by" TEXT,
    "lease_expires_at" TIMESTAMP(3),
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "delivered_at" TIMESTAMP(3),

    CONSTRAINT "deltallm_batch_webhook_outbox_pkey" PRIMARY KEY ("event_id"),
    CONSTRAINT "deltallm_batch_webhook_outbox_event_type_chk"
        CHECK ("event_type" IN ('batch.completed', 'batch.failed', 'batch.cancelled', 'batch.expired')),
    CONSTRAINT "deltallm_batch_webhook_outbox_status_chk"
        CHECK ("status" IN ('queued', 'processing', 'retrying', 'delivered', 'failed')),
    CONSTRAINT "deltallm_batch_webhook_outbox_attempt_count_chk"
        CHECK ("attempt_count" >= 0 AND "max_attempts" > 0)
);

CREATE UNIQUE INDEX "deltallm_batch_webhook_outbox_batch_event_key"
ON "deltallm_batch_webhook_outbox"("batch_id", "event_type");

CREATE INDEX "deltallm_batch_webhook_outbox_due_idx"
ON "deltallm_batch_webhook_outbox"("status", "next_attempt_at");

CREATE INDEX "deltallm_batch_webhook_outbox_lease_idx"
ON "deltallm_batch_webhook_outbox"("lease_expires_at");

CREATE INDEX "deltallm_batch_webhook_outbox_batch_idx"
ON "deltallm_batch_webhook_outbox"("batch_id", "created_at");
