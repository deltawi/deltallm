-- Keep fixed-cell reporting lookups indexed after an empty statistics snapshot.
-- The function restores the caller's plan policy after success or failure.
-- Preserve the existing generation, lease, frontier, page, and byte bounds.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

CREATE FUNCTION deltallm_accounting_claim_read_model(TEXT,BIGINT,TEXT,UUID,INTEGER,INTEGER)
RETURNS TABLE(generation BIGINT,accounting_partition INTEGER,last_sequence BIGINT,
 sequences BIGINT[],source_bytes BIGINT)
LANGUAGE plpgsql SET plan_cache_mode=force_custom_plan AS $report$
#variable_conflict use_column
BEGIN
 RETURN QUERY
WITH protocol AS MATERIALIZED (
 SELECT generation,partition_count FROM deltallm_accounting_protocols
 WHERE protocol_name='primary' AND generation=$2 AND state IN ('active','draining')
), candidates AS MATERIALIZED (
 SELECT array_agg(c.accounting_partition ORDER BY c.updated_at,c.accounting_partition) AS parts
 FROM protocol p CROSS JOIN LATERAL generate_series(0,p.partition_count-1) n
 CROSS JOIN LATERAL (
  SELECT c.accounting_partition,c.updated_at FROM deltallm_accounting_projection_checkpoints c
  WHERE c.projection_name=$1 AND c.protocol_name='primary' AND c.generation=p.generation
   AND c.accounting_partition=n
   AND (c.lease_expires_at IS NULL OR c.lease_expires_at<=clock_timestamp())
   AND EXISTS (SELECT 1 FROM deltallm_accounting_events e
    WHERE e.protocol_name='primary' AND e.generation=p.generation
     AND e.accounting_partition=n
     AND e.sequence>c.last_sequence
     AND (e.protocol_name,e.generation,e.accounting_partition,e.sequence)>
      ('primary',p.generation,n,c.last_sequence)
     AND e.event_type IN ('finalized','reconciled') LIMIT 1 OFFSET 0) OFFSET 0
 ) c
), candidate AS MATERIALIZED (
 SELECT c.accounting_partition FROM candidates
 CROSS JOIN LATERAL unnest(candidates.parts) n
 CROSS JOIN LATERAL (
  SELECT c.accounting_partition,c.last_sequence FROM deltallm_accounting_projection_checkpoints c
  WHERE c.projection_name=$1 AND c.protocol_name='primary' AND c.generation=$2
   AND c.accounting_partition=n
   AND (c.lease_expires_at IS NULL OR c.lease_expires_at<=clock_timestamp())
  FOR UPDATE SKIP LOCKED OFFSET 0
 ) c CROSS JOIN LATERAL (
  SELECT 1 FROM deltallm_accounting_events e
  WHERE e.protocol_name='primary' AND e.generation=$2
   AND e.accounting_partition=c.accounting_partition
   AND e.sequence>c.last_sequence
   AND (e.protocol_name,e.generation,e.accounting_partition,e.sequence)>
    ('primary',$2,c.accounting_partition,c.last_sequence)
   AND e.event_type IN ('finalized','reconciled')
  ORDER BY e.protocol_name,e.generation,e.accounting_partition,e.sequence LIMIT 1 OFFSET 0
 ) pending
 LIMIT 1
), claimed AS (
 UPDATE deltallm_accounting_projection_checkpoints c SET lease_owner=$3,lease_token=$4,
  lease_expires_at=clock_timestamp()+make_interval(secs=>$5::integer),
  last_error_code=NULL,updated_at=clock_timestamp()
 FROM candidate WHERE c.projection_name=$1 AND c.protocol_name='primary'
  AND c.generation=$2 AND c.accounting_partition=candidate.accounting_partition
 RETURNING c.accounting_partition,c.last_sequence
)
SELECT p.generation,c.accounting_partition,c.last_sequence,
page.sequences,page.source_bytes FROM protocol p LEFT JOIN claimed c ON TRUE

LEFT JOIN LATERAL (
 SELECT array_agg(sequence ORDER BY sequence) AS sequences,max(cumulative)::bigint AS source_bytes
 FROM (
  SELECT sequence,sum(bytes) OVER (ORDER BY sequence) AS cumulative FROM (
   SELECT e.sequence,64+octet_length(e.payload_json::text)
    +octet_length(e.audit_envelope_json::text) AS bytes
   FROM deltallm_accounting_events e
   WHERE e.protocol_name='primary' AND e.generation=p.generation
    AND e.accounting_partition=c.accounting_partition
    AND e.sequence>c.last_sequence
    AND (e.protocol_name,e.generation,e.accounting_partition,e.sequence)>
     ('primary',p.generation,c.accounting_partition,c.last_sequence)
    AND e.event_type IN ('finalized','reconciled')
   ORDER BY e.protocol_name,e.generation,e.accounting_partition,e.sequence LIMIT $6::integer
  ) raw
 ) measured WHERE cumulative<=1048576
) page ON c.accounting_partition IS NOT NULL;
END;
$report$;

