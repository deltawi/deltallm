-- Platform deployments using inline credentials must not retain metadata from a
-- previous named-credential binding. Creator rows deliberately retain revoked
-- state and are excluded from this repair.
UPDATE "deltallm_modeldeployment" AS deployment
SET
  "credential_binding_mode" = NULL,
  "credential_binding_state" = NULL,
  "credential_bound_by_account_id" = NULL,
  "credential_bound_at" = NULL,
  "credential_revoked_at" = NULL,
  "updated_at" = NOW()
FROM "deltallm_model" AS model
JOIN "deltallm_managedasset" AS asset
  ON asset."asset_id" = model."managed_asset_id"
WHERE deployment."model_id" = model."model_id"
  AND asset."governance_source" = 'platform'
  AND deployment."named_credential_id" IS NULL
  AND (
    deployment."credential_binding_mode" IS NOT NULL
    OR deployment."credential_binding_state" IS NOT NULL
    OR deployment."credential_bound_by_account_id" IS NOT NULL
    OR deployment."credential_bound_at" IS NOT NULL
    OR deployment."credential_revoked_at" IS NOT NULL
  );
