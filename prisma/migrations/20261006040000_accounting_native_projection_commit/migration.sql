-- Source payloads stay in PostgreSQL. One fenced page commits all read effects.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

CREATE FUNCTION deltallm_accounting_usage_count(
 p_usage JSONB,p_snapshot JSONB,p_key TEXT,p_default INTEGER
) RETURNS INTEGER LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE count_value INTEGER;
BEGIN
 count_value:=COALESCE((p_usage->>p_key)::integer,(p_snapshot->>p_key)::integer,p_default);
 IF count_value IS NULL OR count_value<0 THEN
  RAISE EXCEPTION 'accounting_read_model_usage' USING ERRCODE='P0001';
 END IF;
 RETURN count_value;
END;
$$;

CREATE FUNCTION deltallm_accounting_usage_row(
 p_event deltallm_accounting_events,p_attribution JSONB
) RETURNS JSONB LANGUAGE plpgsql STABLE SET TimeZone='UTC' AS $$
DECLARE
 spend JSONB:=p_event.payload_json->'spend';
 metadata JSONB; billing JSONB; usage JSONB; snapshot JSONB;
 charge NUMERIC; provider_charge NUMERIC; started TIMESTAMPTZ; finished TIMESTAMPTZ;
BEGIN
 IF p_event.outcome<>'completed' THEN RETURN NULL; END IF;
 IF jsonb_typeof(spend) IS DISTINCT FROM 'object'
  OR jsonb_typeof(p_attribution) IS DISTINCT FROM 'object'
  OR p_event.payload_json->>'exact_charge' IS NULL
  OR COALESCE(length(spend->>'request_id'),0) NOT BETWEEN 1 AND 256
  OR COALESCE(length(spend->>'call_type'),0) NOT BETWEEN 1 AND 256
  OR spend->>'start_time' IS NULL OR spend->>'end_time' IS NULL
  OR (spend->>'owner_snapshot_complete')::boolean IS FALSE
  OR EXISTS (SELECT 1 FROM unnest(ARRAY[
   'api_key','user_id','team_id','organization_id','owner_account_id','end_user_id','model','call_type'
  ]) field WHERE spend->>field IS DISTINCT FROM p_attribution->>field) THEN
  RAISE EXCEPTION 'accounting_read_model_attribution' USING ERRCODE='P0001';
 END IF;
 charge:=(p_event.payload_json->>'exact_charge')::numeric;
 IF charge<0 OR charge>=100000000000000000000 OR charge IS DISTINCT FROM charge::numeric(38,18)
  OR COALESCE(spend->>'cost_exact',spend->>'cost')::numeric IS DISTINCT FROM charge THEN
  RAISE EXCEPTION 'accounting_read_model_charge' USING ERRCODE='P0001';
 END IF;
 metadata:=COALESCE(NULLIF(spend->'metadata','null'::jsonb),'{}'::jsonb);
 usage:=COALESCE(NULLIF(spend->'usage','null'::jsonb),'{}'::jsonb);
 billing:=COALESCE(NULLIF(metadata->'billing','null'::jsonb),'{}'::jsonb);
 snapshot:=COALESCE(NULLIF(billing->'usage_snapshot','null'::jsonb),'{}'::jsonb);
 IF jsonb_typeof(metadata)<>'object' OR jsonb_typeof(usage)<>'object'
  OR jsonb_typeof(billing)<>'object' OR jsonb_typeof(snapshot)<>'object' THEN
  RAISE EXCEPTION 'accounting_read_model_payload' USING ERRCODE='P0001';
 END IF;
 metadata:=metadata||jsonb_build_object('_deltallm_reporting_writer_version',2);
 -- Only retained reporting mirrors use the old cost field. Budget charges use charge above.
 provider_charge:=COALESCE(spend->>'provider_cost_exact',metadata->>'provider_cost')::numeric;
 IF provider_charge<0 OR provider_charge>=100000000000000000000
  OR provider_charge IS DISTINCT FROM provider_charge::numeric(38,18) THEN
  RAISE EXCEPTION 'accounting_read_model_provider_cost' USING ERRCODE='P0001';
 END IF;
 started:=(spend->>'start_time')::timestamptz;
 finished:=(spend->>'end_time')::timestamptz;
 RETURN jsonb_build_object(
  'event_id',to_jsonb(p_event.event_id),
  'id',to_jsonb(p_event.event_id),
  'accounting_sequence',to_jsonb(p_event.sequence),
  'protocol_generation',to_jsonb(p_event.generation),
  'source_sha256',to_jsonb(chr(92)||'x'||encode(sha256(convert_to((to_jsonb(p_event)-'created_at')::text||p_attribution::text,'UTF8')),'hex')),
  'operation_id',to_jsonb(p_event.operation_id),
  'request_id',spend->'request_id',
  'call_type',spend->'call_type',
  'api_key',spend->'api_key',
  'user_id',spend->'user_id',
  'team_id',spend->'team_id',
  'organization_id',spend->'organization_id',
  'owner_account_id',spend->'owner_account_id',
  'end_user_id',spend->'end_user_id',
  'model',spend->'model',
  'deployment_model',metadata->'deployment_model',
  'provider',COALESCE(NULLIF(metadata->'provider','null'::jsonb),p_attribution->'provider'),
  'api_base',metadata->'api_base',
  'cache_key',metadata->'cache_key',
  'pricing_tier',metadata->'pricing_tier',
  'spend',to_jsonb(charge::double precision),
  'spend_exact',to_jsonb(charge::text),
  'provider_cost',to_jsonb(provider_charge::double precision),
  'provider_cost_exact',to_jsonb(provider_charge::text),
  'billing_unit',billing->'billing_unit',
  'total_tokens',to_jsonb(deltallm_accounting_usage_count(usage,snapshot,'total_tokens',deltallm_accounting_usage_count(usage,snapshot,'prompt_tokens',0)+deltallm_accounting_usage_count(usage,snapshot,'completion_tokens',0)+deltallm_accounting_usage_count(usage,snapshot,'input_audio_tokens',0)+deltallm_accounting_usage_count(usage,snapshot,'output_audio_tokens',0))),
  'input_tokens',to_jsonb(deltallm_accounting_usage_count(usage,snapshot,'prompt_tokens',0)),
  'output_tokens',to_jsonb(deltallm_accounting_usage_count(usage,snapshot,'completion_tokens',0)),
  'cached_input_tokens',to_jsonb(deltallm_accounting_usage_count(usage,snapshot,'prompt_tokens_cached',0)),
  'cached_output_tokens',to_jsonb(deltallm_accounting_usage_count(usage,snapshot,'completion_tokens_cached',0))
 )||jsonb_build_object(
  'input_audio_tokens',to_jsonb(deltallm_accounting_usage_count(usage,snapshot,'input_audio_tokens',0)),
  'output_audio_tokens',to_jsonb(deltallm_accounting_usage_count(usage,snapshot,'output_audio_tokens',0)),
  'input_characters',to_jsonb(deltallm_accounting_usage_count(usage,snapshot,'input_characters',0)),
  'output_characters',to_jsonb(deltallm_accounting_usage_count(usage,snapshot,'output_characters',0)),
  'image_count',to_jsonb(COALESCE((usage->>'images')::integer,(snapshot->>'image_count')::integer,(snapshot->>'images')::integer,0)),
  'rerank_units',to_jsonb(deltallm_accounting_usage_count(usage,snapshot,'rerank_units',0)),
  'duration_seconds',to_jsonb(COALESCE((usage->>'duration_seconds')::double precision,(snapshot->>'duration_seconds')::double precision,0)),
  'start_time',to_jsonb(started),
  'end_time',to_jsonb(finished),
  'latency_ms',to_jsonb(greatest(0,trunc(extract(epoch FROM (finished-started))*1000))::integer),
  'cache_hit',to_jsonb(COALESCE((spend->>'cache_hit')::boolean,FALSE)),
  'request_tags',CASE WHEN jsonb_typeof(metadata->'tags')='array' THEN metadata->'tags' ELSE '[]'::jsonb END,
  'unpriced_reason',billing->'unpriced_reason',
  'pricing_fields_used',CASE WHEN jsonb_typeof(billing->'pricing_fields_used')='array' THEN billing->'pricing_fields_used' ELSE 'null'::jsonb END,
  'usage_snapshot',NULLIF(snapshot,'{}'::jsonb),
  'metadata',metadata,
  'status',to_jsonb('success'::text),
  'http_status_code',spend->'http_status_code',
  'error_type',spend->'error_type',
  'source_occurred_at',to_jsonb(p_event.occurred_at),
  'rollup_shard',to_jsonb(p_event.accounting_partition)
 );
