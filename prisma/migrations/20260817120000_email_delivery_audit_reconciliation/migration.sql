ALTER TABLE "deltallm_emailoutbox"
  ADD COLUMN IF NOT EXISTS "delivery_audit_status" TEXT NOT NULL DEFAULT 'not_required',
  ADD COLUMN IF NOT EXISTS "delivery_audit_event_id" TEXT,
  ADD COLUMN IF NOT EXISTS "delivery_audit_attempt_count" INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS "delivery_audit_max_attempts" INTEGER NOT NULL DEFAULT 10,
  ADD COLUMN IF NOT EXISTS "delivery_audit_next_attempt_at" TIMESTAMP(3),
  ADD COLUMN IF NOT EXISTS "delivery_audit_last_error" TEXT,
  ADD COLUMN IF NOT EXISTS "delivery_audit_locked_by" TEXT,
  ADD COLUMN IF NOT EXISTS "delivery_audit_lease_expires_at" TIMESTAMP(3),
  ADD COLUMN IF NOT EXISTS "delivery_audited_at" TIMESTAMP(3);

CREATE INDEX IF NOT EXISTS "deltallm_emailoutbox_delivery_audit_due_idx"
  ON "deltallm_emailoutbox" ("delivery_audit_status", "delivery_audit_next_attempt_at");

ALTER TABLE "deltallm_emailoutbox"
  DROP CONSTRAINT IF EXISTS "deltallm_emailoutbox_delivery_audit_status_check";

ALTER TABLE "deltallm_emailoutbox"
  ADD CONSTRAINT "deltallm_emailoutbox_delivery_audit_status_check"
  CHECK ("delivery_audit_status" IN (
    'not_required', 'pending', 'processing', 'retrying', 'persisted', 'failed'
  )) NOT VALID;
