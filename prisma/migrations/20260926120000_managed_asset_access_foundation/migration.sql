-- Additive foundation for creator-owned asset access. Existing rows remain
-- platform-governed until their write paths are migrated to the new policy owner.
CREATE TABLE "deltallm_managedasset" (
  "asset_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "asset_kind" TEXT NOT NULL,
  "governance_source" TEXT NOT NULL DEFAULT 'platform',
  "owner_account_id" TEXT,
  "created_by_account_id" TEXT,
  "policy_version" BIGINT NOT NULL DEFAULT 1,
  "state" TEXT NOT NULL DEFAULT 'active',
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

  CONSTRAINT "deltallm_managedasset_pkey" PRIMARY KEY ("asset_id"),
  CONSTRAINT "deltallm_managedasset_kind_chk" CHECK (
    "asset_kind" IN ('model', 'route_group', 'mcp_server', 'prompt_template', 'named_credential')
  ),
  CONSTRAINT "deltallm_managedasset_source_chk" CHECK (
    "governance_source" IN ('platform', 'creator')
  ),
  CONSTRAINT "deltallm_managedasset_creator_owner_chk" CHECK (
    "governance_source" <> 'creator' OR "owner_account_id" IS NOT NULL
  ),
  CONSTRAINT "deltallm_managedasset_policy_version_chk" CHECK ("policy_version" > 0),
  CONSTRAINT "deltallm_managedasset_state_chk" CHECK ("state" IN ('active', 'archived'))
);

CREATE INDEX "deltallm_managedasset_owner_kind_state_idx"
  ON "deltallm_managedasset"("owner_account_id", "asset_kind", "state");

CREATE INDEX "deltallm_managedasset_kind_source_state_idx"
  ON "deltallm_managedasset"("asset_kind", "governance_source", "state");

ALTER TABLE "deltallm_managedasset"
  ADD CONSTRAINT "deltallm_managedasset_owner_account_id_fkey"
  FOREIGN KEY ("owner_account_id") REFERENCES "deltallm_platformaccount"("account_id")
  ON DELETE RESTRICT ON UPDATE CASCADE,
  ADD CONSTRAINT "deltallm_managedasset_created_by_account_id_fkey"
  FOREIGN KEY ("created_by_account_id") REFERENCES "deltallm_platformaccount"("account_id")
  ON DELETE SET NULL ON UPDATE CASCADE;

CREATE TABLE "deltallm_assetgrant" (
  "grant_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "managed_asset_id" TEXT NOT NULL,
  "subject_type" TEXT NOT NULL,
  "team_id" TEXT,
  "organization_id" TEXT,
  "access_role" TEXT NOT NULL,
  "created_by_account_id" TEXT,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

  CONSTRAINT "deltallm_assetgrant_pkey" PRIMARY KEY ("grant_id"),
  CONSTRAINT "deltallm_assetgrant_managed_asset_id_key" UNIQUE ("managed_asset_id"),
  CONSTRAINT "deltallm_assetgrant_role_chk" CHECK ("access_role" IN ('reader', 'editor')),
  CONSTRAINT "deltallm_assetgrant_subject_chk" CHECK (
    ("subject_type" = 'team' AND "team_id" IS NOT NULL AND "organization_id" IS NULL)
    OR
    ("subject_type" = 'organization' AND "organization_id" IS NOT NULL AND "team_id" IS NULL)
    OR
    ("subject_type" = 'public' AND "team_id" IS NULL AND "organization_id" IS NULL)
  ),
  CONSTRAINT "deltallm_assetgrant_public_reader_chk" CHECK (
    "subject_type" <> 'public' OR "access_role" = 'reader'
  )
);

CREATE INDEX "deltallm_assetgrant_team_role_idx"
  ON "deltallm_assetgrant"("team_id", "access_role");

CREATE INDEX "deltallm_assetgrant_organization_role_idx"
  ON "deltallm_assetgrant"("organization_id", "access_role");

CREATE INDEX "deltallm_assetgrant_subject_role_idx"
  ON "deltallm_assetgrant"("subject_type", "access_role");

