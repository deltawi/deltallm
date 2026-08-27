-- Restore the documented implicit model-group contract after the shared
-- publication-invariant migration has completed.
BEGIN;

DROP INDEX IF EXISTS "deltallm_modeldeployment_model_name_key";

UPDATE deltallm_modeldeployment AS deployment
SET model_name = restore.model_name
FROM "_deltallm_model_name_restore_20260823" AS restore
WHERE deployment.deployment_id = restore.deployment_id;

DROP TABLE "_deltallm_model_name_restore_20260823";

CREATE INDEX IF NOT EXISTS "deltallm_modeldeployment_model_name_idx"
  ON "deltallm_modeldeployment"("model_name");

COMMIT;
