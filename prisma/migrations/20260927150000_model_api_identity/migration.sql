-- Separate the human-friendly model label from the immutable callable model id.
-- Both columns remain nullable during the additive rollout so older binaries can
-- continue writing rows safely while the fleet is upgraded.
ALTER TABLE "deltallm_platformaccount"
  ADD COLUMN "api_namespace" TEXT;

CREATE UNIQUE INDEX "deltallm_platformaccount_api_namespace_ci_key"
  ON "deltallm_platformaccount" (lower("api_namespace"))
  WHERE "api_namespace" IS NOT NULL;

ALTER TABLE "deltallm_model"
  ADD COLUMN "display_name" TEXT;

UPDATE "deltallm_model" AS model
SET "display_name" = CASE
  WHEN asset."owner_account_id" IS NOT NULL
    AND model."model_name" LIKE (
      'creator-'
      || encode(digest(asset."owner_account_id", 'sha256'), 'hex')
      || '-%'
    )
  THEN substring(
    model."model_name"
    FROM length(
      'creator-'
      || encode(digest(asset."owner_account_id", 'sha256'), 'hex')
      || '-'
    ) + 1
  )
  ELSE model."model_name"
END
FROM "deltallm_managedasset" AS asset
WHERE asset."asset_id" = model."managed_asset_id";

UPDATE "deltallm_model"
SET "display_name" = "model_name"
WHERE "display_name" IS NULL;
