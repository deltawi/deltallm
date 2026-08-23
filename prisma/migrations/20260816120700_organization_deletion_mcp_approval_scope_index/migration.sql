CREATE INDEX CONCURRENTLY IF NOT EXISTS "deltallm_mcpapproval_scope_status_created_idx"
  ON "deltallm_mcpapprovalrequest" (
    "scope_type",
    "scope_id",
    "status",
    "created_at"
  );