ALTER TABLE "deltallm_assetgrant"
  ADD CONSTRAINT "deltallm_assetgrant_managed_asset_id_fkey"
  FOREIGN KEY ("managed_asset_id") REFERENCES "deltallm_managedasset"("asset_id")
  ON DELETE CASCADE ON UPDATE CASCADE,
  ADD CONSTRAINT "deltallm_assetgrant_team_id_fkey"
  FOREIGN KEY ("team_id") REFERENCES "deltallm_teamtable"("team_id")
  ON DELETE RESTRICT ON UPDATE CASCADE,
  ADD CONSTRAINT "deltallm_assetgrant_organization_id_fkey"
  FOREIGN KEY ("organization_id") REFERENCES "deltallm_organizationtable"("organization_id")
  ON DELETE RESTRICT ON UPDATE CASCADE,
  ADD CONSTRAINT "deltallm_assetgrant_created_by_account_id_fkey"
  FOREIGN KEY ("created_by_account_id") REFERENCES "deltallm_platformaccount"("account_id")
  ON DELETE SET NULL ON UPDATE CASCADE;

ALTER TABLE "deltallm_namedcredential"
  ADD COLUMN "managed_asset_id" TEXT,
  ADD COLUMN "name_scope" TEXT NOT NULL DEFAULT 'platform';
DROP INDEX "deltallm_namedcredential_name_key";
CREATE UNIQUE INDEX "deltallm_namedcredential_scope_name_key"
  ON "deltallm_namedcredential"("name_scope", "name");
ALTER TABLE "deltallm_routegroup" ADD COLUMN "managed_asset_id" TEXT;
ALTER TABLE "deltallm_mcpserver" ADD COLUMN "managed_asset_id" TEXT;
ALTER TABLE "deltallm_prompttemplate" ADD COLUMN "managed_asset_id" TEXT;

UPDATE "deltallm_namedcredential"
SET "managed_asset_id" = gen_random_uuid()::text
WHERE "managed_asset_id" IS NULL;

UPDATE "deltallm_routegroup"
SET "managed_asset_id" = gen_random_uuid()::text
WHERE "managed_asset_id" IS NULL;

UPDATE "deltallm_mcpserver"
SET "managed_asset_id" = gen_random_uuid()::text
WHERE "managed_asset_id" IS NULL;

UPDATE "deltallm_prompttemplate"
SET "managed_asset_id" = gen_random_uuid()::text
WHERE "managed_asset_id" IS NULL;

INSERT INTO "deltallm_managedasset" (
  "asset_id", "asset_kind", "governance_source", "created_by_account_id", "created_at", "updated_at"
)
SELECT credential."managed_asset_id", 'named_credential', 'platform', account."account_id",
       credential."created_at", credential."updated_at"
FROM "deltallm_namedcredential" AS credential
LEFT JOIN "deltallm_platformaccount" AS account
  ON account."account_id" = credential."created_by_account_id";

INSERT INTO "deltallm_managedasset" (
  "asset_id", "asset_kind", "governance_source", "created_at", "updated_at"
)
SELECT "managed_asset_id", 'route_group', 'platform', "created_at", "updated_at"
FROM "deltallm_routegroup";

INSERT INTO "deltallm_managedasset" (
  "asset_id", "asset_kind", "governance_source", "created_by_account_id", "created_at", "updated_at"
)
SELECT server."managed_asset_id", 'mcp_server', 'platform', account."account_id",
       server."created_at", server."updated_at"
FROM "deltallm_mcpserver" AS server
LEFT JOIN "deltallm_platformaccount" AS account
  ON account."account_id" = server."created_by_account_id";

INSERT INTO "deltallm_managedasset" (
  "asset_id", "asset_kind", "governance_source", "created_at", "updated_at"
)
SELECT "managed_asset_id", 'prompt_template', 'platform', "created_at", "updated_at"
FROM "deltallm_prompttemplate";

