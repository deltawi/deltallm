"""Function-local plans keep report lookups bounded under cached callers."""

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
SELECT * FROM deltallm_accounting_read_model_progress($1,$2)
"""

CLAIM = """
SELECT * FROM deltallm_accounting_claim_read_model($1,$2,$3,$4::uuid,$5::integer,$6::integer)
"""

RECOVER_CLAIM = """
SELECT * FROM deltallm_accounting_recover_read_model_claim($1,$2,$3,$4::uuid,$5::integer)
"""
