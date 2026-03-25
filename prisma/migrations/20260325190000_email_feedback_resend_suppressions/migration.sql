CREATE TABLE IF NOT EXISTS "deltallm_emailwebhookevent" (
  "webhook_event_id" TEXT NOT NULL,
  "provider" TEXT NOT NULL,
  "event_type" TEXT NOT NULL,
  "recipient_address" TEXT,
  "provider_message_id" TEXT,
  "email_id" TEXT,
  "payload_json" JSONB,
  "occurred_at" TIMESTAMPTZ,
  "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  "updated_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT "deltallm_emailwebhookevent_pkey" PRIMARY KEY ("webhook_event_id")
);

CREATE INDEX IF NOT EXISTS "deltallm_emailwebhookevent_provider_msg_idx"
  ON "deltallm_emailwebhookevent" ("provider", "provider_message_id");

CREATE INDEX IF NOT EXISTS "deltallm_emailwebhookevent_occurred_idx"
  ON "deltallm_emailwebhookevent" ("occurred_at");

CREATE TABLE IF NOT EXISTS "deltallm_emailsuppression" (
  "email_address" TEXT NOT NULL,
  "provider" TEXT NOT NULL,
  "reason" TEXT NOT NULL,
  "source" TEXT NOT NULL,
  "provider_message_id" TEXT,
  "webhook_event_id" TEXT,
  "metadata" JSONB,
  "first_seen_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  "last_seen_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  "updated_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT "deltallm_emailsuppression_pkey" PRIMARY KEY ("email_address")
);

CREATE INDEX IF NOT EXISTS "deltallm_emailsuppression_last_seen_idx"
  ON "deltallm_emailsuppression" ("last_seen_at");

CREATE INDEX IF NOT EXISTS "deltallm_emailsuppression_provider_reason_idx"
  ON "deltallm_emailsuppression" ("provider", "reason");
