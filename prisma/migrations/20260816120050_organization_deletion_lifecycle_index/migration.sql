CREATE INDEX CONCURRENTLY IF NOT EXISTS "deltallm_organizationtable_lifecycle_state_idx"
  ON "deltallm_organizationtable" ("lifecycle_state", "updated_at");
