-- Run with psql -X -v ON_ERROR_STOP=1 -f this_file against an isolated migrated DB.
-- The temporary shadow table and all rows disappear at rollback/disconnect.
BEGIN;
CREATE TEMP TABLE deltallm_realtime_billing_intents
    (LIKE public.deltallm_realtime_billing_intents INCLUDING ALL);
INSERT INTO deltallm_realtime_billing_intents
    (operation_id, session_id, snapshot, expires_at)
SELECT 'profile-' || n, 'profile-session', '{"benchmark":true}'::jsonb,
    CURRENT_TIMESTAMP + INTERVAL '5 minutes'
FROM generate_series(1, 10000) AS n;
ANALYZE deltallm_realtime_billing_intents;

-- Before: existing bounded row lookup and lock.
EXPLAIN (ANALYZE, BUFFERS)
SELECT state, receipt_facts FROM deltallm_realtime_billing_intents
WHERE operation_id='profile-5000' AND snapshot='{"benchmark":true}'::jsonb
FOR UPDATE;

-- After: timing uses the same row lookup and lock, with two extra projections.
EXPLAIN (ANALYZE, BUFFERS)
SELECT state, receipt_facts, created_at::text AS started_at,
    CURRENT_TIMESTAMP::text AS completed_at
FROM deltallm_realtime_billing_intents
WHERE operation_id='profile-5000' AND snapshot='{"benchmark":true}'::jsonb
FOR UPDATE;
ROLLBACK;
