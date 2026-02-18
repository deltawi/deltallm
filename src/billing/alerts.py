from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AlertConfig:
    budget_alert_ttl: int = 3600


class AlertService:
    """Alerting abstraction for budget and reporting notifications."""

    def __init__(self, config: AlertConfig | None = None, redis_client: Any | None = None) -> None:
        self.config = config or AlertConfig()
        self.redis = redis_client

    async def send_budget_alert(
        self,
        *,
        entity_type: str,
        entity_id: str,
        current_spend: float,
        soft_budget: float | None,
        hard_budget: float | None,
    ) -> None:
        if not await self._check_alert_rate_limit("budget", entity_id):
            return

        percentage = (current_spend / hard_budget * 100.0) if hard_budget and hard_budget > 0 else 0.0
        payload = {
            "type": "budget_alert",
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "entity_type": entity_type,
            "entity_id": entity_id,
            "current_spend": float(current_spend),
            "soft_budget": float(soft_budget) if soft_budget is not None else None,
            "hard_budget": float(hard_budget) if hard_budget is not None else None,
            "percentage": percentage,
        }

        logger.warning("budget alert", extra=payload)

    async def _check_alert_rate_limit(self, alert_type: str, entity_id: str) -> bool:
        if self.redis is None:
            return True

        key = f"alert:{alert_type}:{entity_id}"
        if await self.redis.exists(key):
            return False

        await self.redis.setex(key, self.config.budget_alert_ttl, "1")
        return True
