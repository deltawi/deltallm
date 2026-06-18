CREATE UNIQUE INDEX "deltallm_tierversion_one_active_per_tier"
  ON "deltallm_tierversion"("tier_id")
  WHERE "status" = 'active';
