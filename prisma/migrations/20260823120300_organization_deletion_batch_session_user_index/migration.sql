CREATE INDEX CONCURRENTLY IF NOT EXISTS
  deltallm_batch_create_session_user_status_created_idx
ON deltallm_batch_create_session (created_by_user_id, status, created_at);
