CREATE INDEX CONCURRENTLY IF NOT EXISTS "deltallm_mcpapproval_api_key_created_idx"
  ON "deltallm_mcpapprovalrequest" ("requested_by_api_key", "created_at");
