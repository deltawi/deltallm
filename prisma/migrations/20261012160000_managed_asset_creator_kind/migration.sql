-- Record how the creator of a managed asset authenticated. The account ID alone
-- cannot tell a master-key create from a deleted account or from a database
-- compatibility path, so the Admin UI showed all of them as "Not recorded".
--
-- Existing rows keep "unknown" unless they already have a creator account.
-- Application writes always set the kind. The column default only applies to
-- older binaries during a rolling deploy.
BEGIN;
SET LOCAL lock_timeout = '5s';

ALTER TABLE "deltallm_managedasset"
  ADD COLUMN "created_by_kind" TEXT NOT NULL DEFAULT 'unknown';

UPDATE "deltallm_managedasset"
SET "created_by_kind" = 'account'
WHERE "created_by_account_id" IS NOT NULL;

ALTER TABLE "deltallm_managedasset"
  ADD CONSTRAINT "deltallm_managedasset_created_by_kind_check"
  CHECK ("created_by_kind" IN ('account', 'master_key', 'system', 'unknown'));

-- Assets that the compatibility triggers create have no application actor:
-- they come from config seeding or from an older binary. Record them as
-- "system", or as "account" when the source row names a valid creator.
CREATE OR REPLACE FUNCTION "deltallm_ensure_managed_asset_link"()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $managed_asset_link$
DECLARE
  expected_kind TEXT;
  existing_kind TEXT;
  new_asset_id TEXT;
  valid_creator_id TEXT;
BEGIN
  expected_kind := CASE TG_TABLE_NAME
    WHEN 'deltallm_namedcredential' THEN 'named_credential'
    WHEN 'deltallm_model' THEN 'model'
    WHEN 'deltallm_routegroup' THEN 'route_group'
    WHEN 'deltallm_mcpserver' THEN 'mcp_server'
    WHEN 'deltallm_prompttemplate' THEN 'prompt_template'
    ELSE NULL
  END;

  IF expected_kind IS NULL THEN
    RAISE EXCEPTION 'unsupported managed-asset trigger table: %', TG_TABLE_NAME;
  END IF;

  IF NEW.managed_asset_id IS NOT NULL THEN
    SELECT asset_kind INTO existing_kind
    FROM deltallm_managedasset
    WHERE asset_id = NEW.managed_asset_id;

    IF existing_kind IS NULL THEN
      RAISE EXCEPTION 'managed asset % does not exist', NEW.managed_asset_id
        USING ERRCODE = '23503';
    END IF;
    IF existing_kind <> expected_kind THEN
      RAISE EXCEPTION 'managed asset % has kind %, expected %',
        NEW.managed_asset_id, existing_kind, expected_kind
        USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
  END IF;

  valid_creator_id := NULL;
  IF TG_TABLE_NAME IN ('deltallm_namedcredential', 'deltallm_mcpserver')
     AND NULLIF(to_jsonb(NEW)->>'created_by_account_id', '') IS NOT NULL THEN
    SELECT account_id INTO valid_creator_id
    FROM deltallm_platformaccount
    WHERE account_id = NULLIF(to_jsonb(NEW)->>'created_by_account_id', '');
  END IF;

  new_asset_id := gen_random_uuid()::text;
  INSERT INTO deltallm_managedasset (
    asset_id,
    asset_kind,
    governance_source,
    owner_account_id,
    created_by_account_id,
    created_by_kind,
    policy_version,
    state,
    created_at,
    updated_at
  ) VALUES (
    new_asset_id,
    expected_kind,
    'platform',
    NULL,
    valid_creator_id,
    CASE WHEN valid_creator_id IS NULL THEN 'system' ELSE 'account' END,
    1,
    'active',
    COALESCE(NEW.created_at, NOW()),
    COALESCE(NEW.updated_at, NOW())
  );
  NEW.managed_asset_id := new_asset_id;
  RETURN NEW;
END
$managed_asset_link$;


CREATE OR REPLACE FUNCTION "deltallm_ensure_model_deployment_link"()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $model_deployment_link$
DECLARE
  linked_model_id TEXT;
  linked_governance_source TEXT;
  new_model_id TEXT;
  new_asset_id TEXT;
BEGIN
  IF NEW.model_id IS NOT NULL THEN
    SELECT model_id INTO linked_model_id
    FROM deltallm_model
    WHERE model_id = NEW.model_id
      AND model_name = NEW.model_name;
    IF linked_model_id IS NOT NULL THEN
      RETURN NEW;
    END IF;
  END IF;

  SELECT model.model_id, asset.governance_source
  INTO linked_model_id, linked_governance_source
  FROM deltallm_model AS model
  JOIN deltallm_managedasset AS asset ON asset.asset_id = model.managed_asset_id
  WHERE model.model_name = NEW.model_name;

  IF linked_model_id IS NOT NULL AND linked_governance_source = 'creator' THEN
    RAISE EXCEPTION 'model name % is owned by a creator', NEW.model_name
      USING ERRCODE = '23505';
  END IF;

  IF linked_model_id IS NULL THEN
    new_model_id := gen_random_uuid()::text;
    new_asset_id := gen_random_uuid()::text;
    BEGIN
      INSERT INTO deltallm_managedasset (
        asset_id,
        asset_kind,
        governance_source,
        created_by_kind,
        policy_version,
        state,
        created_at,
        updated_at
      ) VALUES (
        new_asset_id,
        'model',
        'platform',
        'system',
        1,
        'active',
        COALESCE(NEW.created_at, NOW()),
        COALESCE(NEW.updated_at, NOW())
      );
      INSERT INTO deltallm_model (
        model_id,
        model_name,
        managed_asset_id,
        created_at,
        updated_at
      ) VALUES (
        new_model_id,
        NEW.model_name,
        new_asset_id,
        COALESCE(NEW.created_at, NOW()),
        COALESCE(NEW.updated_at, NOW())
      );
      linked_model_id := new_model_id;
    EXCEPTION WHEN unique_violation THEN
      SELECT model.model_id, asset.governance_source
      INTO linked_model_id, linked_governance_source
      FROM deltallm_model AS model
      JOIN deltallm_managedasset AS asset ON asset.asset_id = model.managed_asset_id
      WHERE model.model_name = NEW.model_name;
      IF linked_governance_source = 'creator' THEN
        RAISE EXCEPTION 'model name % is owned by a creator', NEW.model_name
          USING ERRCODE = '23505';
      END IF;
    END;
  END IF;

  IF linked_model_id IS NULL THEN
    RAISE EXCEPTION 'could not resolve logical model for %', NEW.model_name;
  END IF;

  NEW.model_id := linked_model_id;
  UPDATE deltallm_routeruntimestate
  SET revision = revision + 1,
      updated_at = NOW()
  WHERE state_key = 'routing_runtime';
  RETURN NEW;
END
$model_deployment_link$;


COMMIT;
