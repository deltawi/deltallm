ALTER TABLE "deltallm_assetgrant"
  DROP CONSTRAINT "deltallm_assetgrant_managed_asset_id_key";

CREATE INDEX "deltallm_assetgrant_managed_asset_idx"
  ON "deltallm_assetgrant"("managed_asset_id");

CREATE UNIQUE INDEX "deltallm_assetgrant_asset_public_key"
  ON "deltallm_assetgrant"("managed_asset_id")
  WHERE "subject_type" = 'public';

CREATE UNIQUE INDEX "deltallm_assetgrant_asset_team_key"
  ON "deltallm_assetgrant"("managed_asset_id", "team_id")
  WHERE "subject_type" = 'team';

CREATE UNIQUE INDEX "deltallm_assetgrant_asset_organization_key"
  ON "deltallm_assetgrant"("managed_asset_id", "organization_id")
  WHERE "subject_type" = 'organization';

-- Validate the full set of grants for a model and its credential. A model audience
-- is valid only when at least one credential grant covers it. Public covers every
-- audience; an organization grant also covers teams in that organization.
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
    JOIN deltallm_namedcredential AS credential
      ON credential.credential_id = deployment.named_credential_id
    JOIN deltallm_managedasset AS credential_asset
      ON credential_asset.asset_id = credential.managed_asset_id
    WHERE model_asset.governance_source = 'creator'
      AND model_asset.state = 'active'
      AND credential_asset.state = 'active'
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
  ) THEN
    RAISE EXCEPTION 'creator model audience exceeds its named credential audience'
      USING ERRCODE = '23514';
  END IF;
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END
$model_credential_audience$;
