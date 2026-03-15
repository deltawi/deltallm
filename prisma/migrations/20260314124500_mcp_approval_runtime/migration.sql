ALTER TABLE "deltallm_mcpapprovalrequest"
ADD COLUMN "expires_at" TIMESTAMP(3);

UPDATE "deltallm_mcpapprovalrequest"
SET "expires_at" = CASE
    WHEN "status" = 'pending' THEN COALESCE("created_at", NOW()) + INTERVAL '24 hours'
    WHEN "status" IN ('approved', 'rejected') THEN COALESCE("decided_at", "created_at", NOW()) + INTERVAL '15 minutes'
    ELSE COALESCE("created_at", NOW())
END
WHERE "expires_at" IS NULL;

WITH ranked AS (
    SELECT
        "mcp_approval_request_id",
        ROW_NUMBER() OVER (
            PARTITION BY "request_fingerprint"
            ORDER BY "created_at" DESC, "mcp_approval_request_id" DESC
        ) AS row_num
    FROM "deltallm_mcpapprovalrequest"
    WHERE "status" = 'pending'
)
UPDATE "deltallm_mcpapprovalrequest" r
SET "status" = 'expired',
    "updated_at" = NOW()
FROM ranked
WHERE r."mcp_approval_request_id" = ranked."mcp_approval_request_id"
  AND ranked.row_num > 1;

ALTER TABLE "deltallm_mcpapprovalrequest"
ADD CONSTRAINT "deltallm_mcpapprovalrequest_status_chk"
CHECK ("status" IN ('pending', 'approved', 'rejected', 'expired'));

CREATE INDEX "deltallm_mcpapproval_fingerprint_status_expires_idx"
ON "deltallm_mcpapprovalrequest"("request_fingerprint", "status", "expires_at");

CREATE UNIQUE INDEX "deltallm_mcpapproval_pending_fingerprint_uniq"
ON "deltallm_mcpapprovalrequest"("request_fingerprint")
WHERE "status" = 'pending';
