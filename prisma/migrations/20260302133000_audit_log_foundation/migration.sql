ALTER TABLE "deltallm_organizationtable"
ADD COLUMN IF NOT EXISTS "audit_content_storage_enabled" BOOLEAN NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS "deltallm_auditevent" (
  "event_id" UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  "occurred_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  "organization_id" TEXT,
  "actor_type" TEXT,
  "actor_id" TEXT,
  "api_key" TEXT,
  "action" TEXT NOT NULL,
  "resource_type" TEXT,
  "resource_id" TEXT,
  "request_id" TEXT,
  "correlation_id" TEXT,
  "ip" TEXT,
  "user_agent" TEXT,
  "status" TEXT,
  "latency_ms" INTEGER,
  "input_tokens" INTEGER,
  "output_tokens" INTEGER,
  "error_type" TEXT,
  "error_code" TEXT,
  "metadata" JSONB,
  "content_stored" BOOLEAN NOT NULL DEFAULT false,
  "prev_hash" TEXT,
  "event_hash" TEXT
);

CREATE TABLE IF NOT EXISTS "deltallm_auditpayload" (
  "payload_id" UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  "event_id" UUID NOT NULL,
  "kind" TEXT NOT NULL,
  "storage_mode" TEXT NOT NULL DEFAULT 'inline',
  "content_json" JSONB,
  "storage_uri" TEXT,
  "content_sha256" TEXT,
  "size_bytes" INTEGER,
  "redacted" BOOLEAN NOT NULL DEFAULT false,
  "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT "deltallm_auditpayload_event_fk"
    FOREIGN KEY ("event_id") REFERENCES "deltallm_auditevent"("event_id")
    ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS "deltallm_auditevent_occurred_at_idx" ON "deltallm_auditevent" ("occurred_at");
CREATE INDEX IF NOT EXISTS "deltallm_auditevent_organization_id_idx" ON "deltallm_auditevent" ("organization_id");
CREATE INDEX IF NOT EXISTS "deltallm_auditevent_actor_id_idx" ON "deltallm_auditevent" ("actor_id");
CREATE INDEX IF NOT EXISTS "deltallm_auditevent_action_idx" ON "deltallm_auditevent" ("action");
CREATE INDEX IF NOT EXISTS "deltallm_auditevent_request_id_idx" ON "deltallm_auditevent" ("request_id");
CREATE INDEX IF NOT EXISTS "deltallm_auditevent_correlation_id_idx" ON "deltallm_auditevent" ("correlation_id");
CREATE INDEX IF NOT EXISTS "deltallm_auditpayload_event_id_idx" ON "deltallm_auditpayload" ("event_id");
CREATE INDEX IF NOT EXISTS "deltallm_auditpayload_kind_idx" ON "deltallm_auditpayload" ("kind");
CREATE INDEX IF NOT EXISTS "deltallm_auditpayload_created_at_idx" ON "deltallm_auditpayload" ("created_at");
