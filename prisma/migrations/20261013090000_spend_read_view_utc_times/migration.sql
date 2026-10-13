-- Let time-range filters reach the indexes of both spend read view branches.
--
-- The legacy table stores UTC wall-clock times as `timestamp`; the native
-- table uses `timestamptz`. With mixed types, the UNION ALL converts the
-- legacy columns with the session time zone, and PostgreSQL then keeps every
-- start_time filter above the union. A log page read the whole organization
-- or team history and sorted it. Converting the legacy columns explicitly as
-- UTC gives both branches the same type, so the filters and the ORDER BY ...
-- LIMIT go into each branch. It also stops the result from depending on the
-- session time zone. The column names and types of the view do not change.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

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
  legacy.rerank_units,
  legacy.start_time AT TIME ZONE 'UTC', legacy.end_time AT TIME ZONE 'UTC',
  legacy.latency_ms, legacy.cache_hit, legacy.cache_key, legacy.request_tags,
  legacy.unpriced_reason, legacy.pricing_fields_used, legacy.usage_snapshot,
  legacy.metadata, legacy.status, legacy.http_status_code, legacy.error_type
FROM "deltallm_spendlog_events" AS legacy
WHERE NOT EXISTS (
  SELECT 1 FROM "deltallm_accounting_usage_facts_v2" AS v2
  WHERE v2.event_id = legacy.id
);

COMMIT;
