ALTER TABLE "deltallm_promptversion"
ADD COLUMN IF NOT EXISTS "route_preferences" JSONB;
