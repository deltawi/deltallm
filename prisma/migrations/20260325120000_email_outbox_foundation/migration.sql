CREATE TABLE IF NOT EXISTS "deltallm_emailoutbox" (
  "email_id" TEXT NOT NULL,
  "kind" TEXT NOT NULL,
  "provider" TEXT NOT NULL,
  "to_addresses" TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  "cc_addresses" TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  "bcc_addresses" TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  "from_address" TEXT NOT NULL,
  "reply_to" TEXT,
  "template_key" TEXT,
  "payload_json" JSONB,
  "subject" TEXT NOT NULL,
  "text_body" TEXT NOT NULL,
  "html_body" TEXT,
  "status" TEXT NOT NULL DEFAULT 'queued',
  "attempt_count" INTEGER NOT NULL DEFAULT 0,
  "max_attempts" INTEGER NOT NULL DEFAULT 5,
  "next_attempt_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  "last_error" TEXT,
  "last_provider_message_id" TEXT,
  "created_by_account_id" TEXT,
  "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  "updated_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  "sent_at" TIMESTAMPTZ,
  CONSTRAINT "deltallm_emailoutbox_pkey" PRIMARY KEY ("email_id"),
  CONSTRAINT "deltallm_emailoutbox_created_by_account_id_fkey"
    FOREIGN KEY ("created_by_account_id")
    REFERENCES "deltallm_platformaccount"("account_id")
    ON DELETE SET NULL
    ON UPDATE CASCADE
);

CREATE INDEX IF NOT EXISTS "deltallm_emailoutbox_status_next_attempt_idx"
  ON "deltallm_emailoutbox" ("status", "next_attempt_at");

CREATE INDEX IF NOT EXISTS "deltallm_emailoutbox_created_at_idx"
  ON "deltallm_emailoutbox" ("created_at");

CREATE INDEX IF NOT EXISTS "deltallm_emailoutbox_created_by_idx"
  ON "deltallm_emailoutbox" ("created_by_account_id");

