-- Expand the email outbox with fenced delivery ownership and recoverable
-- required-audit replay metadata. Existing rows are reconciled by the bounded
-- worker; this migration deliberately performs no table-wide data update.
ALTER TABLE "deltallm_emailoutbox"
  ADD COLUMN IF NOT EXISTS "delivery_locked_by" TEXT,
  ADD COLUMN IF NOT EXISTS "delivery_claim_token" TEXT,
  ADD COLUMN IF NOT EXISTS "delivery_lease_expires_at" TIMESTAMP(3),
  ADD COLUMN IF NOT EXISTS "delivery_started_at" TIMESTAMP(3),
  ADD COLUMN IF NOT EXISTS "delivery_blocked_at" TIMESTAMP(3),
  ADD COLUMN IF NOT EXISTS "delivery_audit_claim_token" TEXT,
  ADD COLUMN IF NOT EXISTS "delivery_audit_blocked_at" TIMESTAMP(3),
  ADD COLUMN IF NOT EXISTS "delivery_audit_replay_count" INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS "delivery_audit_last_replayed_at" TIMESTAMP(3),
  ADD COLUMN IF NOT EXISTS "delivery_audit_last_replayed_by" TEXT;

CREATE INDEX IF NOT EXISTS "deltallm_emailoutbox_delivery_lease_idx"
  ON "deltallm_emailoutbox" ("status", "delivery_lease_expires_at");

ALTER TABLE "deltallm_emailoutbox"
  DROP CONSTRAINT IF EXISTS "deltallm_emailoutbox_delivery_audit_status_check";

ALTER TABLE "deltallm_emailoutbox"
  ADD CONSTRAINT "deltallm_emailoutbox_delivery_audit_status_check"
  CHECK ("delivery_audit_status" IN (
    'not_required', 'waiting', 'pending', 'processing', 'retrying', 'persisted', 'blocked'
  )) NOT VALID;
