ALTER TABLE "deltallm_spend_ingestion_outbox"
  ADD COLUMN IF NOT EXISTS "claim_token" TEXT,
  ADD COLUMN IF NOT EXISTS "blocked_at" TIMESTAMP(3),
  ADD COLUMN IF NOT EXISTS "replay_count" INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS "last_replayed_at" TIMESTAMP(3),
  ADD COLUMN IF NOT EXISTS "last_replayed_by" TEXT;

ALTER TABLE "deltallm_audit_ingestion_outbox"
  ADD COLUMN IF NOT EXISTS "claim_token" TEXT,
  ADD COLUMN IF NOT EXISTS "blocked_at" TIMESTAMP(3),
  ADD COLUMN IF NOT EXISTS "replay_count" INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS "last_replayed_at" TIMESTAMP(3),
  ADD COLUMN IF NOT EXISTS "last_replayed_by" TEXT;

UPDATE "deltallm_spend_ingestion_outbox"
SET "status" = 'blocked',
    "blocked_at" = COALESCE("processed_at", "updated_at"),
    "processed_at" = COALESCE("processed_at", "updated_at"),
    "locked_by" = NULL,
    "claim_token" = NULL,
    "lease_expires_at" = NULL
WHERE "status" = 'failed';

UPDATE "deltallm_audit_ingestion_outbox"
SET "status" = 'blocked',
    "blocked_at" = COALESCE("processed_at", "updated_at"),
    "processed_at" = COALESCE("processed_at", "updated_at"),
    "locked_by" = NULL,
    "claim_token" = NULL,
    "lease_expires_at" = NULL
WHERE "status" = 'failed'
  AND "delivery_class" = 'required';

UPDATE "deltallm_telemetry_ingestion_capacity"
SET "pending_count" = (
      SELECT COUNT(*)
      FROM "deltallm_spend_ingestion_outbox"
      WHERE "status" IN ('queued', 'retry', 'processing', 'blocked', 'failed')
    ),
    "updated_at" = CURRENT_TIMESTAMP
WHERE "queue_name" = 'spend';

UPDATE "deltallm_telemetry_ingestion_capacity"
SET "pending_count" = (
      SELECT COUNT(*)
      FROM "deltallm_audit_ingestion_outbox"
      WHERE "status" IN ('queued', 'retry', 'processing', 'blocked')
         OR ("status" = 'failed' AND "delivery_class" = 'required')
    ),
    "updated_at" = CURRENT_TIMESTAMP
WHERE "queue_name" = 'audit';

CREATE INDEX IF NOT EXISTS "deltallm_spendingestionoutbox_claim_idx"
  ON "deltallm_spend_ingestion_outbox" ("event_id", "claim_token")
  WHERE "status" = 'processing';

CREATE INDEX IF NOT EXISTS "deltallm_spendingestionoutbox_blocked_idx"
  ON "deltallm_spend_ingestion_outbox" ("blocked_at", "event_id")
  WHERE "status" = 'blocked';

CREATE INDEX IF NOT EXISTS "deltallm_auditingestionoutbox_claim_idx"
  ON "deltallm_audit_ingestion_outbox" ("event_id", "claim_token")
  WHERE "status" = 'processing';

CREATE INDEX IF NOT EXISTS "deltallm_auditingestionoutbox_blocked_idx"
  ON "deltallm_audit_ingestion_outbox" ("delivery_class", "blocked_at", "event_id")
  WHERE "status" = 'blocked';

ALTER TABLE "deltallm_spend_ingestion_outbox"
  DROP CONSTRAINT IF EXISTS "deltallm_spend_ingestion_outbox_status_check";

ALTER TABLE "deltallm_spend_ingestion_outbox"
  ADD CONSTRAINT "deltallm_spend_ingestion_outbox_status_check"
  CHECK ("status" IN ('queued', 'retry', 'processing', 'completed', 'failed', 'blocked')) NOT VALID;

ALTER TABLE "deltallm_audit_ingestion_outbox"
  DROP CONSTRAINT IF EXISTS "deltallm_audit_ingestion_outbox_status_check";

ALTER TABLE "deltallm_audit_ingestion_outbox"
  ADD CONSTRAINT "deltallm_audit_ingestion_outbox_status_check"
  CHECK ("status" IN ('queued', 'retry', 'processing', 'completed', 'failed', 'blocked')) NOT VALID;
