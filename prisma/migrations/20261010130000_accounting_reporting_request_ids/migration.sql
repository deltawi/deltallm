-- Preserve accepted events and all financial checks during reporting recovery.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

CREATE OR REPLACE FUNCTION deltallm_accounting_usage_row(
 p_event deltallm_accounting_events,p_attribution JSONB
) RETURNS JSONB LANGUAGE plpgsql STABLE SET TimeZone='UTC' AS $$
DECLARE
 spend JSONB:=p_event.payload_json->'spend';
 metadata JSONB; billing JSONB; usage JSONB; snapshot JSONB;
 charge NUMERIC; provider_charge NUMERIC; started TIMESTAMPTZ; finished TIMESTAMPTZ;
BEGIN
 IF p_event.outcome<>'completed' THEN RETURN NULL; END IF;
 -- Keep the accepted event unchanged. Only the reporting row needs this fallback.
 IF jsonb_typeof(spend)='object' AND (
  NOT spend ? 'request_id' OR spend->'request_id'='null'::jsonb
  OR spend->'request_id'='""'::jsonb
 ) THEN
  spend:=jsonb_set(spend,'{request_id}',to_jsonb(p_event.operation_id::text));
 END IF;
 IF jsonb_typeof(spend) IS DISTINCT FROM 'object'
  OR jsonb_typeof(p_attribution) IS DISTINCT FROM 'object'
  OR p_event.payload_json->>'exact_charge' IS NULL
  OR jsonb_typeof(spend->'request_id') IS DISTINCT FROM 'string'
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
COMMIT;
