-- Native usage and audit projection stays off the inference path.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

CREATE TABLE "deltallm_accounting_usage_facts_v2" (
  "event_id" TEXT NOT NULL,
  "accounting_sequence" BIGINT NOT NULL,
  "protocol_generation" BIGINT NOT NULL CHECK ("protocol_generation">0),
  "source_sha256" BYTEA NOT NULL CHECK (octet_length("source_sha256")=32),
  "operation_id" TEXT NOT NULL,
  "request_id" TEXT NOT NULL,
  "call_type" TEXT NOT NULL,
  "api_key" TEXT NOT NULL,
  "user_id" TEXT,
  "team_id" TEXT,
  "organization_id" TEXT,
  "owner_account_id" TEXT,
  "end_user_id" TEXT,
  "model" TEXT NOT NULL,
  "deployment_model" TEXT,
  "provider" TEXT,
  "api_base" TEXT,
  "spend" DOUBLE PRECISION NOT NULL,
  "provider_cost" DOUBLE PRECISION,
  "spend_exact" NUMERIC(38,18) NOT NULL CHECK ("spend_exact">=0 AND "spend_exact"<100000000000000000000),
  "provider_cost_exact" NUMERIC(38,18) CHECK ("provider_cost_exact">=0 AND "provider_cost_exact"<100000000000000000000),
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
  "start_time" TIMESTAMPTZ(6) NOT NULL,
  "end_time" TIMESTAMPTZ(6) NOT NULL,
  "latency_ms" INTEGER,
  "cache_hit" BOOLEAN NOT NULL DEFAULT FALSE,
  "cache_key" TEXT,
  "request_tags" TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  "unpriced_reason" TEXT,
  "pricing_fields_used" JSONB,
  "usage_snapshot" JSONB,
  "metadata" JSONB,
  "status" TEXT NOT NULL DEFAULT 'success',
  "http_status_code" INTEGER,
  "error_type" TEXT,
  "source_occurred_at" TIMESTAMPTZ(6) NOT NULL,
  "projected_at" TIMESTAMPTZ(6) NOT NULL DEFAULT NOW(),
  CONSTRAINT "deltallm_accounting_usage_facts_v2_pkey" PRIMARY KEY ("event_id"),
  CONSTRAINT "deltallm_usage_facts_v2_sequence_key" UNIQUE ("accounting_sequence"),
  CHECK ("total_tokens">=0 AND "input_tokens">=0 AND "output_tokens">=0
    AND "cached_input_tokens">=0 AND "cached_output_tokens">=0
    AND "input_audio_tokens">=0 AND "output_audio_tokens">=0
    AND "input_characters">=0 AND "output_characters">=0
    AND "image_count">=0 AND "rerank_units">=0
    AND "duration_seconds">=0 AND "duration_seconds"<'Infinity'::double precision)
);

CREATE INDEX "deltallm_usage_facts_v2_org_time_id_idx"
  ON "deltallm_accounting_usage_facts_v2" ("organization_id", "start_time", "event_id");
CREATE INDEX "deltallm_usage_facts_v2_owner_time_id_idx"
  ON "deltallm_accounting_usage_facts_v2" ("owner_account_id", "start_time", "event_id");
CREATE INDEX "deltallm_usage_facts_v2_team_time_id_idx"
  ON "deltallm_accounting_usage_facts_v2" ("team_id", "start_time", "event_id");
CREATE INDEX "deltallm_usage_facts_v2_api_key_time_id_idx"
  ON "deltallm_accounting_usage_facts_v2" ("api_key", "start_time", "event_id");
CREATE INDEX "deltallm_usage_facts_v2_user_time_id_idx"
  ON "deltallm_accounting_usage_facts_v2" ("user_id", "start_time", "event_id");
CREATE INDEX "deltallm_usage_facts_v2_model_time_id_idx"
  ON "deltallm_accounting_usage_facts_v2" ("model", "start_time", "event_id");
CREATE INDEX "deltallm_usage_facts_v2_provider_time_id_idx"
  ON "deltallm_accounting_usage_facts_v2" ("provider", "start_time", "event_id");
CREATE INDEX "deltallm_usage_facts_v2_time_id_idx"
  ON "deltallm_accounting_usage_facts_v2" ("start_time", "event_id");
CREATE INDEX "deltallm_usage_facts_v2_request_tags_gin_idx"
  ON "deltallm_accounting_usage_facts_v2" USING GIN ("request_tags");

