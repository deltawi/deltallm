CREATE INDEX CONCURRENTLY IF NOT EXISTS "deltallm_mcpapproval_org_status_created_idx"
  ON "deltallm_mcpapprovalrequest" ("organization_id", "status", "created_at");
