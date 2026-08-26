CREATE INDEX CONCURRENTLY IF NOT EXISTS "deltallm_promptrenderlog_api_key_created_idx"
  ON "deltallm_promptrenderlog" ("api_key", "created_at");
