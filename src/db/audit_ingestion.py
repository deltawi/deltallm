from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from typing import Any, Literal, Sequence, cast

from src.audit.delivery import AuditDeliveryClass, parse_audit_delivery_class
from src.db.client import is_prisma_transaction_client


AuditEnqueueStatus = Literal["accepted", "duplicate", "full"]


def _audit_enqueue_status(value: object) -> AuditEnqueueStatus:
    normalized = str(value)
    if normalized not in {"accepted", "duplicate", "full"}:
        raise RuntimeError(f"unexpected audit enqueue status: {normalized}")
    return cast(AuditEnqueueStatus, normalized)


@dataclass(frozen=True, slots=True)
class AuditOutboxRecord:
    event_id: str
    record_type: str
    organization_id: str | None
    delivery_class: AuditDeliveryClass
    payload: dict[str, Any]
    redacted_payload: dict[str, Any]
    policy_version: int
    attempt_count: int
    max_attempts: int = 10
    claim_token: str = "unfenced-test-claim"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "delivery_class",
            parse_audit_delivery_class(self.delivery_class),
        )


@dataclass(frozen=True, slots=True)
class AuditEnqueueResult:
    status: AuditEnqueueStatus
    pending_count: int


@dataclass(frozen=True, slots=True)
class AuditOutboxEnvelope:
    event_id: str
    record_type: str
    organization_id: str | None
    delivery_class: AuditDeliveryClass
    payload: dict[str, Any]
    redacted_payload: dict[str, Any]
    max_attempts: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "delivery_class",
            parse_audit_delivery_class(self.delivery_class),
        )


@dataclass(frozen=True, slots=True)
class AuditBundleEnqueueResult:
    statuses: dict[str, AuditEnqueueStatus]
    pending_count: int