CREATE TABLE "deltallm_model" (
  "model_id" TEXT NOT NULL DEFAULT gen_random_uuid()::text,
  "model_name" TEXT NOT NULL,
  "managed_asset_id" TEXT,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

  CONSTRAINT "deltallm_model_pkey" PRIMARY KEY ("model_id"),
  CONSTRAINT "deltallm_model_model_name_key" UNIQUE ("model_name"),
  CONSTRAINT "deltallm_model_managed_asset_id_key" UNIQUE ("managed_asset_id")
);

INSERT INTO "deltallm_model" ("model_name", "managed_asset_id", "created_at", "updated_at")
SELECT "model_name", gen_random_uuid()::text, MIN("created_at"), MAX("updated_at")
FROM "deltallm_modeldeployment"
GROUP BY "model_name";

INSERT INTO "deltallm_managedasset" (
  "asset_id", "asset_kind", "governance_source", "created_at", "updated_at"
)
SELECT "managed_asset_id", 'model', 'platform', "created_at", "updated_at"
FROM "deltallm_model";

ALTER TABLE "deltallm_modeldeployment" ADD COLUMN "model_id" TEXT;

UPDATE "deltallm_modeldeployment" AS deployment
SET "model_id" = model."model_id"
FROM "deltallm_model" AS model
WHERE model."model_name" = deployment."model_name";

CREATE INDEX "deltallm_modeldeployment_model_id_idx"
  ON "deltallm_modeldeployment"("model_id");

CREATE UNIQUE INDEX "deltallm_namedcredential_managed_asset_id_key"
  ON "deltallm_namedcredential"("managed_asset_id");
CREATE UNIQUE INDEX "deltallm_routegroup_managed_asset_id_key"
  ON "deltallm_routegroup"("managed_asset_id");
CREATE UNIQUE INDEX "deltallm_mcpserver_managed_asset_id_key"
  ON "deltallm_mcpserver"("managed_asset_id");
CREATE UNIQUE INDEX "deltallm_prompttemplate_managed_asset_id_key"
  ON "deltallm_prompttemplate"("managed_asset_id");

ALTER TABLE "deltallm_namedcredential"
  ADD CONSTRAINT "deltallm_namedcredential_managed_asset_id_fkey"
  FOREIGN KEY ("managed_asset_id") REFERENCES "deltallm_managedasset"("asset_id")
  ON DELETE SET NULL ON UPDATE CASCADE;

ALTER TABLE "deltallm_routegroup"
  ADD CONSTRAINT "deltallm_routegroup_managed_asset_id_fkey"
  FOREIGN KEY ("managed_asset_id") REFERENCES "deltallm_managedasset"("asset_id")
  ON DELETE SET NULL ON UPDATE CASCADE;

ALTER TABLE "deltallm_mcpserver"
  ADD CONSTRAINT "deltallm_mcpserver_managed_asset_id_fkey"
  FOREIGN KEY ("managed_asset_id") REFERENCES "deltallm_managedasset"("asset_id")
  ON DELETE SET NULL ON UPDATE CASCADE;

ALTER TABLE "deltallm_prompttemplate"
  ADD CONSTRAINT "deltallm_prompttemplate_managed_asset_id_fkey"
  FOREIGN KEY ("managed_asset_id") REFERENCES "deltallm_managedasset"("asset_id")
  ON DELETE SET NULL ON UPDATE CASCADE;

ALTER TABLE "deltallm_model"
  ADD CONSTRAINT "deltallm_model_managed_asset_id_fkey"
  FOREIGN KEY ("managed_asset_id") REFERENCES "deltallm_managedasset"("asset_id")
  ON DELETE SET NULL ON UPDATE CASCADE;

ALTER TABLE "deltallm_modeldeployment"
  ADD CONSTRAINT "deltallm_modeldeployment_model_id_fkey"
  FOREIGN KEY ("model_id") REFERENCES "deltallm_model"("model_id")
  ON DELETE SET NULL ON UPDATE CASCADE;