END;
$$;

CREATE FUNCTION deltallm_accounting_audit_row(p_event deltallm_accounting_events,p_attribution JSONB)
RETURNS JSONB LANGUAGE plpgsql STABLE SET TimeZone='UTC' AS $$
DECLARE envelope JSONB:=p_event.audit_envelope_json; redacted JSONB; audit JSONB; audit_id UUID;
BEGIN
 redacted:=envelope->'redacted_payload'; audit:=redacted->'event';
 IF envelope->>'record_type' IS DISTINCT FROM 'audit_event'
  OR jsonb_typeof(redacted) IS DISTINCT FROM 'object'
  OR COALESCE(redacted->'payloads','[]'::jsonb)<>'[]'::jsonb
  OR jsonb_typeof(audit) IS DISTINCT FROM 'object'
  OR COALESCE(length(audit->>'action'),0) NOT BETWEEN 1 AND 256
  OR envelope->>'organization_id' IS DISTINCT FROM p_attribution->>'organization_id'
  OR audit->>'organization_id' IS DISTINCT FROM p_attribution->>'organization_id'
  OR audit->>'api_key' IS DISTINCT FROM p_attribution->>'api_key'
  OR audit->>'request_id' IS DISTINCT FROM p_event.operation_id
  OR COALESCE(NULLIF(jsonb_typeof(audit->'metadata'),'null'),'object')<>'object' THEN
  RAISE EXCEPTION 'accounting_read_model_audit' USING ERRCODE='P0001';
 END IF;
 IF envelope->>'event_id' ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' THEN
  audit_id:=(envelope->>'event_id')::uuid;
 ELSE
  -- Recovery records have named event IDs. This fixed namespace retains one audit identity.
  audit_id:=md5('deltallm-accounting-audit:v2:'||p_event.event_id)::uuid;
 END IF;
 RETURN audit||jsonb_build_object(
  'event_id',audit_id,'occurred_at',p_event.occurred_at,'content_stored',FALSE
 );
