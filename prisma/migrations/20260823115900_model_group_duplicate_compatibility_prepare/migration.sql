-- The already-shared publication-invariant migration temporarily requires
-- model_name uniqueness. Preserve duplicate public model names across that
-- migration without rewriting its history.
BEGIN;

CREATE TABLE "_deltallm_model_name_restore_20260823" (
  "deployment_id" TEXT NOT NULL,
  "model_name" TEXT NOT NULL,

  CONSTRAINT "_deltallm_model_name_restore_20260823_pkey"
    PRIMARY KEY ("deployment_id")
);

DO $$
DECLARE
  duplicate_row RECORD;
  temporary_name TEXT;
BEGIN
  FOR duplicate_row IN
    SELECT deployment_id, model_name
    FROM (
      SELECT
        deployment_id,
        model_name,
        ROW_NUMBER() OVER (
          PARTITION BY model_name
          ORDER BY created_at ASC, deployment_id ASC
        ) AS duplicate_rank
      FROM deltallm_modeldeployment
    ) AS ranked_deployments
    WHERE duplicate_rank > 1
    ORDER BY model_name ASC, deployment_id ASC
  LOOP
    INSERT INTO "_deltallm_model_name_restore_20260823" (
      deployment_id,
      model_name
    )
    VALUES (duplicate_row.deployment_id, duplicate_row.model_name);

    temporary_name :=
      '__deltallm_model_name_migration__' || duplicate_row.deployment_id;
    WHILE EXISTS (
      SELECT 1
      FROM deltallm_modeldeployment
      WHERE model_name = temporary_name
    ) LOOP
      temporary_name := temporary_name || '_';
    END LOOP;

    UPDATE deltallm_modeldeployment
    SET model_name = temporary_name
    WHERE deployment_id = duplicate_row.deployment_id;
  END LOOP;
END $$;

COMMIT;