-- Rolling-deploy compatibility: an older application binary does not know about
-- managed_asset_id/model_id. Keep its writes safe and platform-governed at the
-- database boundary until every supported binary writes the expanded schema.
CREATE FUNCTION "deltallm_ensure_managed_asset_link"()
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
    1,
    'active',
    COALESCE(NEW.created_at, NOW()),
    COALESCE(NEW.updated_at, NOW())
  );
  NEW.managed_asset_id := new_asset_id;
  RETURN NEW;
END
$managed_asset_link$;

CREATE TRIGGER "deltallm_namedcredential_managed_asset_link_trg"
BEFORE INSERT OR UPDATE ON "deltallm_namedcredential"
FOR EACH ROW EXECUTE FUNCTION "deltallm_ensure_managed_asset_link"();

CREATE TRIGGER "deltallm_model_managed_asset_link_trg"
BEFORE INSERT OR UPDATE ON "deltallm_model"
FOR EACH ROW EXECUTE FUNCTION "deltallm_ensure_managed_asset_link"();

CREATE TRIGGER "deltallm_routegroup_managed_asset_link_trg"
BEFORE INSERT OR UPDATE ON "deltallm_routegroup"
FOR EACH ROW EXECUTE FUNCTION "deltallm_ensure_managed_asset_link"();

CREATE TRIGGER "deltallm_mcpserver_managed_asset_link_trg"
BEFORE INSERT OR UPDATE ON "deltallm_mcpserver"
FOR EACH ROW EXECUTE FUNCTION "deltallm_ensure_managed_asset_link"();

CREATE TRIGGER "deltallm_prompttemplate_managed_asset_link_trg"
BEFORE INSERT OR UPDATE ON "deltallm_prompttemplate"
FOR EACH ROW EXECUTE FUNCTION "deltallm_ensure_managed_asset_link"();

CREATE FUNCTION "deltallm_ensure_model_deployment_link"()
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
        policy_version,
        state,
        created_at,
        updated_at
      ) VALUES (
        new_asset_id,
        'model',
        'platform',
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

CREATE TRIGGER "deltallm_modeldeployment_model_link_trg"
BEFORE INSERT OR UPDATE ON "deltallm_modeldeployment"
FOR EACH ROW EXECUTE FUNCTION "deltallm_ensure_model_deployment_link"();

-- Enforce the dependency in PostgreSQL as well as the API. The advisory lock
-- serializes concurrent policy/attachment changes so a write-skew cannot leave
-- a model with a broader audience than its credential.
CREATE FUNCTION "deltallm_validate_model_credential_audiences"()
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
    LEFT JOIN deltallm_assetgrant AS model_grant
      ON model_grant.managed_asset_id = model_asset.asset_id
    LEFT JOIN deltallm_assetgrant AS credential_grant
      ON credential_grant.managed_asset_id = credential_asset.asset_id
    LEFT JOIN deltallm_teamtable AS model_team
      ON model_team.team_id = model_grant.team_id
    WHERE model_asset.governance_source = 'creator'
      AND model_asset.state = 'active'
      AND credential_asset.state = 'active'
      AND (
        NOT (
          credential_asset.owner_account_id = model_asset.owner_account_id
          OR credential_grant.subject_type = 'public'
          OR (
            credential_grant.subject_type = 'team'
            AND EXISTS (
              SELECT 1 FROM deltallm_teammembership AS owner_team
              WHERE owner_team.account_id = model_asset.owner_account_id
                AND owner_team.team_id = credential_grant.team_id
            )
          )
          OR (
            credential_grant.subject_type = 'organization'
            AND EXISTS (
              SELECT 1 FROM deltallm_organizationmembership AS owner_org
              WHERE owner_org.account_id = model_asset.owner_account_id
                AND owner_org.organization_id = credential_grant.organization_id
            )
          )
        )
        OR NOT (
          model_grant.managed_asset_id IS NULL
          OR credential_grant.subject_type = 'public'
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

CREATE CONSTRAINT TRIGGER "deltallm_modeldeployment_credential_audience_trg"
AFTER INSERT OR UPDATE OF "model_id", "named_credential_id" ON "deltallm_modeldeployment"
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
