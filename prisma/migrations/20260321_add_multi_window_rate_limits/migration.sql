ALTER TABLE "deltallm_verificationtoken" ADD COLUMN IF NOT EXISTS "rph_limit" INTEGER;
ALTER TABLE "deltallm_verificationtoken" ADD COLUMN IF NOT EXISTS "rpd_limit" INTEGER;
ALTER TABLE "deltallm_verificationtoken" ADD COLUMN IF NOT EXISTS "tpd_limit" INTEGER;

ALTER TABLE "deltallm_usertable" ADD COLUMN IF NOT EXISTS "rph_limit" INTEGER;
ALTER TABLE "deltallm_usertable" ADD COLUMN IF NOT EXISTS "rpd_limit" INTEGER;
ALTER TABLE "deltallm_usertable" ADD COLUMN IF NOT EXISTS "tpd_limit" INTEGER;

ALTER TABLE "deltallm_teamtable" ADD COLUMN IF NOT EXISTS "rph_limit" INTEGER;
ALTER TABLE "deltallm_teamtable" ADD COLUMN IF NOT EXISTS "rpd_limit" INTEGER;
ALTER TABLE "deltallm_teamtable" ADD COLUMN IF NOT EXISTS "tpd_limit" INTEGER;

ALTER TABLE "deltallm_organizationtable" ADD COLUMN IF NOT EXISTS "rph_limit" INTEGER;
ALTER TABLE "deltallm_organizationtable" ADD COLUMN IF NOT EXISTS "rpd_limit" INTEGER;
ALTER TABLE "deltallm_organizationtable" ADD COLUMN IF NOT EXISTS "tpd_limit" INTEGER;
