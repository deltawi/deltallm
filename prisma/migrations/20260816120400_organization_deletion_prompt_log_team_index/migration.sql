CREATE INDEX CONCURRENTLY IF NOT EXISTS "deltallm_promptrenderlog_team_created_idx"
  ON "deltallm_promptrenderlog" ("team_id", "created_at");
