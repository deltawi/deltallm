-- Validate only credential bindings affected by the row being changed. The
-- previous function acquired one installation-wide advisory lock and scanned
-- every creator deployment for each grant or membership mutation.
CREATE FUNCTION "deltallm_assert_model_credential_audiences"(
  deployment_ids TEXT[]
)
RETURNS VOID
LANGUAGE plpgsql
AS $model_credential_assert$
BEGIN
  IF deployment_ids IS NULL OR cardinality(deployment_ids) = 0 THEN
    RETURN;
  END IF;

  -- Serialize changes to the same dependency set without blocking unrelated
  -- models. The validation statement runs after these locks and therefore gets
  -- a fresh READ COMMITTED snapshot after any waiter completes.
  PERFORM pg_advisory_xact_lock(hashtextextended(lock_key, 0))
  FROM (
    SELECT DISTINCT lock_key
    FROM (
      SELECT 'deployment:' || deployment.deployment_id AS lock_key
      FROM deltallm_modeldeployment AS deployment
      WHERE deployment.deployment_id = ANY(deployment_ids)
      UNION ALL
      SELECT 'asset:' || model.managed_asset_id
      FROM deltallm_modeldeployment AS deployment
      JOIN deltallm_model AS model ON model.model_id = deployment.model_id
      WHERE deployment.deployment_id = ANY(deployment_ids)
      UNION ALL
      SELECT 'asset:' || credential.managed_asset_id
      FROM deltallm_modeldeployment AS deployment
      JOIN deltallm_namedcredential AS credential
        ON credential.credential_id = deployment.named_credential_id
      WHERE deployment.deployment_id = ANY(deployment_ids)
    ) AS dependency_locks
    WHERE lock_key IS NOT NULL
  ) AS unique_locks
  ORDER BY lock_key;

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
    WHERE deployment.deployment_id = ANY(deployment_ids)
      AND model_asset.governance_source = 'creator'
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
                            SELECT 1
                            FROM deltallm_teammembership AS owner_team
                            WHERE owner_team.account_id = model_asset.owner_account_id
                              AND owner_team.team_id = owner_grant.team_id
                          )
                        )
                        OR (
                          owner_grant.subject_type = 'organization'
                          AND EXISTS (
                            SELECT 1
                            FROM deltallm_organizationmembership AS owner_org
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
END
$model_credential_assert$;

CREATE OR REPLACE FUNCTION "deltallm_validate_model_credential_audiences"()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $model_credential_audience$
DECLARE
  affected_deployments TEXT[];
  affected_assets TEXT[];
  affected_accounts TEXT[];
  affected_subjects TEXT[];
BEGIN
  IF TG_TABLE_NAME = 'deltallm_modeldeployment' THEN
    affected_deployments := ARRAY[NEW.deployment_id];

  ELSIF TG_TABLE_NAME = 'deltallm_assetgrant' THEN
    IF TG_OP = 'INSERT' THEN
      affected_assets := ARRAY[NEW.managed_asset_id];
    ELSIF TG_OP = 'DELETE' THEN
      affected_assets := ARRAY[OLD.managed_asset_id];
    ELSE
      affected_assets := ARRAY[OLD.managed_asset_id, NEW.managed_asset_id];
    END IF;
    SELECT array_agg(DISTINCT deployment.deployment_id)
    INTO affected_deployments
    FROM deltallm_modeldeployment AS deployment
    JOIN deltallm_model AS model ON model.model_id = deployment.model_id
    LEFT JOIN deltallm_namedcredential AS credential
      ON credential.credential_id = deployment.named_credential_id
    WHERE model.managed_asset_id = ANY(affected_assets)
       OR credential.managed_asset_id = ANY(affected_assets);

  ELSIF TG_TABLE_NAME = 'deltallm_teammembership' THEN
    IF TG_OP = 'INSERT' THEN
      affected_accounts := ARRAY[NEW.account_id];
      affected_subjects := ARRAY[NEW.team_id];
    ELSIF TG_OP = 'DELETE' THEN
      affected_accounts := ARRAY[OLD.account_id];
      affected_subjects := ARRAY[OLD.team_id];
    ELSE
      affected_accounts := ARRAY[OLD.account_id, NEW.account_id];
      affected_subjects := ARRAY[OLD.team_id, NEW.team_id];
    END IF;
    SELECT array_agg(DISTINCT deployment.deployment_id)
    INTO affected_deployments
    FROM deltallm_modeldeployment AS deployment
    JOIN deltallm_model AS model ON model.model_id = deployment.model_id
    JOIN deltallm_managedasset AS model_asset ON model_asset.asset_id = model.managed_asset_id
    JOIN deltallm_namedcredential AS credential
      ON credential.credential_id = deployment.named_credential_id
    JOIN deltallm_assetgrant AS credential_grant
      ON credential_grant.managed_asset_id = credential.managed_asset_id
    WHERE deployment.credential_binding_mode = 'audience_scoped'
      AND model_asset.owner_account_id = ANY(affected_accounts)
      AND credential_grant.subject_type = 'team'
      AND credential_grant.team_id = ANY(affected_subjects);

  ELSIF TG_TABLE_NAME = 'deltallm_organizationmembership' THEN
    IF TG_OP = 'INSERT' THEN
      affected_accounts := ARRAY[NEW.account_id];
      affected_subjects := ARRAY[NEW.organization_id];
    ELSIF TG_OP = 'DELETE' THEN
      affected_accounts := ARRAY[OLD.account_id];
      affected_subjects := ARRAY[OLD.organization_id];
    ELSE
      affected_accounts := ARRAY[OLD.account_id, NEW.account_id];
      affected_subjects := ARRAY[OLD.organization_id, NEW.organization_id];
    END IF;
    SELECT array_agg(DISTINCT deployment.deployment_id)
    INTO affected_deployments
    FROM deltallm_modeldeployment AS deployment
    JOIN deltallm_model AS model ON model.model_id = deployment.model_id
    JOIN deltallm_managedasset AS model_asset ON model_asset.asset_id = model.managed_asset_id
    JOIN deltallm_namedcredential AS credential
      ON credential.credential_id = deployment.named_credential_id
    JOIN deltallm_assetgrant AS credential_grant
      ON credential_grant.managed_asset_id = credential.managed_asset_id
    WHERE deployment.credential_binding_mode = 'audience_scoped'
      AND model_asset.owner_account_id = ANY(affected_accounts)
      AND credential_grant.subject_type = 'organization'
      AND credential_grant.organization_id = ANY(affected_subjects);

  ELSIF TG_TABLE_NAME = 'deltallm_teamtable' THEN
    affected_subjects := ARRAY[NEW.team_id];
    SELECT array_agg(DISTINCT deployment.deployment_id)
    INTO affected_deployments
    FROM deltallm_modeldeployment AS deployment
    JOIN deltallm_model AS model ON model.model_id = deployment.model_id
    JOIN deltallm_managedasset AS model_asset ON model_asset.asset_id = model.managed_asset_id
    JOIN deltallm_assetgrant AS model_grant
      ON model_grant.managed_asset_id = model_asset.asset_id
    WHERE deployment.credential_binding_mode = 'audience_scoped'
      AND model_grant.subject_type = 'team'
      AND model_grant.team_id = ANY(affected_subjects);

  ELSIF TG_TABLE_NAME = 'deltallm_managedasset' THEN
    affected_assets := ARRAY[COALESCE(NEW.asset_id, OLD.asset_id)];
    SELECT array_agg(DISTINCT deployment.deployment_id)
    INTO affected_deployments
    FROM deltallm_modeldeployment AS deployment
    JOIN deltallm_model AS model ON model.model_id = deployment.model_id
    LEFT JOIN deltallm_namedcredential AS credential
      ON credential.credential_id = deployment.named_credential_id
    WHERE model.managed_asset_id = ANY(affected_assets)
       OR credential.managed_asset_id = ANY(affected_assets);

  ELSIF TG_TABLE_NAME = 'deltallm_namedcredential' THEN
    SELECT array_agg(DISTINCT deployment.deployment_id)
    INTO affected_deployments
    FROM deltallm_modeldeployment AS deployment
    WHERE deployment.named_credential_id = COALESCE(NEW.credential_id, OLD.credential_id);
  END IF;

  PERFORM "deltallm_assert_model_credential_audiences"(affected_deployments);
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END
$model_credential_audience$;

DROP TRIGGER "deltallm_modeldeployment_credential_audience_trg"
  ON "deltallm_modeldeployment";
DROP TRIGGER "deltallm_assetgrant_credential_audience_trg"
  ON "deltallm_assetgrant";
DROP TRIGGER "deltallm_teammembership_credential_audience_trg"
  ON "deltallm_teammembership";
DROP TRIGGER "deltallm_orgmembership_credential_audience_trg"
  ON "deltallm_organizationmembership";
DROP TRIGGER "deltallm_team_organization_credential_audience_trg"
  ON "deltallm_teamtable";
DROP TRIGGER "deltallm_managedasset_credential_binding_trg"
  ON "deltallm_managedasset";
DROP TRIGGER "deltallm_namedcredential_binding_trg"
  ON "deltallm_namedcredential";

CREATE CONSTRAINT TRIGGER "deltallm_modeldeployment_credential_audience_trg"
AFTER INSERT OR UPDATE OF
  "model_id", "named_credential_id", "credential_binding_mode",
  "credential_binding_state", "credential_bound_by_account_id"
ON "deltallm_modeldeployment"
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION "deltallm_validate_model_credential_audiences"();

CREATE CONSTRAINT TRIGGER "deltallm_assetgrant_credential_audience_trg"
AFTER INSERT OR UPDATE OR DELETE ON "deltallm_assetgrant"
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION "deltallm_validate_model_credential_audiences"();

CREATE CONSTRAINT TRIGGER "deltallm_teammembership_credential_audience_trg"
AFTER INSERT OR UPDATE OR DELETE ON "deltallm_teammembership"
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION "deltallm_validate_model_credential_audiences"();

CREATE CONSTRAINT TRIGGER "deltallm_orgmembership_credential_audience_trg"
AFTER INSERT OR UPDATE OR DELETE ON "deltallm_organizationmembership"
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION "deltallm_validate_model_credential_audiences"();

CREATE CONSTRAINT TRIGGER "deltallm_team_organization_credential_audience_trg"
AFTER UPDATE OF "organization_id" ON "deltallm_teamtable"
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
