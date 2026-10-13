-- An approved MCP tool call request now allows one execution.
-- The gateway sets consumed_at when it uses the approval.
ALTER TABLE "deltallm_mcpapprovalrequest"
ADD COLUMN "consumed_at" TIMESTAMP(3);
