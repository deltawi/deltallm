CREATE TABLE "deltallm_serviceaccount" (
  "service_account_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "team_id" TEXT NOT NULL,
  "name" TEXT NOT NULL,
  "description" TEXT,
  "is_active" BOOLEAN NOT NULL DEFAULT true,
  "metadata" JSONB,
  "created_by_account_id" TEXT,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "deltallm_serviceaccount_pkey" PRIMARY KEY ("service_account_id"),
  CONSTRAINT "deltallm_serviceaccount_team_id_fkey"
    FOREIGN KEY ("team_id") REFERENCES "deltallm_teamtable"("team_id")
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT "deltallm_serviceaccount_created_by_account_id_fkey"
    FOREIGN KEY ("created_by_account_id") REFERENCES "deltallm_platformaccount"("account_id")
    ON DELETE SET NULL ON UPDATE CASCADE
);

CREATE UNIQUE INDEX "deltallm_serviceaccount_team_id_name_key"
  ON "deltallm_serviceaccount"("team_id", "name");
CREATE INDEX "deltallm_serviceaccount_team_id_idx"
  ON "deltallm_serviceaccount"("team_id");
CREATE INDEX "deltallm_serviceaccount_created_by_account_id_idx"
  ON "deltallm_serviceaccount"("created_by_account_id");

ALTER TABLE "deltallm_verificationtoken"
  ADD COLUMN "owner_account_id" TEXT,
  ADD COLUMN "owner_service_account_id" TEXT;

ALTER TABLE "deltallm_verificationtoken"
  ADD CONSTRAINT "deltallm_verificationtoken_owner_account_id_fkey"
    FOREIGN KEY ("owner_account_id") REFERENCES "deltallm_platformaccount"("account_id")
    ON DELETE SET NULL ON UPDATE CASCADE,
  ADD CONSTRAINT "deltallm_verificationtoken_owner_service_account_id_fkey"
    FOREIGN KEY ("owner_service_account_id") REFERENCES "deltallm_serviceaccount"("service_account_id")
    ON DELETE SET NULL ON UPDATE CASCADE;

CREATE INDEX "deltallm_verificationtoken_owner_account_id_idx"
  ON "deltallm_verificationtoken"("owner_account_id");
CREATE INDEX "deltallm_verificationtoken_owner_service_account_id_idx"
  ON "deltallm_verificationtoken"("owner_service_account_id");

ALTER TABLE "deltallm_verificationtoken"
  ADD CONSTRAINT "deltallm_verificationtoken_owner_at_most_one_chk"
  CHECK (
    (
      CASE WHEN "owner_account_id" IS NOT NULL THEN 1 ELSE 0 END +
      CASE WHEN "owner_service_account_id" IS NOT NULL THEN 1 ELSE 0 END
    ) <= 1
  );

UPDATE "deltallm_verificationtoken" vt
SET "owner_account_id" = pa."account_id"
FROM "deltallm_platformaccount" pa
WHERE vt."owner_account_id" IS NULL
  AND vt."owner_service_account_id" IS NULL
  AND vt."user_id" = pa."account_id";
