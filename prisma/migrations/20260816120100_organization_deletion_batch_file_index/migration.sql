CREATE INDEX CONCURRENTLY IF NOT EXISTS "deltallm_batch_file_org_created_idx"
  ON "deltallm_batch_file" ("created_by_organization_id", "created_at");
