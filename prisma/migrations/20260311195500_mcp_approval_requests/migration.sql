CREATE TABLE "deltallm_mcpapprovalrequest" (
    "mcp_approval_request_id" TEXT NOT NULL,
    "mcp_server_id" TEXT NOT NULL,
    "tool_name" TEXT NOT NULL,
    "scope_type" TEXT NOT NULL,
    "scope_id" TEXT NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'pending',
    "request_fingerprint" TEXT NOT NULL,
    "requested_by_api_key" TEXT,
    "requested_by_user" TEXT,
    "organization_id" TEXT,
    "request_id" TEXT,
    "correlation_id" TEXT,
    "arguments_json" JSONB NOT NULL,
    "decision_comment" TEXT,
    "decided_by_account_id" TEXT,
    "decided_at" TIMESTAMP(3),
    "metadata" JSONB,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "deltallm_mcpapprovalrequest_pkey" PRIMARY KEY ("mcp_approval_request_id")
);

CREATE INDEX "deltallm_mcpapproval_server_status_created_idx"
ON "deltallm_mcpapprovalrequest"("mcp_server_id", "status", "created_at");

CREATE INDEX "deltallm_mcpapproval_status_created_idx"
ON "deltallm_mcpapprovalrequest"("status", "created_at");

CREATE INDEX "deltallm_mcpapproval_fingerprint_idx"
ON "deltallm_mcpapprovalrequest"("request_fingerprint");

ALTER TABLE "deltallm_mcpapprovalrequest"
ADD CONSTRAINT "deltallm_mcpapprovalrequest_mcp_server_id_fkey"
FOREIGN KEY ("mcp_server_id") REFERENCES "deltallm_mcpserver"("mcp_server_id")
ON DELETE CASCADE ON UPDATE CASCADE;