CREATE TABLE "deltallm_accounting_usage_rollups_v2" (
  "bucket_kind" TEXT NOT NULL CHECK ("bucket_kind" IN ('day', 'month')),
  "bucket_start" TIMESTAMPTZ(6) NOT NULL,
  "organization_id" TEXT NOT NULL DEFAULT '',
  "team_id" TEXT NOT NULL DEFAULT '',
  "user_id" TEXT NOT NULL DEFAULT '',
  "owner_account_id" TEXT NOT NULL DEFAULT '',
  "api_key" TEXT NOT NULL DEFAULT '',
  "model" TEXT NOT NULL DEFAULT '',
  "provider" TEXT NOT NULL DEFAULT '',
  "status" TEXT NOT NULL DEFAULT 'success',
  "rollup_shard" SMALLINT NOT NULL CHECK ("rollup_shard" BETWEEN 0 AND 63),
  "request_count" BIGINT NOT NULL DEFAULT 0,
  "spend_exact" NUMERIC(38,18) NOT NULL DEFAULT 0,
  "provider_cost_exact" NUMERIC(38,18) NOT NULL DEFAULT 0,
  "total_tokens" BIGINT NOT NULL DEFAULT 0,
  "input_tokens" BIGINT NOT NULL DEFAULT 0,
  "output_tokens" BIGINT NOT NULL DEFAULT 0,
  "cached_input_tokens" BIGINT NOT NULL DEFAULT 0,
  "cached_output_tokens" BIGINT NOT NULL DEFAULT 0,
  "updated_at" TIMESTAMPTZ(6) NOT NULL DEFAULT NOW(),
  CONSTRAINT "deltallm_usage_rollups_v2_pkey" PRIMARY KEY (
    "bucket_kind", "bucket_start", "organization_id", "team_id", "user_id",
    "owner_account_id", "api_key", "model", "provider", "status", "rollup_shard"
  )
);

CREATE INDEX "deltallm_usage_rollups_v2_org_bucket_idx"
  ON "deltallm_accounting_usage_rollups_v2" ("organization_id", "bucket_kind", "bucket_start");
CREATE INDEX "deltallm_usage_rollups_v2_team_bucket_idx"
  ON "deltallm_accounting_usage_rollups_v2" ("team_id", "bucket_kind", "bucket_start");
CREATE INDEX "deltallm_usage_rollups_v2_owner_bucket_idx"
  ON "deltallm_accounting_usage_rollups_v2" ("owner_account_id", "bucket_kind", "bucket_start");

-- Readers use one stable contract during the rolling transition. V2 wins on an
-- accounting event id; retained legacy and non-accounting history remains visible.
CREATE OR REPLACE VIEW "deltallm_spend_read_events_v2" AS
SELECT
  event_id AS id, request_id, call_type, api_key, user_id, team_id,
  organization_id, owner_account_id, end_user_id, model, deployment_model,
  provider, api_base, spend, provider_cost, spend_exact, provider_cost_exact,
  billing_unit, pricing_tier, total_tokens, input_tokens, output_tokens,
  cached_input_tokens, cached_output_tokens, input_audio_tokens,
  output_audio_tokens, input_characters, output_characters, duration_seconds,
  image_count, rerank_units, start_time, end_time, latency_ms, cache_hit,
  cache_key, request_tags, unpriced_reason, pricing_fields_used, usage_snapshot,
  metadata, status, http_status_code, error_type
FROM "deltallm_accounting_usage_facts_v2"
UNION ALL
SELECT
  legacy.id, legacy.request_id, legacy.call_type, legacy.api_key, legacy.user_id,
  legacy.team_id, legacy.organization_id, legacy.owner_account_id,
  legacy.end_user_id, legacy.model, legacy.deployment_model, legacy.provider,
  legacy.api_base, legacy.spend, legacy.provider_cost, legacy.spend_exact,
  legacy.provider_cost_exact, legacy.billing_unit, legacy.pricing_tier,
  legacy.total_tokens, legacy.input_tokens, legacy.output_tokens,
  legacy.cached_input_tokens, legacy.cached_output_tokens,
  legacy.input_audio_tokens, legacy.output_audio_tokens, legacy.input_characters,
  legacy.output_characters, legacy.duration_seconds, legacy.image_count,
  legacy.rerank_units, legacy.start_time, legacy.end_time, legacy.latency_ms,
  legacy.cache_hit, legacy.cache_key, legacy.request_tags,
  legacy.unpriced_reason, legacy.pricing_fields_used, legacy.usage_snapshot,
  legacy.metadata, legacy.status, legacy.http_status_code, legacy.error_type
FROM "deltallm_spendlog_events" AS legacy
WHERE NOT EXISTS (
  SELECT 1 FROM "deltallm_accounting_usage_facts_v2" AS v2
  WHERE v2.event_id = legacy.id
);

-- One extra index entry per terminal event. Reserved events do not enter it.
CREATE INDEX deltallm_accounting_terminal_projection_idx
  ON deltallm_accounting_events(protocol_name,generation,accounting_partition,sequence)
  WHERE event_type IN ('finalized','reconciled');

COMMIT;
