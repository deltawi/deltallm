-- Keep each row-locking query outside the set operation. Both branches
-- retain their entry limits and use the same partial indexes.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

CREATE OR REPLACE FUNCTION deltallm_accounting_claim_terminal_journal(
    p_generation BIGINT,p_worker_id TEXT,p_lease_token UUID,p_lease_seconds INTEGER,p_limit INTEGER
) RETURNS TABLE(journal_sequence BIGINT)
LANGUAGE plpgsql AS $$
DECLARE
    candidate_ids BIGINT[];
    chosen_ids BIGINT[];
BEGIN
    IF p_generation IS NULL OR p_generation<1 OR COALESCE(length(p_worker_id),0) NOT BETWEEN 1 AND 256
       OR p_lease_token IS NULL OR COALESCE(p_lease_seconds,0) NOT BETWEEN 5 AND 300
       OR COALESCE(p_limit,0) NOT BETWEEN 1 AND 256 THEN
        RAISE EXCEPTION 'accounting_terminal_claim_shape' USING ERRCODE='P0001';
    END IF;
    IF EXISTS (SELECT 1 FROM deltallm_accounting_terminal_journal j WHERE j.generation=p_generation
        AND j.status='processing' AND j.lease_owner=p_worker_id AND j.lease_token=p_lease_token) THEN
        RETURN QUERY SELECT j.sequence FROM deltallm_accounting_terminal_journal j
            WHERE j.generation=p_generation AND j.status='processing' AND j.lease_owner=p_worker_id
              AND j.lease_token=p_lease_token AND j.lease_expires_at>clock_timestamp()
            ORDER BY j.sequence LIMIT p_limit;
        RETURN;
    END IF;
    -- Each index branch inspects at most one entry slice. Do not sort all
    -- retained pending and expired work before applying the limit.
    WITH pending_candidates AS MATERIALIZED (
        SELECT j.sequence FROM deltallm_accounting_terminal_journal j
        WHERE j.generation=p_generation AND j.status='pending'
        ORDER BY j.sequence LIMIT p_limit FOR UPDATE SKIP LOCKED
    ), expired_candidates AS MATERIALIZED (
        SELECT j.sequence FROM deltallm_accounting_terminal_journal j
        WHERE j.generation=p_generation AND j.status='processing'
          AND j.lease_expires_at<=statement_timestamp()
        ORDER BY j.lease_expires_at,j.sequence LIMIT p_limit FOR UPDATE SKIP LOCKED
    )
    SELECT array_agg(pick.sequence ORDER BY pick.sequence) INTO candidate_ids FROM (
        SELECT sequence FROM pending_candidates
        UNION ALL
        SELECT sequence FROM expired_candidates
    ) pick;
    PERFORM 1 FROM deltallm_accounting_terminal_capacity c WHERE c.protocol_name='primary' AND c.generation=p_generation
        AND c.accounting_partition=ANY(ARRAY(SELECT j.accounting_partition FROM deltallm_accounting_terminal_journal j
            WHERE j.sequence=ANY(candidate_ids))) ORDER BY c.accounting_partition FOR UPDATE;
    WITH failed AS (
        UPDATE deltallm_accounting_terminal_journal j SET status='failed',lease_owner=NULL,lease_token=NULL,
            lease_expires_at=NULL,last_error_code='lease_attempts_exhausted',updated_at=clock_timestamp()
        WHERE j.sequence=ANY(candidate_ids) AND j.attempts>=5
        RETURNING j.accounting_partition
    ), deltas AS (
        SELECT accounting_partition,count(*)::integer AS entries FROM failed GROUP BY accounting_partition
    ) UPDATE deltallm_accounting_terminal_capacity c SET failed_entries=c.failed_entries+d.entries
        FROM deltas d WHERE c.protocol_name='primary' AND c.generation=p_generation AND c.accounting_partition=d.accounting_partition;
    SELECT array_agg(pick.sequence ORDER BY pick.sequence) INTO chosen_ids FROM (
        SELECT j.sequence,sum(j.payload_bytes) OVER (ORDER BY j.sequence) AS bytes,
            row_number() OVER (ORDER BY j.sequence) AS ordinal
        FROM deltallm_accounting_terminal_journal j WHERE j.sequence=ANY(candidate_ids) AND j.attempts<5
    ) pick WHERE pick.ordinal<=p_limit AND pick.bytes<=1048576;
    UPDATE deltallm_accounting_terminal_journal j SET status='processing',attempts=j.attempts+1,
        lease_owner=p_worker_id,lease_token=p_lease_token,lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),
        last_error_code=NULL,updated_at=clock_timestamp() WHERE j.sequence=ANY(chosen_ids);
    RETURN QUERY SELECT j.sequence FROM deltallm_accounting_terminal_journal j WHERE j.sequence=ANY(chosen_ids) ORDER BY j.sequence;
END;
$$;

COMMIT;

