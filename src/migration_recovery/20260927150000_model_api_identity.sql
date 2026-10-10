-- This recovery is intentionally separate from the immutable v0.1.48 migration.
-- It may run only after that migration failed because pgcrypto.digest was missing.
DO $model_api_identity_recovery$
DECLARE
  matching_failure_count INTEGER;
BEGIN
  SELECT count(*) INTO matching_failure_count
  FROM "_prisma_migrations"
  WHERE "migration_name" = '20260927150000_model_api_identity'
    AND "finished_at" IS NULL
    AND "rolled_back_at" IS NULL
    AND "applied_steps_count" = 0
    AND "logs" LIKE '%function digest(text, unknown) does not exist%';

  IF matching_failure_count <> 1 THEN
    RAISE EXCEPTION
      'model API identity recovery requires exactly one active missing-digest failure';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'deltallm_platformaccount'
      AND column_name = 'api_namespace'
  ) OR EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'deltallm_model'
      AND column_name = 'display_name'
  ) OR to_regclass('public.deltallm_platformaccount_api_namespace_ci_key') IS NOT NULL THEN
    RAISE EXCEPTION
      'model API identity recovery found unexpected partial schema changes';
  END IF;
END
$model_api_identity_recovery$;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $model_api_identity_dependency$
BEGIN
  IF to_regprocedure('digest(text,text)') IS NULL THEN
    RAISE EXCEPTION
      'pgcrypto digest(text,text) is not visible in the database search path';
  END IF;
END
$model_api_identity_dependency$;
