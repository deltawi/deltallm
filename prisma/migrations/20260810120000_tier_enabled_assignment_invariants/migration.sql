DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM "deltallm_organizationtierassignment" a
    JOIN "deltallm_tier" t
      ON t."tier_id" = a."tier_id"
    WHERE a."enabled" IS TRUE
      AND (a."ends_at" IS NULL OR a."ends_at" > NOW())
      AND t."enabled" IS NOT TRUE
  ) THEN
    RAISE EXCEPTION 'Cannot enforce enabled tier assignment invariant: enabled assignments reference disabled tiers';
  END IF;
END $$;

CREATE OR REPLACE FUNCTION "deltallm_validate_assignment_active_tier_version"()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  version_status TEXT;
  active_version_id TEXT;
  locked_tier_id TEXT;
  tier_enabled BOOLEAN;
BEGIN
  IF NEW."enabled" IS TRUE AND (NEW."ends_at" IS NULL OR NEW."ends_at" > NOW()) THEN
    SELECT t."tier_id", t."enabled"
    INTO locked_tier_id, tier_enabled
    FROM "deltallm_tier" t
    WHERE t."tier_id" = NEW."tier_id"
    FOR UPDATE;

    IF locked_tier_id IS NULL THEN
      RAISE EXCEPTION 'tier_id must reference an existing tier'
        USING ERRCODE = 'foreign_key_violation';
    END IF;

    IF tier_enabled IS NOT TRUE THEN
      RAISE EXCEPTION 'enabled tier assignments require an enabled tier'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;

  IF NEW."enabled" IS TRUE
     AND (NEW."ends_at" IS NULL OR NEW."ends_at" > NOW())
     AND NEW."tier_version_id" IS NOT NULL THEN
    SELECT v."status"
    INTO version_status
    FROM "deltallm_tierversion" v
    WHERE v."tier_version_id" = NEW."tier_version_id"
      AND v."tier_id" = NEW."tier_id"
    FOR SHARE;

    IF version_status IS NULL THEN
      RAISE EXCEPTION 'tier_version_id must reference an existing tier version for tier_id'
        USING ERRCODE = 'foreign_key_violation';
    END IF;

    IF version_status <> 'active' THEN
      RAISE EXCEPTION 'enabled tier assignments must reference an active tier version'
        USING ERRCODE = 'check_violation';
    END IF;
  ELSIF NEW."enabled" IS TRUE AND (NEW."ends_at" IS NULL OR NEW."ends_at" > NOW()) THEN
    SELECT v."tier_version_id"
    INTO active_version_id
    FROM "deltallm_tierversion" v
    WHERE v."tier_id" = NEW."tier_id"
      AND v."status" = 'active'
    LIMIT 1
    FOR SHARE;

    IF active_version_id IS NULL THEN
      RAISE EXCEPTION 'enabled unpinned tier assignments require an active tier version'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;

  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION "deltallm_prevent_disabling_assigned_tier"()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF OLD."enabled" IS TRUE AND NEW."enabled" IS NOT TRUE THEN
    IF EXISTS (
      SELECT 1
      FROM "deltallm_organizationtierassignment" a
      WHERE a."tier_id" = OLD."tier_id"
        AND a."enabled" IS TRUE
        AND (a."ends_at" IS NULL OR a."ends_at" > NOW())
      LIMIT 1
    ) THEN
      RAISE EXCEPTION 'cannot disable tier while enabled organization assignments exist'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;

  RETURN NEW;
END;
$$;

CREATE TRIGGER "deltallm_tier_disable_assignment_guard"
  BEFORE UPDATE OF "enabled"
  ON "deltallm_tier"
  FOR EACH ROW
  EXECUTE FUNCTION "deltallm_prevent_disabling_assigned_tier"();
