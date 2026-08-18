CREATE TABLE IF NOT EXISTS "deltallm_telemetry_ingestion_capacity" (
  "queue_name" TEXT NOT NULL,
  "pending_count" BIGINT NOT NULL DEFAULT 0,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "deltallm_telemetry_ingestion_capacity_pkey" PRIMARY KEY ("queue_name"),
  CONSTRAINT "deltallm_telemetry_ingestion_capacity_nonnegative" CHECK ("pending_count" >= 0)
);

INSERT INTO "deltallm_telemetry_ingestion_capacity" ("queue_name", "pending_count")
VALUES
  ('spend', 0),
  ('audit', 0)
ON CONFLICT ("queue_name") DO NOTHING;

CREATE INDEX IF NOT EXISTS "deltallm_spendingestionoutbox_active_created_idx"
  ON "deltallm_spend_ingestion_outbox" ("created_at", "event_id")
  WHERE "status" IN ('queued', 'retry', 'processing');

CREATE INDEX IF NOT EXISTS "deltallm_spendingestionoutbox_terminal_retention_idx"
  ON "deltallm_spend_ingestion_outbox" ("status", "processed_at", "updated_at")
  WHERE "status" IN ('completed', 'failed');

CREATE TABLE IF NOT EXISTS "deltallm_audit_ingestion_outbox" (
  "event_id" TEXT NOT NULL,
  "record_type" TEXT NOT NULL,
  "organization_id" TEXT,
  "delivery_class" TEXT NOT NULL DEFAULT 'best_effort',
  "payload_json" JSONB NOT NULL,
  "redacted_payload_json" JSONB NOT NULL,
  "policy_version" BIGINT NOT NULL DEFAULT 0,
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
  CONSTRAINT "deltallm_audit_ingestion_outbox_pkey" PRIMARY KEY ("event_id"),
  CONSTRAINT "deltallm_audit_ingestion_outbox_type_check"
    CHECK ("record_type" IN ('audit_event', 'prompt_render')),
  CONSTRAINT "deltallm_audit_ingestion_outbox_delivery_check"
    CHECK ("delivery_class" IN ('required', 'best_effort'))
);

CREATE INDEX IF NOT EXISTS "deltallm_auditingestionoutbox_due_idx"
  ON "deltallm_audit_ingestion_outbox" ("delivery_class", "status", "next_attempt_at", "created_at");

CREATE INDEX IF NOT EXISTS "deltallm_auditingestionoutbox_lease_idx"
  ON "deltallm_audit_ingestion_outbox" ("lease_expires_at")
  WHERE "status" = 'processing';

CREATE INDEX IF NOT EXISTS "deltallm_auditingestionoutbox_org_active_idx"
  ON "deltallm_audit_ingestion_outbox" ("organization_id", "created_at")
  WHERE "status" IN ('queued', 'retry', 'processing');

CREATE INDEX IF NOT EXISTS "deltallm_auditingestionoutbox_terminal_retention_idx"
  ON "deltallm_audit_ingestion_outbox" ("status", "processed_at", "updated_at")
  WHERE "status" IN ('completed', 'failed');

ALTER TABLE "deltallm_organizationtable"
  ADD COLUMN IF NOT EXISTS "audit_content_policy_version" BIGINT NOT NULL DEFAULT 0;

ALTER TABLE "deltallm_promptrenderlog"
  ADD COLUMN IF NOT EXISTS "variables_redacted" BOOLEAN NOT NULL DEFAULT FALSE;
