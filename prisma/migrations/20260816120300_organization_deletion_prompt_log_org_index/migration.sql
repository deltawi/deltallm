CREATE INDEX CONCURRENTLY IF NOT EXISTS "deltallm_promptrenderlog_org_created_idx"
  ON "deltallm_promptrenderlog" ("organization_id", "created_at");