END;
$$;

CREATE FUNCTION deltallm_accounting_project_read_models(
 p_generation BIGINT,p_worker_id TEXT,p_lease_token UUID,p_partition INTEGER,
 p_after_sequence BIGINT,p_sequences BIGINT[]
) RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE
 checkpoint deltallm_accounting_projection_checkpoints%ROWTYPE;
 first_keys BIGINT[]; source_count INTEGER; source_bytes BIGINT;
 usage_count INTEGER; rollup_count INTEGER; audit_count INTEGER;
BEGIN
 IF p_generation IS NULL OR p_generation<1
  OR COALESCE(length(p_worker_id),0) NOT BETWEEN 1 AND 256 OR p_lease_token IS NULL
  OR p_partition IS NULL OR p_partition NOT BETWEEN 0 AND 63
  OR p_after_sequence IS NULL OR p_after_sequence<0
  OR COALESCE(cardinality(p_sequences),0) NOT BETWEEN 1 AND 256
  OR EXISTS (SELECT 1 FROM unnest(p_sequences) key WHERE key IS NULL OR key<=p_after_sequence)
  OR p_sequences IS DISTINCT FROM (
   SELECT array_agg(DISTINCT key ORDER BY key) FROM unnest(p_sequences) key
  ) THEN
  RAISE EXCEPTION 'accounting_read_model_page' USING ERRCODE='P0001';
 END IF;
 PERFORM set_config('TimeZone','UTC',TRUE);
 SELECT c.* INTO checkpoint FROM deltallm_accounting_projection_checkpoints c
 WHERE c.projection_name='accounting-read-model-v2' AND c.protocol_name='primary'
  AND c.generation=p_generation AND c.accounting_partition=p_partition FOR UPDATE;
 IF NOT FOUND OR checkpoint.lease_owner IS DISTINCT FROM p_worker_id
  OR checkpoint.lease_token IS DISTINCT FROM p_lease_token::text
  OR checkpoint.last_sequence IS DISTINCT FROM p_after_sequence
  OR checkpoint.lease_expires_at IS NULL OR checkpoint.lease_expires_at<=clock_timestamp() THEN
  RETURN 0;
 END IF;
 IF NOT EXISTS (SELECT 1 FROM deltallm_accounting_protocols p
  WHERE p.protocol_name='primary' AND p.generation=p_generation
   AND p.state IN ('active','draining')) THEN
  RAISE EXCEPTION 'accounting_read_model_generation' USING ERRCODE='P0001';
 END IF;
 SELECT array_agg(e.sequence ORDER BY e.sequence) INTO first_keys FROM (
  SELECT e.sequence FROM deltallm_accounting_events e
  WHERE e.protocol_name='primary' AND e.generation=p_generation
   AND e.accounting_partition=p_partition AND e.sequence>p_after_sequence
   AND e.event_type IN ('finalized','reconciled')
  ORDER BY e.sequence LIMIT cardinality(p_sequences)
 ) e;
 IF first_keys IS DISTINCT FROM p_sequences THEN
  RAISE EXCEPTION 'accounting_read_model_prefix' USING ERRCODE='P0001';
 END IF;
 SELECT count(*)::integer,sum(64+octet_length((source.e).payload_json::text)
  +octet_length((source.e).audit_envelope_json::text)) INTO source_count,source_bytes
 FROM (SELECT e::deltallm_accounting_events AS e,b.snapshot->'attribution' AS attribution FROM unnest(p_sequences) key(sequence)
 CROSS JOIN LATERAL (
  SELECT e.* FROM deltallm_accounting_events e WHERE e.sequence=key.sequence
   AND e.protocol_name='primary' AND e.generation=p_generation
   AND e.accounting_partition=p_partition
   AND e.event_type IN ('finalized','reconciled') OFFSET 0
 ) e CROSS JOIN LATERAL (
  SELECT b.snapshot FROM deltallm_billing_operations b WHERE b.operation_id=e.operation_id
   AND b.accounting_protocol='primary' AND b.accounting_generation=p_generation
   AND b.accounting_partition=p_partition OFFSET 0
 ) b) source;
 IF source_count<>cardinality(p_sequences) OR source_bytes>1048576 THEN
  RAISE EXCEPTION 'accounting_read_model_source_bound' USING ERRCODE='P0001';
 END IF;
 -- Existing rows must match the complete normalized facts. Never accept an ID collision.
 IF EXISTS (
  WITH source AS NOT MATERIALIZED (SELECT e::deltallm_accounting_events AS e,b.snapshot->'attribution' AS attribution FROM unnest(p_sequences) key(sequence)
 CROSS JOIN LATERAL (
  SELECT e.* FROM deltallm_accounting_events e WHERE e.sequence=key.sequence
   AND e.protocol_name='primary' AND e.generation=p_generation
   AND e.accounting_partition=p_partition
   AND e.event_type IN ('finalized','reconciled') OFFSET 0
 ) e CROSS JOIN LATERAL (
  SELECT b.snapshot FROM deltallm_billing_operations b WHERE b.operation_id=e.operation_id
   AND b.accounting_protocol='primary' AND b.accounting_generation=p_generation
   AND b.accounting_partition=p_partition OFFSET 0
 ) b), expected AS (
   SELECT jsonb_populate_record(NULL::deltallm_accounting_usage_facts_v2,
    deltallm_accounting_usage_row(e,attribution)) AS row
   FROM source WHERE (e).outcome='completed'
  )
  SELECT 1 FROM expected CROSS JOIN LATERAL (
   SELECT f.* FROM deltallm_accounting_usage_facts_v2 f
   WHERE f.event_id=(expected.row).event_id OFFSET 0
  ) f WHERE to_jsonb(f)-'projected_at' IS DISTINCT FROM to_jsonb(expected.row)-'projected_at'
 ) OR EXISTS (
  WITH source AS NOT MATERIALIZED (SELECT e::deltallm_accounting_events AS e,b.snapshot->'attribution' AS attribution FROM unnest(p_sequences) key(sequence)
 CROSS JOIN LATERAL (
  SELECT e.* FROM deltallm_accounting_events e WHERE e.sequence=key.sequence
   AND e.protocol_name='primary' AND e.generation=p_generation
   AND e.accounting_partition=p_partition
   AND e.event_type IN ('finalized','reconciled') OFFSET 0
 ) e CROSS JOIN LATERAL (
  SELECT b.snapshot FROM deltallm_billing_operations b WHERE b.operation_id=e.operation_id
   AND b.accounting_protocol='primary' AND b.accounting_generation=p_generation
   AND b.accounting_partition=p_partition OFFSET 0
 ) b), expected AS (
   SELECT jsonb_populate_record(NULL::deltallm_auditevent,
    deltallm_accounting_audit_row(e,attribution)) AS row FROM source
  )
  SELECT 1 FROM expected CROSS JOIN LATERAL (
   SELECT a.* FROM deltallm_auditevent a WHERE a.event_id=(expected.row).event_id OFFSET 0
  ) a
  -- A retained legacy audit can have its original projection timestamp.
  WHERE to_jsonb(a)-'occurred_at' IS DISTINCT FROM to_jsonb(expected.row)-'occurred_at'
 ) THEN
  RAISE EXCEPTION 'accounting_read_model_identity' USING ERRCODE='P0001';
 END IF;

