from __future__ import annotations

from src.db.identity.platform_accounts import PlatformAccountDatabase


class ExternalAuthCleanupRepository:
    def __init__(self, db: PlatformAccountDatabase) -> None:
        self.db = db

    async def protocol_ready(self) -> bool:
        rows = await self.db.query_raw("""SELECT count(*)::int = 7 AS compatible
            FROM _prisma_migrations WHERE migration_name IN (
                '20261006120000_external_customer_auth',
                '20261006120100_external_customer_invariants',
                '20261006120200_external_customer_maintenance',
                '20261006120300_external_customer_mfa_proof',
                '20261006120400_external_customer_lifecycle',
                '20261006120500_external_customer_rollback_indexes',
                '20261006120600_external_customer_membership_lock')
              AND finished_at IS NOT NULL AND rolled_back_at IS NULL""")
        return rows[0]["compatible"] is True

    async def claim(self, interval_seconds: int) -> bool:
        rows = await self.db.query_raw(
            """UPDATE deltallm_externalauthmaintenancelease
            SET next_run_at = clock_timestamp() + make_interval(secs => $1)
            WHERE lease_id = 'external-auth-cleanup' AND next_run_at <= clock_timestamp()
            RETURNING lease_id""",
            interval_seconds,
        )
        return bool(rows)

    async def clean(self, batch_size: int) -> dict[str, int]:
        counts: dict[str, int] = {}
        # Table names and predicates are fixed code, never request input.
        for kind, table, column, predicate in (
            ("assertions", "deltallm_externalauthassertionuse", "jti_hash", "retain_until < NOW()"),
            (
                "children",
                "deltallm_platformsession",
                "session_id",
                "external_parent_id IS NOT NULL AND expires_at < NOW() - INTERVAL '7 days'",
            ),
            (
                "parents",
                "deltallm_externalauthparentsession",
                "parent_id",
                "expires_at < NOW() - INTERVAL '30 days' AND NOT EXISTS (SELECT 1 FROM deltallm_platformsession child WHERE child.external_parent_id = retained.parent_id)",
            ),
        ):
            # Assertion hashes can match across integrations: use the physical
            # tuple ID for this transaction's bounded, locked deletion.
            rows = await self.db.query_raw(
                f"""WITH expired AS (
                    SELECT ctid FROM {table} retained WHERE {predicate}
                    ORDER BY {("retain_until" if kind == "assertions" else "expires_at")}, {column}
                    LIMIT $1 FOR UPDATE SKIP LOCKED
                ), removed AS (
                    DELETE FROM {table} retained USING expired
                    WHERE retained.ctid = expired.ctid RETURNING 1
                ) SELECT count(*)::int AS removed FROM removed""",
                batch_size,
            )
            counts[kind] = int(rows[0]["removed"])
        return counts

    async def retention_pressure(self, batch_size: int) -> dict[str, tuple[int, float]]:
        pressure: dict[str, tuple[int, float]] = {}
        for kind, table, column, retention, predicate in (
            (
                "assertions",
                "deltallm_externalauthassertionuse",
                "retain_until",
                "0 seconds",
                "TRUE",
            ),
            (
                "children",
                "deltallm_platformsession",
                "expires_at",
                "7 days",
                "external_parent_id IS NOT NULL",
            ),
            ("parents", "deltallm_externalauthparentsession", "expires_at", "30 days", "TRUE"),
        ):
            rows = await self.db.query_raw(
                f"""SELECT count(*)::int AS backlog,
                    COALESCE(EXTRACT(EPOCH FROM (NOW() - MIN(deadline))), 0)::float8 AS oldest
                FROM (SELECT {column} + $2::interval AS deadline FROM {table}
                    WHERE {predicate} AND {column} < NOW() - $2::interval
                    ORDER BY {column} LIMIT $1) expired""",
                batch_size + 1,
                retention,
            )
            pressure[kind] = (int(rows[0]["backlog"]), max(0, float(rows[0]["oldest"])))
        return pressure
