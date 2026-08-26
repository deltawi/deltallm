CREATE INDEX CONCURRENTLY IF NOT EXISTS
  deltallm_batch_job_user_created_idx
ON deltallm_batch_job (created_by_user_id, created_at);
