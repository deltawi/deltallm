CREATE TABLE IF NOT EXISTS "deltallm_mastersession" (
  "session_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "token_hash" TEXT NOT NULL,
  "master_key_fingerprint" TEXT NOT NULL,
  "expires_at" TIMESTAMP(3) NOT NULL,
  "revoked_at" TIMESTAMP(3),
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "last_seen_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "deltallm_mastersession_pkey" PRIMARY KEY ("session_id")
);

CREATE UNIQUE INDEX IF NOT EXISTS "deltallm_mastersession_token_hash_key"
  ON "deltallm_mastersession" ("token_hash");

CREATE INDEX IF NOT EXISTS "deltallm_mastersession_expires_at_idx"
  ON "deltallm_mastersession" ("expires_at");