WITH source AS NOT MATERIALIZED (SELECT e::deltallm_accounting_events AS e,b.snapshot->'attribution' AS attribution FROM unnest(p_sequences) key(sequence)
 CROSS JOIN LATERAL (
  SELECT e.* FROM deltallm_accounting_events e WHERE e.sequence=key.sequence
   AND e.protocol_name='primary' AND e.generation=p_generation
   AND e.accounting_partition=p_partition
   AND e.event_type IN ('finalized','reconciled') OFFSET 0
 ) e CROSS JOIN LATERAL (
  SELECT b.snapshot FROM deltallm_billing_operations b WHERE b.operation_id=e.operation_id
   AND b.accounting_protocol='primary' AND b.accounting_generation=p_generation
   AND b.accounting_partition=p_partition OFFSET 0
 ) b
), usage_input AS MATERIALIZED (
    SELECT deltallm_accounting_usage_row(e,attribution) AS item
    FROM source WHERE (e).outcome='completed'
), usage_insert AS (
    INSERT INTO deltallm_accounting_usage_facts_v2 (
        event_id, accounting_sequence, protocol_generation, source_sha256, operation_id, request_id, call_type, api_key,
        user_id, team_id, organization_id, owner_account_id, end_user_id, model,
        deployment_model, provider, api_base, spend, provider_cost, spend_exact,
        provider_cost_exact, billing_unit, pricing_tier, total_tokens, input_tokens,
        output_tokens, cached_input_tokens, cached_output_tokens, input_audio_tokens,
        output_audio_tokens, input_characters, output_characters, duration_seconds,
        image_count, rerank_units, start_time, end_time, latency_ms, cache_hit,
        cache_key, request_tags, unpriced_reason, pricing_fields_used, usage_snapshot,
        metadata, status, http_status_code, error_type, source_occurred_at
    )
    SELECT
        item->>'id', (item->>'accounting_sequence')::bigint,
        (item->>'protocol_generation')::bigint,(item->>'source_sha256')::bytea,
        item->>'operation_id', item->>'request_id', item->>'call_type', item->>'api_key',
        item->>'user_id', item->>'team_id', item->>'organization_id',
        item->>'owner_account_id', item->>'end_user_id', item->>'model',
        item->>'deployment_model', item->>'provider', item->>'api_base',
        (item->>'spend')::double precision, (item->>'provider_cost')::double precision,
        (item->>'spend_exact')::numeric, (item->>'provider_cost_exact')::numeric,
        item->>'billing_unit', item->>'pricing_tier',
        COALESCE((item->>'total_tokens')::integer, 0),
        COALESCE((item->>'input_tokens')::integer, 0),
        COALESCE((item->>'output_tokens')::integer, 0),
        COALESCE((item->>'cached_input_tokens')::integer, 0),
        COALESCE((item->>'cached_output_tokens')::integer, 0),
        COALESCE((item->>'input_audio_tokens')::integer, 0),
        COALESCE((item->>'output_audio_tokens')::integer, 0),
        COALESCE((item->>'input_characters')::integer, 0),
        COALESCE((item->>'output_characters')::integer, 0),
        COALESCE((item->>'duration_seconds')::double precision, 0),
        COALESCE((item->>'image_count')::integer, 0),
        COALESCE((item->>'rerank_units')::integer, 0),
        (item->>'start_time')::timestamptz, (item->>'end_time')::timestamptz,
        (item->>'latency_ms')::integer, COALESCE((item->>'cache_hit')::boolean, FALSE),
        item->>'cache_key',
        ARRAY(SELECT jsonb_array_elements_text(COALESCE(item->'request_tags', '[]'::jsonb))),
        item->>'unpriced_reason', NULLIF(item->'pricing_fields_used', 'null'::jsonb),
        NULLIF(item->'usage_snapshot', 'null'::jsonb),
        NULLIF(item->'metadata', 'null'::jsonb),
        COALESCE(item->>'status', 'success'), (item->>'http_status_code')::integer,
        item->>'error_type', (item->>'source_occurred_at')::timestamptz
    FROM usage_input
    ORDER BY (item->>'accounting_sequence')::bigint
    ON CONFLICT (event_id) DO NOTHING
    RETURNING *
), rollup_facts AS MATERIALIZED (
    SELECT u.*, (input.item->>'rollup_shard')::smallint AS rollup_shard
    FROM usage_insert AS u
    JOIN usage_input AS input ON input.item->>'id' = u.event_id
), rollup_source AS MATERIALIZED (
    SELECT 'day'::text AS bucket_kind, date_trunc('day', start_time) AS bucket_start, u.*
    FROM rollup_facts AS u
    UNION ALL
    SELECT 'month'::text, date_trunc('month', start_time), u.*
    FROM rollup_facts AS u
), rollup_insert AS (
    INSERT INTO deltallm_accounting_usage_rollups_v2 (
        bucket_kind, bucket_start, organization_id, team_id, user_id,
        owner_account_id, api_key, model, provider, status, rollup_shard, request_count,
        spend_exact, provider_cost_exact, total_tokens, input_tokens, output_tokens,
        cached_input_tokens, cached_output_tokens, updated_at
    )
    SELECT
        bucket_kind, bucket_start, COALESCE(organization_id, ''), COALESCE(team_id, ''),
        COALESCE(user_id, ''), COALESCE(owner_account_id, ''), COALESCE(api_key, ''),
        COALESCE(model, ''), COALESCE(provider, ''), COALESCE(status, 'success'),
        rollup_shard, COUNT(*)::bigint, SUM(spend_exact),
        SUM(COALESCE(provider_cost_exact, 0)),
        SUM(total_tokens)::bigint, SUM(input_tokens)::bigint, SUM(output_tokens)::bigint,
        SUM(cached_input_tokens)::bigint, SUM(cached_output_tokens)::bigint, NOW()
    FROM rollup_source
    GROUP BY bucket_kind, bucket_start, organization_id, team_id, user_id,
             owner_account_id, api_key, model, provider, status, rollup_shard
    ON CONFLICT ON CONSTRAINT deltallm_usage_rollups_v2_pkey DO UPDATE SET
        request_count = deltallm_accounting_usage_rollups_v2.request_count + EXCLUDED.request_count,
        spend_exact = deltallm_accounting_usage_rollups_v2.spend_exact + EXCLUDED.spend_exact,
        provider_cost_exact = deltallm_accounting_usage_rollups_v2.provider_cost_exact + EXCLUDED.provider_cost_exact,
        total_tokens = deltallm_accounting_usage_rollups_v2.total_tokens + EXCLUDED.total_tokens,
        input_tokens = deltallm_accounting_usage_rollups_v2.input_tokens + EXCLUDED.input_tokens,
        output_tokens = deltallm_accounting_usage_rollups_v2.output_tokens + EXCLUDED.output_tokens,
        cached_input_tokens = deltallm_accounting_usage_rollups_v2.cached_input_tokens + EXCLUDED.cached_input_tokens,
        cached_output_tokens = deltallm_accounting_usage_rollups_v2.cached_output_tokens + EXCLUDED.cached_output_tokens,
        updated_at = NOW()
    RETURNING 1
), audit_input AS MATERIALIZED (
    SELECT deltallm_accounting_audit_row(e,attribution) AS item FROM source
), audit_insert AS (
    INSERT INTO deltallm_auditevent (
        event_id, occurred_at, organization_id, actor_type, actor_id, api_key,
        action, resource_type, resource_id, request_id, correlation_id, ip,
        user_agent, status, latency_ms, input_tokens, output_tokens, error_type,
        error_code, metadata, content_stored, prev_hash, event_hash
    )
    SELECT
        (item->>'event_id')::uuid,
        COALESCE((item->>'occurred_at')::timestamptz, NOW()),
        item->>'organization_id', item->>'actor_type', item->>'actor_id',
        item->>'api_key', item->>'action', item->>'resource_type',
        item->>'resource_id', item->>'request_id', item->>'correlation_id',
        item->>'ip', item->>'user_agent', item->>'status',
        (item->>'latency_ms')::integer, (item->>'input_tokens')::integer,
        (item->>'output_tokens')::integer, item->>'error_type', item->>'error_code',
        NULLIF(item->'metadata', 'null'::jsonb),
        COALESCE((item->>'content_stored')::boolean, FALSE),
        item->>'prev_hash', item->>'event_hash'
    FROM audit_input
    ON CONFLICT (event_id) DO UPDATE SET event_id=EXCLUDED.event_id
    WHERE to_jsonb(deltallm_auditevent)-'occurred_at'
      IS NOT DISTINCT FROM to_jsonb(EXCLUDED)-'occurred_at'
    RETURNING 1
)
SELECT (SELECT COUNT(*) FROM usage_insert)::integer,
       (SELECT COUNT(*) FROM rollup_insert)::integer,
       (SELECT COUNT(*) FROM audit_insert)::integer
INTO usage_count,rollup_count,audit_count;

 IF audit_count<>cardinality(p_sequences) THEN
  RAISE EXCEPTION 'accounting_read_model_audit_identity' USING ERRCODE='P0001';
 END IF;
 IF clock_timestamp()>=checkpoint.lease_expires_at THEN
  RAISE EXCEPTION 'accounting_read_model_expired_fence' USING ERRCODE='P0001';
 END IF;
 UPDATE deltallm_accounting_projection_checkpoints c SET
  last_sequence=p_sequences[cardinality(p_sequences)],lease_owner=NULL,lease_token=NULL,
  lease_expires_at=NULL,last_error_code=NULL,updated_at=clock_timestamp()
 WHERE c.projection_name='accounting-read-model-v2' AND c.protocol_name='primary'
  AND c.generation=p_generation AND c.accounting_partition=p_partition
  AND c.last_sequence=p_after_sequence AND c.lease_owner=p_worker_id
  AND c.lease_token=p_lease_token::text AND c.lease_expires_at>clock_timestamp();
 IF NOT FOUND THEN
  RAISE EXCEPTION 'accounting_read_model_commit_fence' USING ERRCODE='P0001';
 END IF;
 RETURN cardinality(p_sequences);
END;
$$;

COMMIT;
