CREATE INDEX CONCURRENTLY IF NOT EXISTS "deltallm_promptrenderlog_user_created_idx"
  ON "deltallm_promptrenderlog" ("user_id", "created_at");
