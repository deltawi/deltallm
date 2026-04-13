ALTER TABLE "deltallm_batch_create_session"
ADD CONSTRAINT "deltallm_batch_create_session_status_chk"
CHECK ("status" IN ('staged', 'completed', 'failed_retryable', 'failed_permanent', 'expired'));

CREATE INDEX "deltallm_batch_create_session_stage_artifact_idx"
ON "deltallm_batch_create_session"("staged_storage_backend", "staged_storage_key");