CREATE FUNCTION deltallm_accounting_read_model_progress(TEXT,BIGINT)
RETURNS TABLE(generation BIGINT,partition_count INTEGER,slots INTEGER,pending_partitions INTEGER,
 oldest_head_age_seconds DOUBLE PRECISION)
LANGUAGE plpgsql SET plan_cache_mode=force_custom_plan AS $report$
#variable_conflict use_column
BEGIN
 RETURN QUERY
WITH protocol AS MATERIALIZED (
 SELECT generation,partition_count FROM deltallm_accounting_protocols
 WHERE protocol_name='primary' AND generation=$2 AND state IN ('active','draining')
)
SELECT p.generation,p.partition_count,count(c.accounting_partition)::integer AS slots,
 count(e.sequence)::integer AS pending_partitions,
 max(greatest(0,extract(epoch FROM (clock_timestamp()-e.created_at))))
  FILTER (WHERE e.sequence IS NOT NULL)::double precision AS oldest_head_age_seconds
FROM protocol p CROSS JOIN LATERAL generate_series(0,p.partition_count-1) n
LEFT JOIN LATERAL (
 SELECT c.accounting_partition,c.last_sequence FROM deltallm_accounting_projection_checkpoints c
 WHERE c.projection_name=$1 AND c.protocol_name='primary' AND c.generation=p.generation
  AND c.accounting_partition=n OFFSET 0
) c ON TRUE
LEFT JOIN LATERAL (
 SELECT e.sequence,e.created_at FROM deltallm_accounting_events e
 WHERE e.protocol_name='primary' AND e.generation=p.generation
  AND e.accounting_partition=c.accounting_partition
  AND e.sequence>c.last_sequence
  AND (e.protocol_name,e.generation,e.accounting_partition,e.sequence)>
   ('primary',p.generation,c.accounting_partition,c.last_sequence)
  AND e.event_type IN ('finalized','reconciled')
 ORDER BY e.protocol_name,e.generation,e.accounting_partition,e.sequence LIMIT 1
) e ON c.accounting_partition IS NOT NULL
GROUP BY p.generation,p.partition_count;
END;
$report$;

CREATE FUNCTION deltallm_accounting_recover_read_model_claim(TEXT,BIGINT,TEXT,UUID,INTEGER)
RETURNS TABLE(generation BIGINT,accounting_partition INTEGER,last_sequence BIGINT,
 sequences BIGINT[],source_bytes BIGINT)
LANGUAGE plpgsql SET plan_cache_mode=force_custom_plan AS $report$
#variable_conflict use_column
BEGIN
 RETURN QUERY
WITH protocol AS MATERIALIZED (
 SELECT generation,partition_count FROM deltallm_accounting_protocols
 WHERE protocol_name='primary' AND generation=$2 AND state IN ('active','draining')
), owned AS MATERIALIZED (
 SELECT c.accounting_partition,c.last_sequence FROM protocol p
 CROSS JOIN LATERAL generate_series(0,p.partition_count-1) n
 CROSS JOIN LATERAL (
  SELECT c.accounting_partition,c.last_sequence FROM deltallm_accounting_projection_checkpoints c
  WHERE c.projection_name=$1 AND c.protocol_name='primary' AND c.generation=p.generation
   AND c.accounting_partition=n AND c.lease_owner=$3 AND c.lease_token=$4
   AND c.lease_expires_at>clock_timestamp() OFFSET 0
 ) c
)
SELECT p.generation,c.accounting_partition,c.last_sequence,
 page.sequences,page.source_bytes FROM protocol p LEFT JOIN owned c ON TRUE

LEFT JOIN LATERAL (
 SELECT array_agg(sequence ORDER BY sequence) AS sequences,max(cumulative)::bigint AS source_bytes
 FROM (
  SELECT sequence,sum(bytes) OVER (ORDER BY sequence) AS cumulative FROM (
   SELECT e.sequence,64+octet_length(e.payload_json::text)
    +octet_length(e.audit_envelope_json::text) AS bytes
   FROM deltallm_accounting_events e
   WHERE e.protocol_name='primary' AND e.generation=p.generation
    AND e.accounting_partition=c.accounting_partition
    AND e.sequence>c.last_sequence
    AND (e.protocol_name,e.generation,e.accounting_partition,e.sequence)>
     ('primary',p.generation,c.accounting_partition,c.last_sequence)
    AND e.event_type IN ('finalized','reconciled')
   ORDER BY e.protocol_name,e.generation,e.accounting_partition,e.sequence LIMIT $5::integer
  ) raw
 ) measured WHERE cumulative<=1048576
) page ON c.accounting_partition IS NOT NULL;
END;
$report$;

COMMIT;
