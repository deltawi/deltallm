-- Expand-only rolling-deploy compatibility. Existing binary-float columns
-- remain populated until a separately supervised, bounded backfill and
-- contract release can remove them.
ALTER TABLE "deltallm_spendlog_events"
  ADD COLUMN IF NOT EXISTS "spend_exact" NUMERIC(38,18),
  ADD COLUMN IF NOT EXISTS "provider_cost_exact" NUMERIC(38,18);

ALTER TABLE "deltallm_teammodelspend"
  ADD COLUMN IF NOT EXISTS "spend_exact" NUMERIC(38,18);

ALTER TABLE "deltallm_verificationtoken"
  ADD COLUMN IF NOT EXISTS "spend_exact" NUMERIC(38,18);

ALTER TABLE "deltallm_usertable"
  ADD COLUMN IF NOT EXISTS "spend_exact" NUMERIC(38,18);

ALTER TABLE "deltallm_teamtable"
  ADD COLUMN IF NOT EXISTS "spend_exact" NUMERIC(38,18);

ALTER TABLE "deltallm_organizationtable"
  ADD COLUMN IF NOT EXISTS "spend_exact" NUMERIC(38,18);
