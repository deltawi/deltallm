CREATE TABLE IF NOT EXISTS "deltallm_spendlog_events" (
  "id" TEXT NOT NULL,
  "request_id" TEXT NOT NULL,
  "call_type" TEXT NOT NULL,
  "api_key" TEXT NOT NULL,
  "user_id" TEXT,
  "team_id" TEXT,
  "organization_id" TEXT,
  "end_user_id" TEXT,
  "model" TEXT NOT NULL,
  "deployment_model" TEXT,
  "provider" TEXT,
  "api_base" TEXT,
  "spend" DOUBLE PRECISION NOT NULL,
  "provider_cost" DOUBLE PRECISION,
  "billing_unit" TEXT,
  "pricing_tier" TEXT,
  "total_tokens" INTEGER NOT NULL DEFAULT 0,
  "input_tokens" INTEGER NOT NULL DEFAULT 0,
  "output_tokens" INTEGER NOT NULL DEFAULT 0,
  "cached_input_tokens" INTEGER NOT NULL DEFAULT 0,
  "cached_output_tokens" INTEGER NOT NULL DEFAULT 0,
  "input_audio_tokens" INTEGER NOT NULL DEFAULT 0,
  "output_audio_tokens" INTEGER NOT NULL DEFAULT 0,
  "input_characters" INTEGER NOT NULL DEFAULT 0,
  "output_characters" INTEGER NOT NULL DEFAULT 0,
  "duration_seconds" DOUBLE PRECISION NOT NULL DEFAULT 0,
  "image_count" INTEGER NOT NULL DEFAULT 0,
  "rerank_units" INTEGER NOT NULL DEFAULT 0,
  "start_time" TIMESTAMP(3) NOT NULL,
  "end_time" TIMESTAMP(3) NOT NULL,
  "latency_ms" INTEGER,
  "cache_hit" BOOLEAN NOT NULL DEFAULT false,
  "cache_key" TEXT,
  "request_tags" TEXT[] DEFAULT ARRAY[]::TEXT[],
  "unpriced_reason" TEXT,
  "pricing_fields_used" JSONB,
  "usage_snapshot" JSONB,
  "metadata" JSONB,
  CONSTRAINT "deltallm_spendlog_events_pkey" PRIMARY KEY ("id")
);

CREATE TABLE IF NOT EXISTS "deltallm_teammodelspend" (
  "team_id" TEXT NOT NULL,
  "model" TEXT NOT NULL,
  "spend" DOUBLE PRECISION NOT NULL DEFAULT 0,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "deltallm_teammodelspend_pkey" PRIMARY KEY ("team_id", "model")
);

CREATE INDEX IF NOT EXISTS "deltallm_spendlog_events_org_time_idx"
  ON "deltallm_spendlog_events" ("organization_id", "start_time");

CREATE INDEX IF NOT EXISTS "deltallm_spendlog_events_team_time_idx"
  ON "deltallm_spendlog_events" ("team_id", "start_time");

CREATE INDEX IF NOT EXISTS "deltallm_spendlog_events_api_key_time_idx"
  ON "deltallm_spendlog_events" ("api_key", "start_time");

CREATE INDEX IF NOT EXISTS "deltallm_spendlog_events_user_time_idx"
  ON "deltallm_spendlog_events" ("user_id", "start_time");

CREATE INDEX IF NOT EXISTS "deltallm_spendlog_events_model_time_idx"
  ON "deltallm_spendlog_events" ("model", "start_time");

CREATE INDEX IF NOT EXISTS "deltallm_spendlog_events_provider_time_idx"
  ON "deltallm_spendlog_events" ("provider", "start_time");

CREATE INDEX IF NOT EXISTS "deltallm_spendlog_events_start_time_idx"
  ON "deltallm_spendlog_events" ("start_time");

CREATE INDEX IF NOT EXISTS "deltallm_spendlog_events_request_tags_gin_idx"
  ON "deltallm_spendlog_events" USING GIN ("request_tags");

CREATE INDEX IF NOT EXISTS "deltallm_teammodelspend_team_idx"
  ON "deltallm_teammodelspend" ("team_id");
