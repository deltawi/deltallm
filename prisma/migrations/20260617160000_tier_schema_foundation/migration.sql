CREATE TABLE "deltallm_tier" (
  "tier_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "tier_key" TEXT NOT NULL,
  "name" TEXT NOT NULL,
  "description" TEXT,
  "enabled" BOOLEAN NOT NULL DEFAULT TRUE,
  "metadata" JSONB,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

  CONSTRAINT "deltallm_tier_pkey" PRIMARY KEY ("tier_id")
);

CREATE UNIQUE INDEX "deltallm_tier_tier_key_key"
  ON "deltallm_tier"("tier_key");

CREATE INDEX "deltallm_tier_enabled_idx"
  ON "deltallm_tier"("enabled");

CREATE TABLE "deltallm_tierversion" (
  "tier_version_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "tier_id" TEXT NOT NULL,
  "version_number" INTEGER NOT NULL,
  "status" TEXT NOT NULL DEFAULT 'draft',
  "published_at" TIMESTAMP(3),
  "published_by_account_id" TEXT,
  "metadata" JSONB,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

  CONSTRAINT "deltallm_tierversion_pkey" PRIMARY KEY ("tier_version_id"),
  CONSTRAINT "deltallm_tierversion_status_check"
    CHECK ("status" IN ('draft', 'active', 'archived'))
);

CREATE UNIQUE INDEX "deltallm_tierversion_tier_version_key"
  ON "deltallm_tierversion"("tier_id", "version_number");

CREATE INDEX "deltallm_tierversion_tier_status_idx"
  ON "deltallm_tierversion"("tier_id", "status");

CREATE INDEX "deltallm_tierversion_status_idx"
  ON "deltallm_tierversion"("status");

CREATE INDEX "deltallm_tierversion_published_by_idx"
  ON "deltallm_tierversion"("published_by_account_id");

CREATE TABLE "deltallm_tiermodelpolicy" (
  "tier_model_policy_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "tier_version_id" TEXT NOT NULL,
  "callable_key" TEXT NOT NULL,
  "enabled" BOOLEAN NOT NULL DEFAULT TRUE,
  "access_mode" TEXT NOT NULL DEFAULT 'allow',
  "rpm_limit" INTEGER,
  "tpm_limit" INTEGER,
  "rph_limit" INTEGER,
  "rpd_limit" INTEGER,
  "tpd_limit" INTEGER,
  "max_parallel_requests" INTEGER,
  "batch_rpm_limit" INTEGER,
  "batch_tpm_limit" INTEGER,
  "pricing" JSONB,
  "capacity_pool_key" TEXT,
  "priority" INTEGER NOT NULL DEFAULT 0,
  "metadata" JSONB,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

  CONSTRAINT "deltallm_tiermodelpolicy_pkey" PRIMARY KEY ("tier_model_policy_id"),
  CONSTRAINT "deltallm_tiermodelpolicy_access_mode_check"
    CHECK ("access_mode" IN ('allow', 'deny')),
  CONSTRAINT "deltallm_tiermodelpolicy_rpm_limit_check"
    CHECK ("rpm_limit" IS NULL OR "rpm_limit" > 0),
  CONSTRAINT "deltallm_tiermodelpolicy_tpm_limit_check"
    CHECK ("tpm_limit" IS NULL OR "tpm_limit" > 0),
  CONSTRAINT "deltallm_tiermodelpolicy_rph_limit_check"
    CHECK ("rph_limit" IS NULL OR "rph_limit" > 0),
  CONSTRAINT "deltallm_tiermodelpolicy_rpd_limit_check"
    CHECK ("rpd_limit" IS NULL OR "rpd_limit" > 0),
  CONSTRAINT "deltallm_tiermodelpolicy_tpd_limit_check"
    CHECK ("tpd_limit" IS NULL OR "tpd_limit" > 0),
  CONSTRAINT "deltallm_tiermodelpolicy_max_parallel_check"
    CHECK ("max_parallel_requests" IS NULL OR "max_parallel_requests" > 0),
  CONSTRAINT "deltallm_tiermodelpolicy_batch_rpm_check"
    CHECK ("batch_rpm_limit" IS NULL OR "batch_rpm_limit" > 0),
  CONSTRAINT "deltallm_tiermodelpolicy_batch_tpm_check"
    CHECK ("batch_tpm_limit" IS NULL OR "batch_tpm_limit" > 0)
);

CREATE UNIQUE INDEX "deltallm_tiermodelpolicy_version_callable_key"
  ON "deltallm_tiermodelpolicy"("tier_version_id", "callable_key");

CREATE INDEX "deltallm_tiermodelpolicy_callable_key_idx"
  ON "deltallm_tiermodelpolicy"("callable_key");

CREATE INDEX "deltallm_tiermodelpolicy_version_enabled_idx"
  ON "deltallm_tiermodelpolicy"("tier_version_id", "enabled");

CREATE INDEX "deltallm_tiermodelpolicy_capacity_pool_idx"
  ON "deltallm_tiermodelpolicy"("capacity_pool_key");

