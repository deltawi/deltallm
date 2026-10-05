"""Read at most 64 partition counters and one oldest retained-work key."""

from __future__ import annotations

from src.billing.accounting_health import AccountingBacklogSnapshot
from src.db.accounting_calls import AccountingDatabaseCalls, AccountingQueryClient
from src.db.accounting_permit_results import invalid_result


BACKLOG_SQL = """
WITH protocol AS MATERIALIZED (
    SELECT generation,state,partition_count
    FROM deltallm_accounting_protocols
    WHERE protocol_name='primary' AND generation=$1 AND writer_version=2
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
        SELECT partition_id,outstanding_count FROM deltallm_accounting_partitions
        WHERE protocol_name='primary' AND generation=p.generation
          AND partition_id=key.partition_id OFFSET 0
    ) partition ON TRUE
    LEFT JOIN LATERAL (
        SELECT pending_entries,pending_bytes,failed_entries,max_entries,max_bytes
        FROM deltallm_accounting_terminal_capacity
        WHERE protocol_name='primary' AND generation=p.generation
          AND accounting_partition=key.partition_id OFFSET 0
    ) capacity ON TRUE
), oldest AS MATERIALIZED (
    SELECT accepted_at FROM deltallm_accounting_terminal_journal
    WHERE protocol_name='primary' AND generation=$1 AND status<>'completed'
    ORDER BY accepted_at,sequence LIMIT 1
)
SELECT p.generation,p.state AS protocol_state,p.partition_count,c.*,
    CASE WHEN oldest.accepted_at IS NOT NULL
        THEN greatest(0,extract(epoch FROM (statement_timestamp()-oldest.accepted_at)))::float8
        ELSE NULL END AS oldest_age_seconds
FROM protocol p CROSS JOIN counters c LEFT JOIN oldest ON TRUE
"""


class AccountingBacklogRepository:
    def __init__(
        self, db: AccountingQueryClient, *, statement_budget_seconds: float = 0.25
    ) -> None:
        self._calls = AccountingDatabaseCalls(db, statement_budget_seconds=statement_budget_seconds)

    async def snapshot(self, *, generation: int, expires_at: float) -> AccountingBacklogSnapshot:
        if type(generation) is not int or not 1 <= generation <= 2**63 - 1:
            raise ValueError("accounting backlog generation is invalid")
        rows = await self._calls.call(
            "terminal_backlog_snapshot", BACKLOG_SQL, generation, expires_at=expires_at
        )
        if len(rows) != 1:
            raise invalid_result()
        row = dict(rows[0])
        covered = row.pop("covered_partitions", None)
        expected = row.pop("expected_covered_partitions", None)
        if (
            type(covered) is not int
            or type(expected) is not int
            or covered != row.get("partition_count")
            or expected != covered
        ):
            raise invalid_result()
        try:
            value = AccountingBacklogSnapshot.model_validate(row)
        except ValueError:
            raise invalid_result() from None
        if value.generation != generation:
            raise invalid_result()
        return value
