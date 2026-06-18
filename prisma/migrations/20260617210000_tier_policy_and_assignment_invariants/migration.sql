DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM "deltallm_tiermodelpolicy" p
    WHERE p."capacity_pool_key" IS NOT NULL
      AND NOT EXISTS (
        SELECT 1
        FROM "deltallm_tiercapacitypool" c
        WHERE c."tier_version_id" = p."tier_version_id"
          AND c."pool_key" = p."capacity_pool_key"
          AND c."callable_key" = p."callable_key"
      )
  ) THEN
    RAISE EXCEPTION 'Cannot enforce tier policy capacity pool invariant: model policies reference missing capacity pools';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM "deltallm_organizationtierassignment" a
    JOIN "deltallm_organizationtierassignment" b
      ON b."organization_id" = a."organization_id"
     AND b."assignment_id" > a."assignment_id"
    WHERE a."enabled" IS TRUE
      AND b."enabled" IS TRUE
      AND a."assignment_type" = 'primary'
      AND b."assignment_type" = 'primary'
      AND (a."starts_at" IS NULL OR b."ends_at" IS NULL OR a."starts_at" < b."ends_at")
      AND (a."ends_at" IS NULL OR b."starts_at" IS NULL OR a."ends_at" > b."starts_at")
  ) THEN
    RAISE EXCEPTION 'Cannot enforce primary tier assignment invariant: organizations have overlapping enabled primary assignments';
  END IF;
END $$;

CREATE INDEX "deltallm_tiermodelpolicy_capacity_pool_fk_idx"
  ON "deltallm_tiermodelpolicy"("tier_version_id", "capacity_pool_key", "callable_key");

ALTER TABLE "deltallm_tiermodelpolicy"
  ADD CONSTRAINT "deltallm_tiermodelpolicy_capacity_pool_fkey"
  FOREIGN KEY ("tier_version_id", "capacity_pool_key", "callable_key")
  REFERENCES "deltallm_tiercapacitypool"("tier_version_id", "pool_key", "callable_key")
  ON DELETE NO ACTION
  ON UPDATE CASCADE
  DEFERRABLE INITIALLY DEFERRED;

CREATE EXTENSION IF NOT EXISTS btree_gist;

ALTER TABLE "deltallm_organizationtierassignment"
  ADD CONSTRAINT "deltallm_orgtierassignment_primary_no_overlap"
  EXCLUDE USING gist (
    "organization_id" WITH =,
    tsrange(
      COALESCE("starts_at", '-infinity'::timestamp),
      COALESCE("ends_at", 'infinity'::timestamp),
      '[)'
    ) WITH &&
  )
  WHERE ("enabled" IS TRUE AND "assignment_type" = 'primary');
