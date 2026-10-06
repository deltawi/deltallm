-- A queue-head observation must not scan completed receipt history.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

CREATE FUNCTION deltallm_accounting_backlog_snapshot(p_generation BIGINT)
RETURNS TABLE (
 generation BIGINT,protocol_state TEXT,partition_count INTEGER,
 covered_partitions INTEGER,expected_covered_partitions INTEGER,
 outstanding_operations BIGINT,pending_entries BIGINT,pending_bytes BIGINT,
 failed_entries BIGINT,capacity_saturated BOOLEAN,oldest_age_seconds DOUBLE PRECISION
) LANGUAGE plpgsql VOLATILE
SET enable_seqscan=off SET enable_bitmapscan=off AS $$
DECLARE usable BOOLEAN;
BEGIN
 SELECT i.indisvalid AND i.indisready INTO usable FROM pg_index i
 WHERE i.indexrelid='deltallm_accounting_terminal_oldest_work_idx'::regclass;
 IF usable IS DISTINCT FROM TRUE THEN
  RAISE EXCEPTION 'accounting_health_index_unavailable' USING ERRCODE='P0001';
 END IF;
-- Repeated claim/complete cycles can leave many empty partial-index pages.
-- That cost estimate can select a history scan even for LIMIT 1. These settings
-- apply only inside this function. They keep the ordered queue-head seek and
-- fixed primary-key cells; they do not change the pool or database defaults.
RETURN QUERY WITH protocol AS MATERIALIZED (
 SELECT p.generation,p.state,p.partition_count FROM deltallm_accounting_protocols p
 WHERE p.protocol_name='primary' AND p.generation=p_generation AND p.writer_version=2
), counters AS MATERIALIZED (
 SELECT count(partition.partition_id)::integer AS covered_partitions,
  count(partition.partition_id) FILTER (WHERE key.partition_id<p.partition_count)::integer
   AS expected_covered_partitions,
  coalesce(sum(partition.outstanding_count),0)::bigint AS outstanding_operations,
  coalesce(sum(capacity.pending_entries),0)::bigint AS pending_entries,
  coalesce(sum(capacity.pending_bytes),0)::bigint AS pending_bytes,
  coalesce(sum(capacity.failed_entries),0)::bigint AS failed_entries,
  coalesce(bool_or(capacity.pending_entries>=capacity.max_entries
   OR capacity.pending_bytes>=capacity.max_bytes),FALSE) AS capacity_saturated
 FROM protocol p CROSS JOIN generate_series(0,63) key(partition_id)
 LEFT JOIN LATERAL (
  SELECT x.partition_id,x.outstanding_count FROM deltallm_accounting_partitions x
  WHERE x.protocol_name='primary' AND x.generation=p.generation
   AND x.partition_id=key.partition_id OFFSET 0
 ) partition ON TRUE
 LEFT JOIN LATERAL (
  SELECT x.pending_entries,x.pending_bytes,x.failed_entries,x.max_entries,x.max_bytes
  FROM deltallm_accounting_terminal_capacity x
  WHERE x.protocol_name='primary' AND x.generation=p.generation
   AND x.accounting_partition=key.partition_id OFFSET 0
 ) capacity ON TRUE
), oldest AS MATERIALIZED (
 SELECT j.accepted_at FROM deltallm_accounting_terminal_journal j
 WHERE j.protocol_name='primary' AND j.generation=p_generation AND j.status<>'completed'
 ORDER BY j.accepted_at,j.sequence LIMIT 1
)
SELECT p.generation,p.state,p.partition_count,c.*,
 CASE WHEN oldest.accepted_at IS NOT NULL
  THEN greatest(0,extract(epoch FROM (statement_timestamp()-oldest.accepted_at)))::float8
  ELSE NULL END
FROM protocol p CROSS JOIN counters c LEFT JOIN oldest ON TRUE;
END;
$$;

COMMIT;