class AuditIngestionRepository:
    """Durable, bounded audit and prompt-render ingress."""

    def __init__(self, prisma_client: Any | None) -> None:
        self.prisma = prisma_client

    def with_db(self, prisma_client: Any | None) -> AuditIngestionRepository:
        return AuditIngestionRepository(prisma_client)

    async def enqueue(
        self,
        *,
        event_id: str,
        record_type: str,
        organization_id: str | None,
        delivery_class: AuditDeliveryClass | str,
        payload: dict[str, Any],
        redacted_payload: dict[str, Any],
        max_attempts: int,
        max_pending_events: int,
        required_reserve: int,
    ) -> AuditEnqueueResult:
        result = await self.enqueue_bundle(
            envelopes=[
                AuditOutboxEnvelope(
                    event_id=event_id,
                    record_type=record_type,
                    organization_id=organization_id,
                    delivery_class=delivery_class,
                    payload=payload,
                    redacted_payload=redacted_payload,
                    max_attempts=max_attempts,
                )
            ],
            max_pending_events=max_pending_events,
            required_reserve=required_reserve,
        )
        return AuditEnqueueResult(
            status=result.statuses.get(event_id, "full"),
            pending_count=result.pending_count,
        )

    async def enqueue_bundle(
        self,
        *,
        envelopes: Sequence[AuditOutboxEnvelope],
        max_pending_events: int,
        required_reserve: int,
    ) -> AuditBundleEnqueueResult:
        """Accept a small same-tenant bundle with one policy and capacity decision."""

        if self.prisma is None:
            raise RuntimeError("audit ingestion database is unavailable")
        if not envelopes:
            return AuditBundleEnqueueResult(statuses={}, pending_count=0)
        organization_ids = {item.organization_id for item in envelopes}
        if len(organization_ids) != 1:
            raise ValueError("audit enqueue bundles must belong to one organization")
        event_ids = [item.event_id for item in envelopes]
        if len(set(event_ids)) != len(event_ids):
            raise ValueError("audit enqueue bundle event IDs must be unique")
        organization_id = next(iter(organization_ids))
        serialized_envelopes = json.dumps(
            [
                {
                    "event_id": item.event_id,
                    "record_type": item.record_type,
                    "organization_id": item.organization_id,
                    "delivery_class": item.delivery_class.value,
                    "payload": item.payload,
                    "redacted_payload": item.redacted_payload,
                    "max_attempts": max(1, int(item.max_attempts)),
                }
                for item in envelopes
            ],
            default=str,
        )
        async with self._transaction() as tx:
            transactional = self.with_db(tx)
            await transactional._lock_bundle_admission(organization_id)
            return await transactional._enqueue_bundle_under_lock(
                serialized_envelopes=serialized_envelopes,
                organization_id=organization_id,
                max_pending_events=max(1, int(max_pending_events)),
                required_reserve=max(0, int(required_reserve)),
            )

    async def _lock_bundle_admission(self, organization_id: str | None) -> None:
        if self.prisma is None:
            raise RuntimeError("audit ingestion database is unavailable")
        await self.prisma.query_raw(
            """
            WITH queue_lock AS MATERIALIZED (
                SELECT pg_advisory_xact_lock(
                    hashtextextended('deltallm:audit-ingestion-capacity', 0)
                ) AS locked
            ), policy_lock AS MATERIALIZED (
                SELECT pg_advisory_xact_lock(
                    hashtextextended('deltallm:audit-content-policy:' || $1, 0)
                ) AS locked
                FROM queue_lock
                WHERE $1::text IS NOT NULL
            )
            SELECT
                (SELECT COUNT(*) FROM queue_lock)::int AS queue_locks,
                (SELECT COUNT(*) FROM policy_lock)::int AS policy_locks
            """,
            organization_id,
        )

    async def _enqueue_bundle_under_lock(
        self,
        *,
        serialized_envelopes: str,
        organization_id: str | None,
        max_pending_events: int,
        required_reserve: int,
    ) -> AuditBundleEnqueueResult:
        if self.prisma is None:
            raise RuntimeError("audit ingestion database is unavailable")
        rows = await self.prisma.query_raw(
            """
            WITH input AS MATERIALIZED (
                SELECT
                    item->>'event_id' AS event_id,
                    item->>'record_type' AS record_type,
                    NULLIF(item->>'organization_id', '') AS organization_id,
                    item->>'delivery_class' AS delivery_class,
                    item->'payload' AS payload,
                    item->'redacted_payload' AS redacted_payload,
                    (item->>'max_attempts')::int AS max_attempts
                FROM jsonb_array_elements($1::jsonb) AS source(item)
            ),
            existing AS MATERIALIZED (
                SELECT input.event_id
                FROM input
                JOIN deltallm_audit_ingestion_outbox existing
                  ON existing.event_id = input.event_id
            ),
            capacity AS MATERIALIZED (
                SELECT pending_count
                FROM deltallm_telemetry_ingestion_capacity
                WHERE queue_name = 'audit'
            ),
            policy AS MATERIALIZED (
                SELECT
                    COALESCE(o.audit_content_storage_enabled, FALSE) AS enabled,
                    COALESCE(o.audit_content_policy_version, 0)::bigint AS version
                FROM (VALUES (1)) AS singleton(value)
                LEFT JOIN deltallm_organizationtable o ON o.organization_id = $2
                LIMIT 1
            ),
            new_rows AS MATERIALIZED (
                SELECT input.*
                FROM input
                LEFT JOIN existing USING (event_id)
                WHERE existing.event_id IS NULL
            ),
            required_admitted AS MATERIALIZED (
                SELECT new_rows.*
                FROM new_rows, capacity
                WHERE delivery_class = 'required'
                ORDER BY event_id
                LIMIT GREATEST(0, $3 - (SELECT pending_count FROM capacity))
            ),
            best_effort_admitted AS MATERIALIZED (
                SELECT new_rows.*
                FROM new_rows, capacity
                WHERE delivery_class = 'best_effort'
                ORDER BY event_id
                LIMIT GREATEST(
                    0,
                    $3 - $4 - (SELECT pending_count FROM capacity)
                    - (SELECT COUNT(*) FROM required_admitted)
                )
            ),
            admitted AS MATERIALIZED (
                SELECT * FROM required_admitted
                UNION ALL
                SELECT * FROM best_effort_admitted
            ),
            inserted AS (
                INSERT INTO deltallm_audit_ingestion_outbox (
                    event_id, record_type, organization_id, delivery_class,
                    payload_json, redacted_payload_json, policy_version, max_attempts
                )
                SELECT
                    admitted.event_id,
                    admitted.record_type,
                    admitted.organization_id,
                    admitted.delivery_class,
                    CASE WHEN policy.enabled THEN admitted.payload ELSE admitted.redacted_payload END,
                    admitted.redacted_payload,
                    policy.version,
                    admitted.max_attempts
                FROM admitted
                CROSS JOIN policy
                ORDER BY admitted.event_id
                ON CONFLICT (event_id) DO NOTHING
                RETURNING event_id
            ),
            bumped AS (
                UPDATE deltallm_telemetry_ingestion_capacity
                SET pending_count = pending_count + (SELECT COUNT(*) FROM inserted),
                    updated_at = NOW()
                WHERE queue_name = 'audit'
                RETURNING pending_count
            )
            SELECT
                input.event_id,
                CASE
                    WHEN existing.event_id IS NOT NULL THEN 'duplicate'
                    WHEN inserted.event_id IS NOT NULL THEN 'accepted'
                    ELSE 'full'
                END AS status,
                COALESCE(
                    (SELECT pending_count FROM bumped),
                    (SELECT pending_count FROM capacity),
                    0
                )::bigint AS pending_count
            FROM input
            LEFT JOIN existing USING (event_id)
            LEFT JOIN inserted USING (event_id)
            ORDER BY input.event_id
            """,
            serialized_envelopes,
            organization_id,
            max_pending_events,
            required_reserve,
        )
        statuses = {str(row["event_id"]): _audit_enqueue_status(row["status"]) for row in rows}
        pending_count = int(rows[0].get("pending_count") or 0) if rows else 0
        return AuditBundleEnqueueResult(statuses=statuses, pending_count=pending_count)

    async def claim_batch(
        self,
        *,
        limit: int,
        worker_id: str,
        claim_token: str,
        lease_seconds: int,
    ) -> list[AuditOutboxRecord]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            """
            WITH expired_candidates AS (
                SELECT event_id
                FROM deltallm_audit_ingestion_outbox
                WHERE status = 'processing'
                  AND lease_expires_at <= NOW()
                  AND attempt_count >= max_attempts
                ORDER BY lease_expires_at, event_id
                FOR UPDATE SKIP LOCKED
                LIMIT $1
            ), expired AS (
                UPDATE deltallm_audit_ingestion_outbox o
                SET status = CASE
                        WHEN o.delivery_class = 'required' THEN 'blocked'
                        ELSE 'failed'
                    END,
                    blocked_at = CASE
                        WHEN o.delivery_class = 'required' THEN NOW()
                        ELSE o.blocked_at
                    END,
                    processed_at = NOW(),
                    last_error = COALESCE(
                        o.last_error,
                        'max_attempts_exhausted_after_lease_expiry'
                    ),
                    locked_by = NULL, claim_token = NULL, lease_expires_at = NULL,
                    updated_at = NOW()
                FROM expired_candidates candidate
                WHERE o.event_id = candidate.event_id
                RETURNING o.event_id, o.delivery_class
            ), adjusted AS (
                UPDATE deltallm_telemetry_ingestion_capacity
                SET pending_count = GREATEST(
                        0,
                        pending_count - (
                            SELECT COUNT(*) FROM expired
                            WHERE delivery_class = 'best_effort'
                        )
                    ),
                    updated_at = NOW()
                WHERE queue_name = 'audit'
                RETURNING pending_count
            ), candidates AS (
                SELECT event_id
                FROM deltallm_audit_ingestion_outbox
                WHERE (
                    status IN ('queued', 'retry')
                    AND next_attempt_at <= NOW()
                    AND attempt_count < max_attempts
                ) OR (
                    status = 'processing'
                    AND lease_expires_at <= NOW()
                    AND attempt_count < max_attempts
                )
                ORDER BY
                    CASE delivery_class WHEN 'required' THEN 0 ELSE 1 END,
                    created_at,
                    event_id
                FOR UPDATE SKIP LOCKED
                LIMIT $1
            )
            UPDATE deltallm_audit_ingestion_outbox o
            SET status = 'processing',
                attempt_count = o.attempt_count + 1,
                locked_by = $2,
                claim_token = $3,
                lease_expires_at = NOW() + make_interval(secs => $4),
                updated_at = NOW()
            FROM candidates c
            WHERE o.event_id = c.event_id
            RETURNING
                o.event_id, o.record_type, o.organization_id, o.delivery_class,
                o.payload_json, o.redacted_payload_json, o.policy_version,
                o.attempt_count, o.max_attempts, o.claim_token
            """,
            max(1, int(limit)),
            worker_id,
            claim_token,
            max(1, int(lease_seconds)),
        )
        return [
            AuditOutboxRecord(
                event_id=str(row["event_id"]),
                record_type=str(row["record_type"]),
                organization_id=(
                    str(row["organization_id"]) if row.get("organization_id") else None
                ),
                delivery_class=parse_audit_delivery_class(row.get("delivery_class")),
                payload=dict(row.get("payload_json") or {}),
                redacted_payload=dict(row.get("redacted_payload_json") or {}),
                policy_version=int(row.get("policy_version") or 0),
                attempt_count=int(row.get("attempt_count") or 1),
                max_attempts=int(row.get("max_attempts") or 1),
                claim_token=str(row["claim_token"]),
            )
            for row in rows
        ]

    async def mark_completed(
        self,
        *,
        event_ids: list[str],
        worker_id: str,
        claim_token: str,
    ) -> int:
        if self.prisma is None or not event_ids:
            return 0
        rows = await self.prisma.query_raw(
            """
            WITH completed AS (
                UPDATE deltallm_audit_ingestion_outbox
                SET status = 'completed', processed_at = NOW(), locked_by = NULL,
                    claim_token = NULL, lease_expires_at = NULL, last_error = NULL,
                    updated_at = NOW()
                WHERE event_id = ANY($1::text[])
                  AND status = 'processing'
                  AND locked_by = $2
                  AND claim_token = $3
                RETURNING event_id
            ),
            adjusted AS (
                UPDATE deltallm_telemetry_ingestion_capacity
                SET pending_count = GREATEST(
                        0,
                        pending_count - (SELECT COUNT(*) FROM completed)
                    ),
                    updated_at = NOW()
                WHERE queue_name = 'audit'
                RETURNING pending_count
            )
            SELECT event_id FROM completed
            """,
            event_ids,
            worker_id,
            claim_token,
        )
        return len(rows)

    async def renew_lease(
        self,
        *,
        event_ids: list[str],
        worker_id: str,
        claim_token: str,
        lease_seconds: int,
    ) -> int:
        if self.prisma is None or not event_ids:
            return 0
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_audit_ingestion_outbox
            SET lease_expires_at = NOW() + make_interval(secs => $4),
                updated_at = NOW()
            WHERE event_id = ANY($1::text[])
              AND status = 'processing'
              AND locked_by = $2
              AND claim_token = $3
              AND lease_expires_at > NOW()
            RETURNING event_id
            """,
            event_ids,
            worker_id,
            claim_token,
            max(1, int(lease_seconds)),
        )
        return len(rows)

    async def mark_retry(
        self,
        *,
        record: AuditOutboxRecord,
        worker_id: str,
        error: str,
    ) -> bool:
        if self.prisma is None:
            return False
        terminal = record.attempt_count >= record.max_attempts
        terminal_status = "blocked" if record.delivery_class == "required" else "failed"
        delay = min(60, 2 ** min(record.attempt_count, 6))
        rows = await self.prisma.query_raw(
            """
            WITH transitioned AS (
                UPDATE deltallm_audit_ingestion_outbox
                SET status = $3,
                    next_attempt_at = $4::timestamp,
                    processed_at = CASE WHEN $3 IN ('failed', 'blocked') THEN NOW() ELSE processed_at END,
                    blocked_at = CASE WHEN $3 = 'blocked' THEN NOW() ELSE blocked_at END,
                    last_error = $5,
                    locked_by = NULL,
                    claim_token = NULL,
                    lease_expires_at = NULL,
                    updated_at = NOW()
                WHERE event_id = $1
                  AND status = 'processing'
                  AND locked_by = $2
                  AND claim_token = $6
                RETURNING event_id
            ),
            adjusted AS (
                UPDATE deltallm_telemetry_ingestion_capacity
                SET pending_count = GREATEST(
                        0,
                        pending_count - CASE
                            WHEN $3 = 'failed' THEN (SELECT COUNT(*) FROM transitioned)
                            ELSE 0
                        END
                    ),
                    updated_at = NOW()
                WHERE queue_name = 'audit'
                RETURNING pending_count
            )
            SELECT event_id FROM transitioned
            """,
            record.event_id,
            worker_id,
            terminal_status if terminal else "retry",
            datetime.now(tz=UTC) + timedelta(seconds=delay),
            str(error)[:2000],
            record.claim_token,
        )
        return bool(rows) and terminal

    async def pending_stats(self) -> tuple[int, float]:
        if self.prisma is None:
            return 0, 0.0
        rows = await self.prisma.query_raw(
            """
            SELECT
                COUNT(*)::bigint AS count,
                COALESCE(EXTRACT(EPOCH FROM (NOW() - MIN(created_at))), 0)::float8 AS oldest_age
            FROM deltallm_audit_ingestion_outbox
            WHERE status IN ('queued', 'retry', 'processing', 'blocked')
               OR (status = 'failed' AND delivery_class = 'required')
            """
        )
        row = rows[0] if rows else {}
        return int(row.get("count") or 0), float(row.get("oldest_age") or 0.0)

    async def drainable_count(self) -> int:
        if self.prisma is None:
            return 0
        rows = await self.prisma.query_raw(
            """
            SELECT COUNT(*)::bigint AS count
            FROM deltallm_audit_ingestion_outbox
            WHERE status IN ('queued', 'retry', 'processing')
            """
        )
        return int((rows[0] if rows else {}).get("count") or 0)

    async def reconcile_capacity(self) -> int:
        if self.prisma is None:
            return 0
        async with self._transaction() as tx:
            await tx.query_raw(
                """
                SELECT pg_advisory_xact_lock(
                    hashtextextended('deltallm:audit-ingestion-capacity', 0)
                )::text AS locked
                """
            )
            capacity_rows = await tx.query_raw(
                """
                SELECT pending_count
                FROM deltallm_telemetry_ingestion_capacity
                WHERE queue_name = 'audit'
                FOR UPDATE
                """
            )
            if not capacity_rows:
                raise RuntimeError("audit ingestion capacity row is missing")
            count_rows = await tx.query_raw(
                """
                SELECT COUNT(*)::bigint AS count
                FROM deltallm_audit_ingestion_outbox
                WHERE status IN ('queued', 'retry', 'processing', 'blocked')
                   OR (status = 'failed' AND delivery_class = 'required')
                """
            )
            actual = int((count_rows[0] if count_rows else {}).get("count") or 0)
            rows = await tx.query_raw(
                """
                UPDATE deltallm_telemetry_ingestion_capacity
                SET pending_count = $1, updated_at = NOW()
                WHERE queue_name = 'audit'
                RETURNING pending_count
                """,
                actual,
            )
            if not rows:
                raise RuntimeError("audit ingestion capacity row disappeared")
            return int(rows[0].get("pending_count") or 0)

    @asynccontextmanager
    async def _transaction(self):  # noqa: ANN202
        if self.prisma is None:
            raise RuntimeError("audit ingestion database is unavailable")
        if is_prisma_transaction_client(self.prisma):
            yield self.prisma
            return
        tx_factory = getattr(self.prisma, "tx", None)
        if callable(tx_factory):
            async with tx_factory() as tx:
                yield tx
            return
        raise RuntimeError("audit ingestion database does not support transactions")

    async def cleanup_terminal(
        self,
        *,
        completed_retention_hours: int,
        failed_retention_days: int,
        limit: int,
    ) -> int:
        if self.prisma is None:
            return 0
        rows = await self.prisma.query_raw(
            """
            WITH candidates AS (
                SELECT event_id
                FROM deltallm_audit_ingestion_outbox
                WHERE (
                    status = 'completed'
                    AND processed_at <= NOW() - make_interval(hours => $1::int)
                ) OR (
                    status = 'failed'
                    AND delivery_class = 'best_effort'
                    AND COALESCE(processed_at, updated_at) <= NOW() - make_interval(days => $2::int)
                )
                ORDER BY COALESCE(processed_at, updated_at), event_id
                LIMIT $3
                FOR UPDATE SKIP LOCKED
            )
            DELETE FROM deltallm_audit_ingestion_outbox o
            USING candidates c
            WHERE o.event_id = c.event_id
            RETURNING o.event_id
            """,
            max(0, int(completed_retention_hours)),
            max(1, int(failed_retention_days)),
            max(1, int(limit)),
        )
        return len(rows)

    async def replay_blocked(
        self,
        *,
        event_id: str,
        replayed_by: str,
    ) -> bool:
        """Requeue one required audit event without changing its identity or envelope."""

        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_audit_ingestion_outbox
            SET status = 'retry', attempt_count = 0, next_attempt_at = NOW(),
                last_error = NULL, locked_by = NULL, claim_token = NULL,
                lease_expires_at = NULL, processed_at = NULL, blocked_at = NULL,
                replay_count = replay_count + 1, last_replayed_at = NOW(),
                last_replayed_by = $2, updated_at = NOW()
            WHERE event_id = $1
              AND delivery_class = 'required'
              AND status IN ('blocked', 'failed')
            RETURNING event_id
            """,
            event_id,
            replayed_by,
        )
        return bool(rows)

    async def redact_active_for_current_policy(self, organization_id: str) -> int:
        """Scrub active envelopes when the locked, authoritative policy is disabled."""

        if self.prisma is None:
            return 0
        rows = await self.prisma.query_raw(
            """
            WITH policy_lock AS MATERIALIZED (
                SELECT pg_advisory_xact_lock(
                    hashtextextended('deltallm:audit-content-policy:' || $1, 0)
                )
            ), policy AS MATERIALIZED (
                SELECT
                    audit_content_storage_enabled AS enabled,
                    audit_content_policy_version AS version
                FROM deltallm_organizationtable, policy_lock
                WHERE organization_id = $1
                LIMIT 1
            )
            UPDATE deltallm_audit_ingestion_outbox o
            SET payload_json = o.redacted_payload_json,
                policy_version = policy.version,
                updated_at = NOW()
            FROM policy
            WHERE o.organization_id = $1
              AND policy.enabled = FALSE
              AND o.status IN ('queued', 'retry', 'processing', 'blocked', 'failed')
              AND (
                    o.payload_json IS DISTINCT FROM o.redacted_payload_json
                    OR o.policy_version IS DISTINCT FROM policy.version
                  )
            RETURNING o.event_id
            """,
            organization_id,
        )
        return len(rows)

    async def redact_claimed_records(
        self,
        *,
        event_ids: list[str],
        worker_id: str,
        claim_token: str,
    ) -> int:
        """Scrub claimed envelopes using the policy snapshot locked by the caller."""

        if self.prisma is None or not event_ids:
            return 0
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_audit_ingestion_outbox o
            SET payload_json = o.redacted_payload_json,
                policy_version = policy.audit_content_policy_version,
                updated_at = NOW()
            FROM deltallm_organizationtable policy
            WHERE o.event_id = ANY($1::text[])
              AND o.status = 'processing'
              AND o.locked_by = $2
              AND o.claim_token = $3
              AND o.organization_id = policy.organization_id
              AND policy.audit_content_storage_enabled = FALSE
            RETURNING o.event_id
            """,
            event_ids,
            worker_id,
            claim_token,
        )
        return len(rows)

    async def redact_pending_for_organization(
        self,
        *,
        organization_id: str,
        policy_version: int,
    ) -> int:
        if self.prisma is None:
            return 0
        rows = await self.prisma.query_raw(
            """
            WITH policy_lock AS MATERIALIZED (
                SELECT pg_advisory_xact_lock(
                    hashtextextended('deltallm:audit-content-policy:' || $1, 0)
                )
            )
            UPDATE deltallm_audit_ingestion_outbox
            SET payload_json = redacted_payload_json,
                policy_version = $2,
                updated_at = NOW()
            FROM policy_lock
            WHERE organization_id = $1
              AND status IN ('queued', 'retry', 'processing', 'blocked', 'failed')
            RETURNING event_id
            """,
            organization_id,
            int(policy_version),
        )
        return len(rows)

    async def get_content_policy(self, organization_id: str) -> tuple[bool, int]:
        if self.prisma is None:
            return False, 0
        rows = await self.prisma.query_raw(
            """
            SELECT
                audit_content_storage_enabled AS enabled,
                audit_content_policy_version AS version
            FROM deltallm_organizationtable
            WHERE organization_id = $1
            LIMIT 1
            """,
            organization_id,
        )
        row = rows[0] if rows else {}
        return bool(row.get("enabled", False)), int(row.get("version") or 0)

    async def lock_content_policies(
        self,
        organization_ids: list[str],
    ) -> None:
        """Lock policy keys in deterministic order without reading durable policy state."""

        if self.prisma is None or not organization_ids:
            return
        normalized = sorted({str(value) for value in organization_ids if str(value)})
        await self.prisma.query_raw(
            """
            WITH requested AS MATERIALIZED (
                SELECT organization_id
                FROM unnest($1::text[]) AS requested(organization_id)
                ORDER BY organization_id
            ), policy_locks AS MATERIALIZED (
                SELECT pg_advisory_xact_lock(
                    hashtextextended('deltallm:audit-content-policy:' || organization_id, 0)
                ) AS locked
                FROM requested
                ORDER BY organization_id
            )
            SELECT COUNT(*)::int AS locked_count
            FROM policy_locks
            """,
            normalized,
        )

    async def get_content_policies(
        self,
        organization_ids: list[str],
    ) -> dict[str, tuple[bool, int]]:
        """Read authoritative policy state after the caller has acquired policy locks."""

        if self.prisma is None or not organization_ids:
            return {}
        normalized = sorted({str(value) for value in organization_ids if str(value)})
        rows = await self.prisma.query_raw(
            """
            WITH requested AS MATERIALIZED (
                SELECT organization_id
                FROM unnest($1::text[]) AS requested(organization_id)
            )
            SELECT
                requested.organization_id,
                COALESCE(policy.audit_content_storage_enabled, FALSE) AS enabled,
                COALESCE(policy.audit_content_policy_version, 0)::bigint AS version
            FROM requested
            LEFT JOIN deltallm_organizationtable policy
              ON policy.organization_id = requested.organization_id
            ORDER BY requested.organization_id
            """,
            normalized,
        )
        return {
            str(row["organization_id"]): (
                bool(row.get("enabled", False)),
                int(row.get("version") or 0),
            )
            for row in rows
        }

    async def lock_content_policy(self, organization_id: str) -> None:
        if self.prisma is None:
            return
        await self.prisma.query_raw(
            """
            SELECT pg_advisory_xact_lock(
                hashtextextended('deltallm:audit-content-policy:' || $1, 0)
            )::text AS locked
            """,
            organization_id,
        )
