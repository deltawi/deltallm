ALTER TABLE "deltallm_tierversion"
  ADD COLUMN "configuration_revision" INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN "created_by_account_id" TEXT,
  ADD COLUMN "created_by_kind" TEXT NOT NULL DEFAULT 'unknown',
  ADD COLUMN "source_tier_version_id" TEXT;

ALTER TABLE "deltallm_tierversion"
  ADD CONSTRAINT "deltallm_tierversion_configuration_revision_check"
  CHECK ("configuration_revision" >= 0);

ALTER TABLE "deltallm_tierversion"
  ADD CONSTRAINT "deltallm_tierversion_created_by_kind_check"
  CHECK ("created_by_kind" IN ('account', 'master_key', 'system', 'unknown'));

ALTER TABLE "deltallm_tierversion"
  ADD CONSTRAINT "deltallm_tierversion_source_not_self_check"
  CHECK (
    "source_tier_version_id" IS NULL
    OR "source_tier_version_id" <> "tier_version_id"
  );

CREATE INDEX "deltallm_tierversion_created_by_idx"
  ON "deltallm_tierversion"("created_by_account_id");

CREATE INDEX "deltallm_tierversion_source_idx"
  ON "deltallm_tierversion"("source_tier_version_id");

ALTER TABLE "deltallm_tierversion"
  ADD CONSTRAINT "deltallm_tierversion_created_by_fkey"
  FOREIGN KEY ("created_by_account_id")
  REFERENCES "deltallm_platformaccount"("account_id")
  ON DELETE SET NULL
  ON UPDATE CASCADE;

ALTER TABLE "deltallm_tierversion"
  ADD CONSTRAINT "deltallm_tierversion_source_fkey"
  FOREIGN KEY ("source_tier_version_id")
  REFERENCES "deltallm_tierversion"("tier_version_id")
  ON DELETE SET NULL
  ON UPDATE CASCADE;

CREATE TABLE "deltallm_tiercreationrequest" (
  "tier_creation_request_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "principal_scope" TEXT NOT NULL,
  "idempotency_key" TEXT NOT NULL,
  "request_hash" TEXT NOT NULL,
  "tier_id" TEXT NOT NULL,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

  CONSTRAINT "deltallm_tiercreationrequest_pkey"
    PRIMARY KEY ("tier_creation_request_id"),
  CONSTRAINT "deltallm_tiercreationrequest_principal_scope_check"
    CHECK (BTRIM("principal_scope") <> '' AND CHAR_LENGTH("principal_scope") <= 320),
  CONSTRAINT "deltallm_tiercreationrequest_idempotency_key_check"
    CHECK (BTRIM("idempotency_key") <> '' AND CHAR_LENGTH("idempotency_key") <= 200),
  CONSTRAINT "deltallm_tiercreationrequest_request_hash_check"
    CHECK (BTRIM("request_hash") <> '' AND CHAR_LENGTH("request_hash") <= 128)
);

CREATE UNIQUE INDEX "deltallm_tiercreationrequest_scope_key"
  ON "deltallm_tiercreationrequest"("principal_scope", "idempotency_key");

CREATE UNIQUE INDEX "deltallm_tiercreationrequest_tier_id_key"
  ON "deltallm_tiercreationrequest"("tier_id");

CREATE INDEX "deltallm_tiercreationrequest_created_at_idx"
  ON "deltallm_tiercreationrequest"("created_at");

ALTER TABLE "deltallm_tiercreationrequest"
  ADD CONSTRAINT "deltallm_tiercreationrequest_tier_id_fkey"
  FOREIGN KEY ("tier_id")
  REFERENCES "deltallm_tier"("tier_id")
  ON DELETE CASCADE
  ON UPDATE CASCADE;
