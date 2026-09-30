-- Keep old application instances safe after the one-time stale-binding repair.
-- An old writer only clears named_credential_id when moving a platform model
-- back to inline credentials, so the compatibility trigger must clear every
-- newer binding field on that write as well.
CREATE OR REPLACE FUNCTION "deltallm_adopt_model_credential_binding"()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $model_credential_binding$
DECLARE
  model_governance TEXT;
  model_owner TEXT;
  credential_owner TEXT;
BEGIN
  SELECT asset.governance_source, asset.owner_account_id
  INTO model_governance, model_owner
  FROM deltallm_model AS model
  JOIN deltallm_managedasset AS asset ON asset.asset_id = model.managed_asset_id
  WHERE model.model_id = NEW.model_id;

  IF NEW.named_credential_id IS NULL THEN
    IF model_governance = 'creator' THEN
      NEW.credential_binding_state := 'revoked';
      NEW.credential_revoked_at := COALESCE(NEW.credential_revoked_at, NOW());
    ELSE
      NEW.credential_binding_mode := NULL;
      NEW.credential_binding_state := NULL;
      NEW.credential_bound_by_account_id := NULL;
      NEW.credential_bound_at := NULL;
      NEW.credential_revoked_at := NULL;
    END IF;
    RETURN NEW;
  END IF;

  IF TG_OP = 'INSERT'
     OR NEW.named_credential_id IS DISTINCT FROM OLD.named_credential_id
     OR NEW.credential_binding_mode IS NULL
     OR NEW.credential_binding_state IS NULL THEN
    SELECT asset.owner_account_id
    INTO credential_owner
    FROM deltallm_namedcredential AS credential
    JOIN deltallm_managedasset AS asset
      ON asset.asset_id = credential.managed_asset_id
    WHERE credential.credential_id = NEW.named_credential_id;

    IF NEW.credential_binding_mode IS NULL
       OR (
         TG_OP = 'UPDATE'
         AND NEW.named_credential_id IS DISTINCT FROM OLD.named_credential_id
         AND NEW.credential_binding_mode IS NOT DISTINCT FROM OLD.credential_binding_mode
       ) THEN
      NEW.credential_binding_mode := CASE
        WHEN model_governance = 'platform' THEN 'platform_override'
        WHEN credential_owner = model_owner THEN 'owner_delegated'
        ELSE 'audience_scoped'
      END;
    END IF;
    NEW.credential_binding_state := COALESCE(NEW.credential_binding_state, 'active');
    NEW.credential_bound_by_account_id := COALESCE(
      NEW.credential_bound_by_account_id,
      CASE
        WHEN NEW.credential_binding_mode = 'owner_delegated' THEN credential_owner
        ELSE NULL
      END
    );
    NEW.credential_bound_at := COALESCE(NEW.credential_bound_at, NOW());
    NEW.credential_revoked_at := NULL;
  END IF;
  RETURN NEW;
END
$model_credential_binding$;

-- Repair any stale platform rows recreated by an old writer after the previous
-- one-time cleanup was applied and before this trigger replacement landed.
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
