ALTER TABLE "deltallm_batch_webhook_outbox"
ADD COLUMN "created_by_team_id" TEXT,
ADD COLUMN "created_by_organization_id" TEXT;

UPDATE "deltallm_batch_webhook_outbox" AS webhook
SET "created_by_team_id" = job."created_by_team_id",
    "created_by_organization_id" = job."created_by_organization_id"
FROM "deltallm_batch_job" AS job
WHERE job."batch_id" = webhook."batch_id";

CREATE INDEX "deltallm_batch_webhook_outbox_retention_idx"
ON "deltallm_batch_webhook_outbox"("status", "updated_at");
