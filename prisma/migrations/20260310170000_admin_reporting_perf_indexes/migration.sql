CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Large append-only spend log table: keep write overhead low with BRIN on time.
CREATE INDEX IF NOT EXISTS "deltallm_spendlogs_start_time_brin_idx"
  ON "deltallm_spendlogs" USING BRIN ("start_time");

-- Admin reporting joins/searches hit these smaller lookup tables frequently.
CREATE INDEX IF NOT EXISTS "deltallm_teamtable_team_alias_trgm_idx"
  ON "deltallm_teamtable" USING GIN ("team_alias" gin_trgm_ops);

CREATE INDEX IF NOT EXISTS "deltallm_organizationtable_name_trgm_idx"
  ON "deltallm_organizationtable" USING GIN ("organization_name" gin_trgm_ops);

CREATE INDEX IF NOT EXISTS "deltallm_verificationtoken_key_name_trgm_idx"
  ON "deltallm_verificationtoken" USING GIN ("key_name" gin_trgm_ops);
