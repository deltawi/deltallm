-- Audit log composite indexes for common scoped/time-ordered queries.
CREATE INDEX IF NOT EXISTS "deltallm_auditevent_org_occurred_at_idx"
  ON "deltallm_auditevent" ("organization_id", "occurred_at");

CREATE INDEX IF NOT EXISTS "deltallm_auditevent_action_occurred_at_idx"
  ON "deltallm_auditevent" ("action", "occurred_at");

CREATE INDEX IF NOT EXISTS "deltallm_auditevent_status_occurred_at_idx"
  ON "deltallm_auditevent" ("status", "occurred_at");

-- Spend log composite indexes for filtered dashboards and pagination.
CREATE INDEX IF NOT EXISTS "deltallm_spendlogs_team_time_idx"
  ON "deltallm_spendlogs" ("team_id", "start_time");

CREATE INDEX IF NOT EXISTS "deltallm_spendlogs_api_key_time_idx"
  ON "deltallm_spendlogs" ("api_key", "start_time");

CREATE INDEX IF NOT EXISTS "deltallm_spendlogs_user_time_idx"
  ON "deltallm_spendlogs" ("user", "start_time");

CREATE INDEX IF NOT EXISTS "deltallm_spendlogs_model_time_idx"
  ON "deltallm_spendlogs" ("model", "start_time");

-- request_tags filtering uses containment operators; use GIN instead of BTREE.
DROP INDEX IF EXISTS "deltallm_spendlogs_request_tags_idx";
CREATE INDEX IF NOT EXISTS "deltallm_spendlogs_request_tags_gin_idx"
  ON "deltallm_spendlogs" USING GIN ("request_tags");
