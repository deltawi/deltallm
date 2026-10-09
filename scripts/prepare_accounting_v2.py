"""Prepare an accounting-v2 generation from exact legacy budget balances."""

from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta
import json

from src.config import DatabaseConnectionSettings
from src.db.client import PrismaClientManager
from src.db.accounting_cutover import require_legacy_work_drained

_LEGACY_HOLDS = """
SELECT count(*) AS hold_count FROM (
    SELECT reserved_spend_exact FROM deltallm_verificationtoken
    UNION ALL SELECT reserved_spend_exact FROM deltallm_usertable
    UNION ALL SELECT reserved_spend_exact FROM deltallm_teamtable
    UNION ALL SELECT reserved_spend_exact FROM deltallm_organizationtable
    UNION ALL SELECT reserved_spend_exact FROM deltallm_teammodelspend
) legacy_holds WHERE reserved_spend_exact<>0
"""

_INSERT_PROTOCOL = """
INSERT INTO deltallm_accounting_protocols(
    protocol_name,generation,writer_version,state,partition_count,max_outstanding_per_partition
) VALUES ('primary',$1,2,'prepared',$2,$3)
ON CONFLICT (protocol_name,generation) DO NOTHING
"""

_INSERT_PARTITIONS = """
INSERT INTO deltallm_accounting_partitions(
    protocol_name,generation,partition_id,max_outstanding
)
SELECT 'primary',$1,partition_id,$3 FROM generate_series(0,$2-1) partition_id
ON CONFLICT (protocol_name,generation,partition_id) DO NOTHING
"""

_INSERT_WINDOWS = r"""
WITH legacy_windows(
    scope_type,scope_id,limit_exact,committed_exact,reset_at,renewal_spec,metadata
) AS (
    SELECT 'api_key',token,max_budget::numeric,COALESCE(spend_exact,spend::numeric),
           budget_reset_at,budget_duration,metadata
      FROM deltallm_verificationtoken WHERE max_budget>=0 AND max_budget<1e20
    UNION ALL
    SELECT 'user',user_id,max_budget::numeric,COALESCE(spend_exact,spend::numeric),
           budget_reset_at,budget_duration,metadata
      FROM deltallm_usertable WHERE max_budget>=0 AND max_budget<1e20
    UNION ALL
    SELECT 'team',team_id,max_budget::numeric,COALESCE(spend_exact,spend::numeric),
           budget_reset_at,budget_duration,metadata
      FROM deltallm_teamtable WHERE max_budget>=0 AND max_budget<1e20
    UNION ALL
    SELECT 'organization',organization_id,max_budget::numeric,
           COALESCE(spend_exact,spend::numeric),budget_reset_at,budget_duration,metadata
      FROM deltallm_organizationtable
      WHERE lifecycle_state='active' AND max_budget>=0 AND max_budget<1e20
    UNION ALL
    SELECT 'team_model',team.team_id||':'||entry.key,entry.value::numeric,
           COALESCE(counter.spend_exact,counter.spend::numeric,0),NULL,NULL,NULL
      FROM deltallm_teamtable team
      CROSS JOIN LATERAL jsonb_each_text(COALESCE(team.model_max_budget,'{}'::jsonb)) entry
      LEFT JOIN deltallm_teammodelspend counter
        ON counter.team_id=team.team_id AND counter.model=entry.key
      WHERE entry.value~'^[0-9]+(\.[0-9]+)?$'
), validated AS (
    SELECT *,CASE WHEN renewal_spec IS NOT NULL THEN reset_at AT TIME ZONE 'UTC'
                  ELSE '9999-12-31 00:00:00+00'::timestamptz END AS window_end,
           CASE WHEN renewal_spec~'mo$' THEN
                CASE WHEN metadata#>>'{_budget_reset,monthly_anchor_day}'~'^([1-9]|[12][0-9]|3[01])$'
                     THEN (metadata#>>'{_budget_reset,monthly_anchor_day}')::integer
                     ELSE extract(day FROM reset_at)::integer END
                ELSE NULL END AS renewal_anchor_day
      FROM legacy_windows
     WHERE length(scope_id) BETWEEN 1 AND 256
       AND limit_exact>=0 AND committed_exact>=0 AND committed_exact<=limit_exact
       AND (renewal_spec IS NULL OR (
           renewal_spec~'^([1-9][0-9]{0,3}|10000)(h|d|mo)$'
           AND reset_at AT TIME ZONE 'UTC'>CURRENT_TIMESTAMP
       ))
)
INSERT INTO deltallm_accounting_budget_windows(
    window_id,protocol_name,generation,scope_type,scope_id,period_key,
    policy_generation,limit_exact,committed_exact,window_starts_at,window_ends_at,
    renewal_spec,renewal_anchor_day
)
SELECT md5('accounting-v2:'||$1||':'||scope_type||':'||scope_id),
       'primary',$1,scope_type,scope_id,'legacy-current:v1',0,
       limit_exact,committed_exact,'1970-01-01 00:00:00+00',window_end,
       renewal_spec,renewal_anchor_day
  FROM validated
ON CONFLICT (protocol_name,generation,scope_type,scope_id,period_key) DO UPDATE SET
    limit_exact=EXCLUDED.limit_exact,
    committed_exact=EXCLUDED.committed_exact,
    window_ends_at=EXCLUDED.window_ends_at,
    renewal_spec=EXCLUDED.renewal_spec,
    renewal_anchor_day=EXCLUDED.renewal_anchor_day,
    updated_at=NOW()
WHERE deltallm_accounting_budget_windows.reserved_exact=0
  AND deltallm_accounting_budget_windows.provisional_exact=0
"""

