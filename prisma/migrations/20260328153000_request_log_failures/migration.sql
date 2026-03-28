ALTER TABLE deltallm_spendlog_events
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'success',
    ADD COLUMN IF NOT EXISTS http_status_code INTEGER,
    ADD COLUMN IF NOT EXISTS error_type TEXT;
