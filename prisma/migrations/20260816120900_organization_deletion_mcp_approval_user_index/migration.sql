CREATE INDEX CONCURRENTLY IF NOT EXISTS "deltallm_mcpapproval_user_created_idx"
  ON "deltallm_mcpapprovalrequest" ("requested_by_user", "created_at");
