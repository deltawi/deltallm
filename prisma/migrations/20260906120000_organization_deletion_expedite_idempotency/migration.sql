-- Persist the one-time recovery-window waiver and its replay identity on the
-- deletion job. All columns are nullable so existing jobs and older binaries
-- remain compatible during a migration-first rollout.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '15min';

ALTER TABLE deltallm_organizationdeletionjob
  ADD COLUMN expedited_at TIMESTAMPTZ,
  ADD COLUMN expedited_by_account_id TEXT,
  ADD COLUMN expedite_previous_not_before_at TIMESTAMPTZ,
  ADD COLUMN expedite_idempotency_key VARCHAR(200),
  ADD COLUMN expedite_request_hash CHAR(64);

ALTER TABLE deltallm_organizationdeletionjob
  ADD CONSTRAINT deltallm_orgdeletionjob_expedite_marker_check CHECK (
    (
      expedited_at IS NULL
      AND expedited_by_account_id IS NULL
      AND expedite_previous_not_before_at IS NULL
      AND expedite_idempotency_key IS NULL
      AND expedite_request_hash IS NULL
    ) OR (
      expedited_at IS NOT NULL
      AND expedite_previous_not_before_at IS NOT NULL
      AND expedite_previous_not_before_at > expedited_at
      AND expedite_idempotency_key IS NOT NULL
      AND expedite_request_hash IS NOT NULL
    )
  ),
  ADD CONSTRAINT deltallm_orgdeletionjob_expedite_key_check CHECK (
    expedite_idempotency_key IS NULL OR (
      length(btrim(expedite_idempotency_key)) BETWEEN 1 AND 200
      AND expedite_idempotency_key = btrim(expedite_idempotency_key)
    )
  ),
  ADD CONSTRAINT deltallm_orgdeletionjob_expedite_hash_check CHECK (
    expedite_request_hash IS NULL
    OR expedite_request_hash ~ '^[0-9a-f]{64}$'
  );
