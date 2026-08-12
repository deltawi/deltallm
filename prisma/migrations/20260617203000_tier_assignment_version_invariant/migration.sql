ALTER TABLE "deltallm_tierversion"
  ADD CONSTRAINT "deltallm_tierversion_id_tier_key"
  UNIQUE ("tier_version_id", "tier_id");

ALTER TABLE "deltallm_organizationtierassignment"
  DROP CONSTRAINT "deltallm_orgtierassignment_tier_version_id_fkey";

ALTER TABLE "deltallm_organizationtierassignment"
  ADD CONSTRAINT "deltallm_orgtierassignment_version_matches_tier_fkey"
  FOREIGN KEY ("tier_version_id", "tier_id")
  REFERENCES "deltallm_tierversion"("tier_version_id", "tier_id")
  ON DELETE RESTRICT
  ON UPDATE CASCADE;
