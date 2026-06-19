CREATE TABLE IF NOT EXISTS "deltallm_cacheinvalidationoutbox" (
  "invalidation_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "scope_type" TEXT NOT NULL,
  "scope_id" TEXT NOT NULL,
  "reason" TEXT NOT NULL,
  "metadata" JSONB,
  "status" TEXT NOT NULL DEFAULT 'pending',
  "attempt_count" INTEGER NOT NULL DEFAULT 0,
  "max_attempts" INTEGER NOT NULL DEFAULT 10,
  "next_attempt_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "last_error" TEXT,
  "locked_by" TEXT,
  "lease_expires_at" TIMESTAMP(3),
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "processed_at" TIMESTAMP(3),

  CONSTRAINT "deltallm_cacheinvalidationoutbox_pkey" PRIMARY KEY ("invalidation_id"),
  CONSTRAINT "deltallm_cacheinvalidationoutbox_scope_type_check"
    CHECK ("scope_type" IN ('organization', 'team', 'user', 'key_hash')),
  CONSTRAINT "deltallm_cacheinvalidationoutbox_status_check"
    CHECK ("status" IN ('pending', 'processing', 'completed', 'failed', 'superseded')),
  CONSTRAINT "deltallm_cacheinvalidationoutbox_attempt_count_check"
    CHECK ("attempt_count" >= 0),
  CONSTRAINT "deltallm_cacheinvalidationoutbox_max_attempts_check"
    CHECK ("max_attempts" > 0)
);

ALTER TABLE "deltallm_cacheinvalidationoutbox"
  DROP CONSTRAINT IF EXISTS "deltallm_cacheinvalidationoutbox_status_check";

ALTER TABLE "deltallm_cacheinvalidationoutbox"
  ADD CONSTRAINT "deltallm_cacheinvalidationoutbox_status_check"
  CHECK ("status" IN ('pending', 'processing', 'completed', 'failed', 'superseded'));

DROP INDEX IF EXISTS "deltallm_cacheinvalidationoutbox_pending_scope_reason_key";

CREATE UNIQUE INDEX IF NOT EXISTS "deltallm_cacheinvalidationoutbox_pending_scope_reason_key"
  ON "deltallm_cacheinvalidationoutbox" ("scope_type", "scope_id", "reason")
  WHERE "status" = 'pending';

CREATE INDEX IF NOT EXISTS "deltallm_cacheinvalidationoutbox_status_next_attempt_idx"
  ON "deltallm_cacheinvalidationoutbox" ("status", "next_attempt_at");

CREATE INDEX IF NOT EXISTS "deltallm_cacheinvalidationoutbox_lease_expires_idx"
  ON "deltallm_cacheinvalidationoutbox" ("lease_expires_at");

CREATE INDEX IF NOT EXISTS "deltallm_cacheinvalidationoutbox_scope_idx"
  ON "deltallm_cacheinvalidationoutbox" ("scope_type", "scope_id");
