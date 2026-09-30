ALTER TABLE "deltallm_modeldeployment"
  ADD COLUMN "credential_binding_mode" TEXT,
  ADD COLUMN "credential_binding_state" TEXT,
  ADD COLUMN "credential_bound_by_account_id" TEXT,
  ADD COLUMN "credential_bound_at" TIMESTAMP(3),
  ADD COLUMN "credential_revoked_at" TIMESTAMP(3);

ALTER TABLE "deltallm_modeldeployment"
  ADD CONSTRAINT "deltallm_modeldeployment_credential_binding_mode_check"
  CHECK (
    "credential_binding_mode" IS NULL
    OR "credential_binding_mode" IN ('audience_scoped', 'owner_delegated', 'platform_override')
  ),
  ADD CONSTRAINT "deltallm_modeldeployment_credential_binding_state_check"
  CHECK (
    "credential_binding_state" IS NULL
    OR "credential_binding_state" IN ('active', 'revoked')
  ),
  ADD CONSTRAINT "deltallm_modeldeployment_credential_bound_by_account_id_fkey"
  FOREIGN KEY ("credential_bound_by_account_id")
  REFERENCES "deltallm_platformaccount"("account_id")
  ON DELETE SET NULL ON UPDATE CASCADE;

CREATE INDEX "deltallm_modeldeployment_credential_binding_idx"
  ON "deltallm_modeldeployment"("credential_binding_state", "credential_binding_mode");

-- Existing creator models using their owner's credential become opaque delegated
-- bindings. Cross-owner legacy bindings retain the stricter audience rule.
UPDATE "deltallm_modeldeployment" AS deployment
SET
  "credential_binding_mode" = CASE
    WHEN model_asset.governance_source = 'platform' THEN 'platform_override'
    WHEN credential_asset.owner_account_id = model_asset.owner_account_id
      THEN 'owner_delegated'
    ELSE 'audience_scoped'
  END,
  "credential_binding_state" = 'active',
  "credential_bound_by_account_id" = CASE
    WHEN model_asset.governance_source = 'creator'
      AND credential_asset.owner_account_id = model_asset.owner_account_id
      THEN credential_asset.owner_account_id
    ELSE NULL
  END,
  "credential_bound_at" = deployment.created_at
FROM "deltallm_model" AS model
JOIN "deltallm_managedasset" AS model_asset
  ON model_asset.asset_id = model.managed_asset_id
JOIN "deltallm_namedcredential" AS credential
  ON TRUE
JOIN "deltallm_managedasset" AS credential_asset
  ON credential_asset.asset_id = credential.managed_asset_id
WHERE deployment.model_id = model.model_id
  AND credential.credential_id = deployment.named_credential_id
  AND deployment.named_credential_id IS NOT NULL;

-- A creator deployment without a credential must remain unroutable. Platform
-- deployments keep their existing inline/default-credential compatibility.
UPDATE "deltallm_modeldeployment" AS deployment
SET
  "credential_binding_state" = 'revoked',
  "credential_revoked_at" = NOW()
FROM "deltallm_model" AS model
JOIN "deltallm_managedasset" AS model_asset
  ON model_asset.asset_id = model.managed_asset_id
WHERE deployment.model_id = model.model_id
  AND model_asset.governance_source = 'creator'
  AND deployment.named_credential_id IS NULL;

-- Keep rolling deployments safe. Older application versions do not know the new
-- columns, so infer conservative binding metadata on their writes.
CREATE FUNCTION "deltallm_adopt_model_credential_binding"()
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

CREATE TRIGGER "deltallm_modeldeployment_adopt_credential_binding_trg"
BEFORE INSERT OR UPDATE OF
  "model_id", "named_credential_id", "credential_binding_mode",
  "credential_binding_state", "credential_bound_by_account_id"
ON "deltallm_modeldeployment"
FOR EACH ROW EXECUTE FUNCTION "deltallm_adopt_model_credential_binding"();