_EXPECTED_WINDOWS = r"""
SELECT count(*) AS expected_count FROM (
    SELECT token AS scope_id FROM deltallm_verificationtoken
      WHERE max_budget>=0 AND max_budget<1e20
    UNION ALL SELECT user_id FROM deltallm_usertable
      WHERE max_budget>=0 AND max_budget<1e20
    UNION ALL SELECT team_id FROM deltallm_teamtable
      WHERE max_budget>=0 AND max_budget<1e20
    UNION ALL SELECT organization_id FROM deltallm_organizationtable
      WHERE lifecycle_state='active' AND max_budget>=0 AND max_budget<1e20
    UNION ALL SELECT team_id||':'||entry.key FROM deltallm_teamtable,
      LATERAL jsonb_each_text(COALESCE(model_max_budget,'{}'::jsonb)) entry
      WHERE entry.value~'^[0-9]+(\.[0-9]+)?$'
) expected
"""


async def prepare(args: argparse.Namespace) -> None:
    manager = PrismaClientManager()
    try:
        await manager.connect(
            DatabaseConnectionSettings(url=args.database_url, pool_size=1, pool_timeout=5),
        )
        async with manager.client.tx(
            max_wait=timedelta(seconds=5),
            timeout=timedelta(seconds=30),
        ) as tx:
            await tx.execute_raw("SET LOCAL lock_timeout='5s'")
            await tx.execute_raw("SET LOCAL statement_timeout='30s'")
            await tx.execute_raw(
                "SELECT pg_advisory_xact_lock(hashtextextended('deltallm:accounting-protocol',0))"
            )
            await tx.execute_raw(
                "LOCK TABLE deltallm_verificationtoken,deltallm_usertable,"
                "deltallm_teamtable,deltallm_organizationtable,"
                "deltallm_teammodelspend IN SHARE MODE"
            )
            await tx.execute_raw("LOCK TABLE deltallm_accounting_protocols IN EXCLUSIVE MODE")
            holds = await tx.query_raw(_LEGACY_HOLDS)
            if int(holds[0]["hold_count"]):
                raise RuntimeError("legacy budget reservations must be settled before preparation")
            await tx.execute_raw(
                _INSERT_PROTOCOL,
                args.generation,
                args.partitions,
                args.max_outstanding_per_partition,
            )
            protocol = await tx.query_raw(
                "SELECT state,writer_version,partition_count,max_outstanding_per_partition "
                "FROM deltallm_accounting_protocols "
                "WHERE protocol_name='primary' AND generation=$1 FOR UPDATE",
                args.generation,
            )
            expected_protocol = {
                "writer_version": 2,
                "partition_count": args.partitions,
                "max_outstanding_per_partition": args.max_outstanding_per_partition,
            }
            if (
                not protocol
                or any(protocol[0].get(key) != value for key, value in expected_protocol.items())
                or protocol[0]["state"] not in {"prepared", "active"}
            ):
                raise RuntimeError(
                    "accounting generation exists with incompatible configuration or state"
                )
            already_active = protocol[0]["state"] == "active"
            if already_active and not args.activate:
                raise RuntimeError("accounting generation is already active")
            if not already_active:
                await require_legacy_work_drained(tx)
                await tx.execute_raw(
                    _INSERT_PARTITIONS,
                    args.generation,
                    args.partitions,
                    args.max_outstanding_per_partition,
                )
                await tx.execute_raw(_INSERT_WINDOWS, args.generation)
            expected = int((await tx.query_raw(_EXPECTED_WINDOWS))[0]["expected_count"])
            actual = int(
                (
                    await tx.query_raw(
                        "SELECT count(*) AS window_count "
                        "FROM deltallm_accounting_budget_windows "
                        "WHERE protocol_name='primary' AND generation=$1 "
                        "AND window_starts_at<=CURRENT_TIMESTAMP "
                        "AND window_ends_at>CURRENT_TIMESTAMP",
                        args.generation,
                    )
                )[0]["window_count"]
            )
            if actual != expected:
                raise RuntimeError(
                    f"legacy budget snapshot is incomplete: expected {expected}, prepared {actual}"
                )
            if args.activate and not already_active:
                await tx.execute_raw(
                    "SELECT deltallm_activate_accounting_protocol_locked($1)",
                    args.generation,
                )
        rows = await manager.client.query_raw(
            "SELECT state,partition_count,max_outstanding_per_partition,"
            "(SELECT count(*) FROM deltallm_accounting_budget_windows w "
            " WHERE w.protocol_name=p.protocol_name AND w.generation=p.generation) AS windows "
            "FROM deltallm_accounting_protocols p "
            "WHERE protocol_name='primary' AND generation=$1",
            args.generation,
        )
        print(json.dumps(rows[0], default=str, sort_keys=True))
    finally:
        await manager.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--generation", type=int, default=1)
    parser.add_argument("--partitions", type=int, default=16, choices=range(1, 65))
    parser.add_argument("--max-outstanding-per-partition", type=int, default=4096)
    parser.add_argument(
        "--activate",
        action="store_true",
        help="atomically activate after the exact legacy snapshot passes validation",
    )
    args = parser.parse_args()
    if not 1 <= args.generation <= 2**63 - 1:
        parser.error("--generation must be a positive bigint")
    if not 1 <= args.max_outstanding_per_partition <= 1_000_000:
        parser.error("--max-outstanding-per-partition is outside the supported range")
    asyncio.run(prepare(args))


if __name__ == "__main__":
    main()
