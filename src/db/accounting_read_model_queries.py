"""Fixed checkpoint cells and indexed terminal key pages bound claim work."""

INITIALIZE = """
WITH protocol AS MATERIALIZED (
 SELECT generation,partition_count FROM deltallm_accounting_protocols
 WHERE protocol_name='primary' AND generation=$2 AND state IN ('active','draining')
), inserted AS (
 INSERT INTO deltallm_accounting_projection_checkpoints
 (projection_name,protocol_name,generation,accounting_partition)
 SELECT $1,'primary',p.generation,n FROM protocol p
 CROSS JOIN LATERAL generate_series(0,p.partition_count-1) n
 ON CONFLICT DO NOTHING RETURNING accounting_partition
)
SELECT p.generation,p.partition_count,
 (SELECT count(*)::integer FROM generate_series(0,p.partition_count-1) n
  CROSS JOIN LATERAL (
   SELECT c.accounting_partition FROM deltallm_accounting_projection_checkpoints c
   WHERE c.projection_name=$1 AND c.protocol_name='primary' AND c.generation=p.generation
    AND c.accounting_partition=n OFFSET 0
  ) c)+(SELECT count(*)::integer FROM inserted) AS slots
FROM protocol p CROSS JOIN (SELECT count(*) FROM inserted) effects
"""

VERIFY_CELLS = """
SELECT p.generation,p.partition_count,
 (SELECT count(*)::integer FROM generate_series(0,p.partition_count-1) n
  CROSS JOIN LATERAL (
   SELECT c.accounting_partition FROM deltallm_accounting_projection_checkpoints c
   WHERE c.projection_name=$1 AND c.protocol_name='primary' AND c.generation=p.generation
    AND c.accounting_partition=n OFFSET 0
  ) c) AS slots
FROM deltallm_accounting_protocols p
WHERE p.protocol_name='primary' AND p.generation=$2 AND p.state IN ('active','draining')
"""

PROGRESS = """
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
  AND (e.protocol_name,e.generation,e.accounting_partition,e.sequence)>
   ('primary',p.generation,c.accounting_partition,c.last_sequence)
  AND e.event_type IN ('finalized','reconciled')
 ORDER BY e.protocol_name,e.generation,e.accounting_partition,e.sequence LIMIT 1
) e ON c.accounting_partition IS NOT NULL
GROUP BY p.generation,p.partition_count
"""

_KEY_PAGE = """
LEFT JOIN LATERAL (
 SELECT array_agg(sequence ORDER BY sequence) AS sequences,max(cumulative)::bigint AS source_bytes
 FROM (
  SELECT sequence,sum(bytes) OVER (ORDER BY sequence) AS cumulative FROM (
   SELECT e.sequence,64+octet_length(e.payload_json::text)
    +octet_length(e.audit_envelope_json::text) AS bytes
   FROM deltallm_accounting_events e
   WHERE e.protocol_name='primary' AND e.generation=p.generation
    AND e.accounting_partition=c.accounting_partition
    AND (e.protocol_name,e.generation,e.accounting_partition,e.sequence)>
     ('primary',p.generation,c.accounting_partition,c.last_sequence)
    AND e.event_type IN ('finalized','reconciled')
   ORDER BY e.protocol_name,e.generation,e.accounting_partition,e.sequence LIMIT $6::integer
  ) raw
 ) measured WHERE cumulative<=1048576
) page ON c.accounting_partition IS NOT NULL
"""

CLAIM = (
    """
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
     AND (e.protocol_name,e.generation,e.accounting_partition,e.sequence)>
      ('primary',p.generation,n,c.last_sequence)
     AND e.event_type IN ('finalized','reconciled') LIMIT 1) OFFSET 0
 ) c
), candidate AS MATERIALIZED (
 SELECT c.accounting_partition FROM candidates
 CROSS JOIN LATERAL unnest(candidates.parts) n
 CROSS JOIN LATERAL (
  SELECT c.accounting_partition FROM deltallm_accounting_projection_checkpoints c
  WHERE c.projection_name=$1 AND c.protocol_name='primary' AND c.generation=$2
   AND c.accounting_partition=n
   AND (c.lease_expires_at IS NULL OR c.lease_expires_at<=clock_timestamp())
  FOR UPDATE SKIP LOCKED
 ) c LIMIT 1
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
"""
    + _KEY_PAGE
)

RECOVER_CLAIM = """
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
""" + _KEY_PAGE.replace("$6::integer", "$5::integer")
