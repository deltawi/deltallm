CREATE TABLE IF NOT EXISTS "deltallm_platforminvitation" (
  "invitation_id" TEXT NOT NULL,
  "account_id" TEXT NOT NULL,
  "email" TEXT NOT NULL,
  "status" TEXT NOT NULL DEFAULT 'pending',
  "invite_scope_type" TEXT NOT NULL,
  "invited_by_account_id" TEXT,
  "message_email_id" TEXT,
  "expires_at" TIMESTAMPTZ NOT NULL,
  "accepted_at" TIMESTAMPTZ,
  "cancelled_at" TIMESTAMPTZ,
  "metadata" JSONB,
  "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  "updated_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT "deltallm_platforminvitation_pkey" PRIMARY KEY ("invitation_id"),
  CONSTRAINT "deltallm_platforminvitation_account_id_fkey"
    FOREIGN KEY ("account_id")
    REFERENCES "deltallm_platformaccount"("account_id")
    ON DELETE CASCADE
    ON UPDATE CASCADE,
  CONSTRAINT "deltallm_platforminvitation_invited_by_account_id_fkey"
    FOREIGN KEY ("invited_by_account_id")
    REFERENCES "deltallm_platformaccount"("account_id")
    ON DELETE SET NULL
    ON UPDATE CASCADE,
  CONSTRAINT "deltallm_platforminvitation_message_email_id_fkey"
    FOREIGN KEY ("message_email_id")
    REFERENCES "deltallm_emailoutbox"("email_id")
    ON DELETE SET NULL
    ON UPDATE CASCADE
);

CREATE INDEX IF NOT EXISTS "deltallm_platforminvitation_account_idx"
  ON "deltallm_platforminvitation" ("account_id");

CREATE INDEX IF NOT EXISTS "deltallm_platforminvitation_status_expires_idx"
  ON "deltallm_platforminvitation" ("status", "expires_at");

CREATE INDEX IF NOT EXISTS "deltallm_platforminvitation_email_idx"
  ON "deltallm_platforminvitation" ("email");

CREATE INDEX IF NOT EXISTS "deltallm_platforminvitation_invited_by_idx"
  ON "deltallm_platforminvitation" ("invited_by_account_id");

CREATE TABLE IF NOT EXISTS "deltallm_emailtoken" (
  "token_id" TEXT NOT NULL,
  "purpose" TEXT NOT NULL,
  "token_hash" TEXT NOT NULL,
  "account_id" TEXT NOT NULL,
  "email" TEXT NOT NULL,
  "invitation_id" TEXT,
  "expires_at" TIMESTAMPTZ NOT NULL,
  "consumed_at" TIMESTAMPTZ,
  "created_by_account_id" TEXT,
  "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  "updated_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT "deltallm_emailtoken_pkey" PRIMARY KEY ("token_id"),
  CONSTRAINT "deltallm_emailtoken_account_id_fkey"
    FOREIGN KEY ("account_id")
    REFERENCES "deltallm_platformaccount"("account_id")
    ON DELETE CASCADE
    ON UPDATE CASCADE,
  CONSTRAINT "deltallm_emailtoken_invitation_id_fkey"
    FOREIGN KEY ("invitation_id")
    REFERENCES "deltallm_platforminvitation"("invitation_id")
    ON DELETE CASCADE
    ON UPDATE CASCADE,
  CONSTRAINT "deltallm_emailtoken_created_by_account_id_fkey"
    FOREIGN KEY ("created_by_account_id")
    REFERENCES "deltallm_platformaccount"("account_id")
    ON DELETE SET NULL
    ON UPDATE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS "deltallm_emailtoken_token_hash_key"
  ON "deltallm_emailtoken" ("token_hash");

CREATE INDEX IF NOT EXISTS "deltallm_emailtoken_purpose_expires_idx"
  ON "deltallm_emailtoken" ("purpose", "expires_at");

CREATE INDEX IF NOT EXISTS "deltallm_emailtoken_account_idx"
  ON "deltallm_emailtoken" ("account_id");

CREATE INDEX IF NOT EXISTS "deltallm_emailtoken_invitation_idx"
  ON "deltallm_emailtoken" ("invitation_id");