CREATE TABLE "deltallm_tiercapacitypool" (
  "tier_capacity_pool_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "tier_version_id" TEXT NOT NULL,
  "pool_key" TEXT NOT NULL,
  "callable_key" TEXT NOT NULL,
  "rpm_capacity" INTEGER,
  "tpm_capacity" INTEGER,
  "max_parallel_requests" INTEGER,
  "strategy" TEXT NOT NULL DEFAULT 'hard_cap',
  "saturation_threshold" DOUBLE PRECISION,
  "burst_multiplier" DOUBLE PRECISION,
  "metadata" JSONB,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

  CONSTRAINT "deltallm_tiercapacitypool_pkey" PRIMARY KEY ("tier_capacity_pool_id"),
  CONSTRAINT "deltallm_tiercapacitypool_strategy_check"
    CHECK ("strategy" IN ('hard_cap', 'weighted_fair', 'reserved_burst')),
  CONSTRAINT "deltallm_tiercapacitypool_rpm_capacity_check"
    CHECK ("rpm_capacity" IS NULL OR "rpm_capacity" > 0),
  CONSTRAINT "deltallm_tiercapacitypool_tpm_capacity_check"
    CHECK ("tpm_capacity" IS NULL OR "tpm_capacity" > 0),
  CONSTRAINT "deltallm_tiercapacitypool_max_parallel_check"
    CHECK ("max_parallel_requests" IS NULL OR "max_parallel_requests" > 0),
  CONSTRAINT "deltallm_tiercapacitypool_saturation_check"
    CHECK ("saturation_threshold" IS NULL OR ("saturation_threshold" > 0 AND "saturation_threshold" <= 1)),
  CONSTRAINT "deltallm_tiercapacitypool_burst_multiplier_check"
    CHECK ("burst_multiplier" IS NULL OR "burst_multiplier" >= 1)
);

CREATE UNIQUE INDEX "deltallm_tiercapacitypool_version_pool_callable_key"
  ON "deltallm_tiercapacitypool"("tier_version_id", "pool_key", "callable_key");

CREATE INDEX "deltallm_tiercapacitypool_version_pool_idx"
  ON "deltallm_tiercapacitypool"("tier_version_id", "pool_key");

CREATE INDEX "deltallm_tiercapacitypool_callable_key_idx"
  ON "deltallm_tiercapacitypool"("callable_key");

CREATE TABLE "deltallm_organizationtierassignment" (
  "assignment_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "organization_id" TEXT NOT NULL,
  "tier_id" TEXT NOT NULL,
  "tier_version_id" TEXT,
  "assignment_type" TEXT NOT NULL DEFAULT 'primary',
  "enabled" BOOLEAN NOT NULL DEFAULT TRUE,
  "weight" INTEGER NOT NULL DEFAULT 1,
  "starts_at" TIMESTAMP(3),
  "ends_at" TIMESTAMP(3),
  "metadata" JSONB,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

  CONSTRAINT "deltallm_organizationtierassignment_pkey" PRIMARY KEY ("assignment_id"),
  CONSTRAINT "deltallm_organizationtierassignment_type_check"
    CHECK ("assignment_type" IN ('primary', 'addon', 'override')),
  CONSTRAINT "deltallm_organizationtierassignment_weight_check"
    CHECK ("weight" > 0),
  CONSTRAINT "deltallm_organizationtierassignment_effective_window_check"
    CHECK ("starts_at" IS NULL OR "ends_at" IS NULL OR "starts_at" < "ends_at")
);

CREATE INDEX "deltallm_orgtierassignment_org_enabled_idx"
  ON "deltallm_organizationtierassignment"("organization_id", "enabled");

CREATE INDEX "deltallm_orgtierassignment_org_type_enabled_idx"
  ON "deltallm_organizationtierassignment"("organization_id", "assignment_type", "enabled");

CREATE INDEX "deltallm_orgtierassignment_tier_enabled_idx"
  ON "deltallm_organizationtierassignment"("tier_id", "enabled");

CREATE INDEX "deltallm_orgtierassignment_tier_version_idx"
  ON "deltallm_organizationtierassignment"("tier_version_id");

ALTER TABLE "deltallm_tierversion"
  ADD CONSTRAINT "deltallm_tierversion_tier_id_fkey"
  FOREIGN KEY ("tier_id")
  REFERENCES "deltallm_tier"("tier_id")
  ON DELETE CASCADE
  ON UPDATE CASCADE;

ALTER TABLE "deltallm_tierversion"
  ADD CONSTRAINT "deltallm_tierversion_published_by_fkey"
  FOREIGN KEY ("published_by_account_id")
  REFERENCES "deltallm_platformaccount"("account_id")
  ON DELETE SET NULL
  ON UPDATE CASCADE;

ALTER TABLE "deltallm_tiermodelpolicy"
  ADD CONSTRAINT "deltallm_tiermodelpolicy_tier_version_id_fkey"
  FOREIGN KEY ("tier_version_id")
  REFERENCES "deltallm_tierversion"("tier_version_id")
  ON DELETE CASCADE
  ON UPDATE CASCADE;

ALTER TABLE "deltallm_tiercapacitypool"
  ADD CONSTRAINT "deltallm_tiercapacitypool_tier_version_id_fkey"
  FOREIGN KEY ("tier_version_id")
  REFERENCES "deltallm_tierversion"("tier_version_id")
  ON DELETE CASCADE
  ON UPDATE CASCADE;

ALTER TABLE "deltallm_organizationtierassignment"
  ADD CONSTRAINT "deltallm_orgtierassignment_organization_id_fkey"
  FOREIGN KEY ("organization_id")
  REFERENCES "deltallm_organizationtable"("organization_id")
  ON DELETE CASCADE
  ON UPDATE CASCADE;

ALTER TABLE "deltallm_organizationtierassignment"
  ADD CONSTRAINT "deltallm_orgtierassignment_tier_id_fkey"
  FOREIGN KEY ("tier_id")
  REFERENCES "deltallm_tier"("tier_id")
  ON DELETE RESTRICT
  ON UPDATE CASCADE;

ALTER TABLE "deltallm_organizationtierassignment"
  ADD CONSTRAINT "deltallm_orgtierassignment_tier_version_id_fkey"
  FOREIGN KEY ("tier_version_id")
  REFERENCES "deltallm_tierversion"("tier_version_id")
  ON DELETE SET NULL
  ON UPDATE CASCADE;