-- Replace the original audience invariant. Only audience-scoped bindings expose
-- the credential audience to the model. Owner delegation authorizes the model as
-- an opaque runtime consumer and never grants credential visibility to model users.
CREATE OR REPLACE FUNCTION "deltallm_validate_model_credential_audiences"()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $model_credential_audience$
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('deltallm:model-credential-audience', 0));

  IF EXISTS (
    SELECT 1
    FROM deltallm_modeldeployment AS deployment
    JOIN deltallm_model AS model ON model.model_id = deployment.model_id
    JOIN deltallm_managedasset AS model_asset
      ON model_asset.asset_id = model.managed_asset_id
    LEFT JOIN deltallm_namedcredential AS credential
      ON credential.credential_id = deployment.named_credential_id
    LEFT JOIN deltallm_managedasset AS credential_asset
      ON credential_asset.asset_id = credential.managed_asset_id
    WHERE model_asset.governance_source = 'creator'
      AND model_asset.state = 'active'
      AND (
        deployment.credential_binding_state IS NULL
        OR deployment.credential_binding_state NOT IN ('active', 'revoked')
        OR (
          deployment.credential_binding_state = 'active'
          AND (
            deployment.named_credential_id IS NULL
            OR credential_asset.asset_id IS NULL
            OR credential_asset.state <> 'active'
            OR deployment.credential_binding_mode IS NULL
            OR deployment.credential_binding_mode NOT IN (
              'audience_scoped', 'owner_delegated', 'platform_override'
            )
            OR (
              deployment.credential_binding_mode = 'owner_delegated'
              AND (
                deployment.credential_bound_by_account_id IS NULL
                OR deployment.credential_bound_by_account_id
                   IS DISTINCT FROM credential_asset.owner_account_id
              )
            )
            OR (
              deployment.credential_binding_mode = 'audience_scoped'
              AND (
                NOT (
                  credential_asset.owner_account_id = model_asset.owner_account_id
                  OR EXISTS (
                    SELECT 1
                    FROM deltallm_assetgrant AS owner_grant
                    WHERE owner_grant.managed_asset_id = credential_asset.asset_id
                      AND (
                        owner_grant.subject_type = 'public'
                        OR (
                          owner_grant.subject_type = 'team'
                          AND EXISTS (
                            SELECT 1 FROM deltallm_teammembership AS owner_team
                            WHERE owner_team.account_id = model_asset.owner_account_id
                              AND owner_team.team_id = owner_grant.team_id
                          )
                        )
                        OR (
                          owner_grant.subject_type = 'organization'
                          AND EXISTS (
                            SELECT 1 FROM deltallm_organizationmembership AS owner_org
                            WHERE owner_org.account_id = model_asset.owner_account_id
                              AND owner_org.organization_id = owner_grant.organization_id
                          )
                        )
                      )
                  )
                )
                OR EXISTS (
                  SELECT 1
                  FROM deltallm_assetgrant AS model_grant
                  LEFT JOIN deltallm_teamtable AS model_team
                    ON model_team.team_id = model_grant.team_id
                  WHERE model_grant.managed_asset_id = model_asset.asset_id
                    AND NOT EXISTS (
                      SELECT 1
                      FROM deltallm_assetgrant AS credential_grant
                      WHERE credential_grant.managed_asset_id = credential_asset.asset_id
                        AND (
                          credential_grant.subject_type = 'public'
                          OR (
                            model_grant.subject_type = 'team'
                            AND credential_grant.subject_type = 'team'
                            AND credential_grant.team_id = model_grant.team_id
                          )
                          OR (
                            model_grant.subject_type = 'team'
                            AND credential_grant.subject_type = 'organization'
                            AND credential_grant.organization_id = model_team.organization_id
                          )
                          OR (
                            model_grant.subject_type = 'organization'
                            AND credential_grant.subject_type = 'organization'
                            AND credential_grant.organization_id = model_grant.organization_id
                          )
                        )
                    )
                )
              )
            )
          )
        )
      )
  ) THEN
    RAISE EXCEPTION 'creator model has an invalid named credential binding'
      USING ERRCODE = '23514';
  END IF;
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END
$model_credential_audience$;

DROP TRIGGER "deltallm_modeldeployment_credential_audience_trg"
  ON "deltallm_modeldeployment";

CREATE CONSTRAINT TRIGGER "deltallm_modeldeployment_credential_audience_trg"
AFTER INSERT OR UPDATE OF
  "model_id", "named_credential_id", "credential_binding_mode",
  "credential_binding_state", "credential_bound_by_account_id"
ON "deltallm_modeldeployment"
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION "deltallm_validate_model_credential_audiences"();

CREATE CONSTRAINT TRIGGER "deltallm_managedasset_credential_binding_trg"
AFTER UPDATE OF "owner_account_id", "state" ON "deltallm_managedasset"
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION "deltallm_validate_model_credential_audiences"();

CREATE CONSTRAINT TRIGGER "deltallm_namedcredential_binding_trg"
AFTER UPDATE OF "managed_asset_id" ON "deltallm_namedcredential"
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION "deltallm_validate_model_credential_audiences"();
