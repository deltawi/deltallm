from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from src.billing.money import money_string

if TYPE_CHECKING:
    from prisma import Prisma

EnqueueOutcome = Literal["queued", "throttled", "busy", "full", "inactive"]


@dataclass(frozen=True)
class BudgetNotification:
    organization_id: str
    notification_id: str
    spend: Decimal
    soft_budget: Decimal
    hard_budget: Decimal | None
    claim_token: str
    attempt_count: int
    status: str
    outcome: str | None = None


class BudgetNotificationRepository:
    """Control-pool persistence; one bounded claim per worker, fenced transitions."""

    def __init__(self, db: Prisma) -> None:
        self.db = db

    async def enqueue(
        self,
        *,
        organization_id: str,
        spend: Decimal,
        soft_budget: Decimal,
        hard_budget: Decimal | None,
        ttl_seconds: int,
    ) -> EnqueueOutcome:
        rows = await self.db.query_raw(
            "SELECT deltallm_enqueue_budget_notification($1,$2,$3::numeric,$4::numeric,$5::numeric,$6::int) AS outcome",
            organization_id,
            str(uuid4()),
            money_string(spend),
            money_string(soft_budget),
            money_string(hard_budget) if hard_budget is not None else None,
            ttl_seconds,
        )
        outcome = rows[0]["outcome"]
        if outcome not in {"queued", "throttled", "busy", "full", "inactive"}:
            raise RuntimeError("invalid budget notification acceptance result")
        return outcome

    async def probe(self) -> None:
        await self.db.query_raw("SELECT notification_id FROM deltallm_budgetnotification LIMIT 0")

    async def claim(self) -> BudgetNotification | None:
        rows = await self.db.query_raw(
            """
            WITH candidate AS MATERIALIZED (
                SELECT organization_id FROM deltallm_budgetnotification
                WHERE status IN ('pending','processing','dispatching') AND available_at <= NOW()
                ORDER BY available_at, organization_id FOR UPDATE SKIP LOCKED LIMIT 1
            )
            UPDATE deltallm_budgetnotification n SET
                status = CASE WHEN n.status = 'dispatching' OR n.attempt_count >= 5
                              THEN 'failed' ELSE 'processing' END,
                outcome = CASE WHEN n.status = 'dispatching' THEN 'delivery_unknown'
                               WHEN n.attempt_count >= 5 THEN 'attempts_exhausted' END,
                dedupe_until = CASE WHEN n.status = 'dispatching'
                    THEN GREATEST(n.dedupe_until, NOW() + INTERVAL '7 days') ELSE n.dedupe_until END,
                attempt_count = LEAST(n.attempt_count + 1, 5),
                claim_token = $1, lease_expires_at = NOW() + INTERVAL '30 seconds',
                available_at = NOW() + INTERVAL '30 seconds', updated_at = NOW()
            FROM candidate c WHERE n.organization_id = c.organization_id
            RETURNING n.organization_id, n.notification_id, n.spend::text,
                n.soft_budget::text, n.hard_budget::text, n.claim_token, n.attempt_count, n.status, n.outcome
            """,
            str(uuid4()),
        )
        if not rows:
            return None
        row = rows[0]
        return BudgetNotification(
            organization_id=row["organization_id"],
            notification_id=row["notification_id"],
            spend=Decimal(row["spend"]),
            soft_budget=Decimal(row["soft_budget"]),
            hard_budget=Decimal(row["hard_budget"]) if row["hard_budget"] is not None else None,
            claim_token=row["claim_token"],
            attempt_count=int(row["attempt_count"]),
            status=row["status"],
            outcome=row["outcome"],
        )

    async def begin_dispatch(self, record: BudgetNotification) -> bool:
        rows = await self.db.query_raw(
            """
            UPDATE deltallm_budgetnotification n SET status = 'dispatching', updated_at = NOW()
            WHERE notification_id = $1 AND claim_token = $2 AND status = 'processing'
              AND lease_expires_at > NOW()
              AND EXISTS (SELECT 1 FROM deltallm_organizationtable o
                          WHERE o.organization_id = n.organization_id AND o.lifecycle_state = 'active')
            RETURNING notification_id
            """,
            record.notification_id,
            record.claim_token,
        )
        return bool(rows)

    async def finish(
        self,
        record: BudgetNotification,
        *,
        outcome: str,
        delivered: bool,
    ) -> None:
        if outcome not in {"delivered", "delivery_unknown", "undeliverable", "disabled"}:
            raise ValueError("unsupported notification outcome")
        await self.db.execute_raw(
            """
            UPDATE deltallm_budgetnotification SET status = $3, outcome = $4,
                claim_token = NULL, lease_expires_at = NULL, updated_at = NOW(),
                dedupe_until = CASE WHEN $4 = 'delivery_unknown'
                    THEN GREATEST(dedupe_until, NOW() + INTERVAL '7 days') ELSE dedupe_until END
            WHERE notification_id = $1 AND claim_token = $2
              AND status IN ('processing','dispatching') AND lease_expires_at > NOW()
            """,
            record.notification_id,
            record.claim_token,
            "completed" if delivered else "failed",
            outcome,
        )

    async def retry_preparation(self, record: BudgetNotification, *, delay: float) -> None:
        await self.db.execute_raw(
            """
            UPDATE deltallm_budgetnotification SET
                status = CASE WHEN attempt_count >= 5 THEN 'failed' ELSE 'pending' END,
                outcome = 'preparation_failed', available_at = NOW() + make_interval(secs => $3),
                claim_token = NULL, lease_expires_at = NULL, updated_at = NOW()
            WHERE notification_id = $1 AND claim_token = $2 AND status = 'processing'
              AND lease_expires_at > NOW()
            """,
            record.notification_id,
            record.claim_token,
            delay,
        )

    async def cleanup(self) -> None:
        await self.db.execute_raw(
            """
            DELETE FROM deltallm_budgetnotification WHERE organization_id IN (
                SELECT organization_id FROM deltallm_budgetnotification
                WHERE status IN ('completed','failed') AND dedupe_until < NOW()
                  AND updated_at < NOW() - INTERVAL '7 days'
                ORDER BY updated_at LIMIT 100 FOR UPDATE SKIP LOCKED
            )
            """
        )
