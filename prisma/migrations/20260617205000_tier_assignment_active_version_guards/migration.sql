DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM "deltallm_organizationtierassignment" a
    LEFT JOIN "deltallm_tierversion" v
      ON v."tier_version_id" = a."tier_version_id"
     AND v."tier_id" = a."tier_id"
    WHERE a."enabled" IS TRUE
      AND (a."ends_at" IS NULL OR a."ends_at" > NOW())
      AND a."tier_version_id" IS NOT NULL
      AND COALESCE(v."status", '') <> 'active'
  ) THEN
    RAISE EXCEPTION 'Cannot enforce active tier assignment invariant: enabled assignments reference non-active tier versions';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM "deltallm_organizationtierassignment" a
    WHERE a."enabled" IS TRUE
      AND (a."ends_at" IS NULL OR a."ends_at" > NOW())
      AND a."tier_version_id" IS NULL
      AND NOT EXISTS (
        SELECT 1
        FROM "deltallm_tierversion" v
        WHERE v."tier_id" = a."tier_id"
          AND v."status" = 'active'
      )
  ) THEN
    RAISE EXCEPTION 'Cannot enforce active tier assignment invariant: enabled unpinned assignments reference tiers without active versions';
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
BEGIN
  IF NEW."enabled" IS TRUE AND (NEW."ends_at" IS NULL OR NEW."ends_at" > NOW()) THEN
    SELECT t."tier_id"
    INTO locked_tier_id
    FROM "deltallm_tier" t
    WHERE t."tier_id" = NEW."tier_id"
    FOR UPDATE;

    IF locked_tier_id IS NULL THEN
      RAISE EXCEPTION 'tier_id must reference an existing tier'
        USING ERRCODE = 'foreign_key_violation';
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

CREATE TRIGGER "deltallm_orgtierassignment_active_version_guard"
  BEFORE INSERT OR UPDATE OF "tier_version_id", "tier_id", "enabled", "ends_at"
  ON "deltallm_organizationtierassignment"
  FOR EACH ROW
  EXECUTE FUNCTION "deltallm_validate_assignment_active_tier_version"();

CREATE OR REPLACE FUNCTION "deltallm_prevent_retiring_assigned_tier_version"()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  retiring_active_version BOOLEAN := FALSE;
BEGIN
  IF OLD."status" = 'active' THEN
    IF TG_OP = 'DELETE' THEN
      retiring_active_version := TRUE;
    ELSIF NEW."status" <> 'active' THEN
      retiring_active_version := TRUE;
    END IF;
  END IF;

  IF retiring_active_version THEN
    IF EXISTS (
      SELECT 1
      FROM "deltallm_organizationtierassignment" a
      WHERE a."tier_version_id" = OLD."tier_version_id"
        AND a."enabled" IS TRUE
        AND (a."ends_at" IS NULL OR a."ends_at" > NOW())
      LIMIT 1
    ) THEN
      RAISE EXCEPTION 'cannot retire active tier version while enabled assignments are pinned to it'
        USING ERRCODE = 'check_violation';
    END IF;

    IF EXISTS (
      SELECT 1
      FROM "deltallm_organizationtierassignment" a
      WHERE a."tier_id" = OLD."tier_id"
        AND a."tier_version_id" IS NULL
        AND a."enabled" IS TRUE
        AND (a."ends_at" IS NULL OR a."ends_at" > NOW())
      LIMIT 1
    ) THEN
      IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'cannot delete active tier version while enabled assignments follow this tier'
          USING ERRCODE = 'check_violation';
      END IF;

      IF NOT EXISTS (
        SELECT 1
        FROM "deltallm_tierversion" v
        WHERE v."tier_id" = OLD."tier_id"
          AND v."status" = 'active'
        LIMIT 1
      ) THEN
        RAISE EXCEPTION 'cannot retire active tier version while enabled assignments follow this tier without an active replacement'
          USING ERRCODE = 'check_violation';
      END IF;
    END IF;
  END IF;

  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;

  RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER "deltallm_tierversion_retire_assignment_guard"
  AFTER UPDATE OR DELETE
  ON "deltallm_tierversion"
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW
  EXECUTE FUNCTION "deltallm_prevent_retiring_assigned_tier_version"();
