from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from src.billing.ledger import SpendLedgerService

logger = logging.getLogger(__name__)


class SpendTrackingService:
    """Writes per-request spend logs and updates cumulative spend."""

    def __init__(self, db_client: Any | None, ledger: SpendLedgerService | None = None) -> None:
        self.db = db_client
        self.ledger = ledger or SpendLedgerService(db_client)

    async def log_spend(
        self,
        *,
        request_id: str,
        api_key: str,
        user_id: str | None,
        team_id: str | None,
        organization_id: str | None,
        end_user_id: str | None,
        model: str,
        call_type: str,
        usage: dict[str, int] | None,
        cost: float,
        metadata: dict[str, Any] | None = None,
        cache_hit: bool = False,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> None:
        if self.db is None:
            return

        usage_data = usage or {}
        meta = metadata or {}
        now = datetime.now(tz=UTC)
        tags = meta.get("tags") if isinstance(meta.get("tags"), list) else []

        log_entry = {
            "request_id": request_id,
            "call_type": call_type,
            "api_key": api_key,
            "spend": float(cost),
            "total_tokens": int(usage_data.get("total_tokens") or 0),
            "prompt_tokens": int(usage_data.get("prompt_tokens") or 0),
            "completion_tokens": int(usage_data.get("completion_tokens") or 0),
            "start_time": start_time or now,
            "end_time": end_time or now,
            "model": model,
            "api_base": _to_str_or_none(meta.get("api_base")),
            "user": user_id,
            "team_id": team_id,
            "end_user": end_user_id,
            "metadata": meta,
            "cache_hit": bool(cache_hit),
            "cache_key": _to_str_or_none(meta.get("cache_key")),
            "request_tags": [str(tag) for tag in tags],
        }

        try:
            await self.db.query_raw(
                """
                INSERT INTO litellm_spendlogs (
                    request_id,
                    call_type,
                    api_key,
                    spend,
                    total_tokens,
                    prompt_tokens,
                    completion_tokens,
                    start_time,
                    end_time,
                    model,
                    api_base,
                    "user",
                    team_id,
                    end_user,
                    metadata,
                    cache_hit,
                    cache_key,
                    request_tags
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18
                )
                """,
                log_entry["request_id"],
                log_entry["call_type"],
                log_entry["api_key"],
                log_entry["spend"],
                log_entry["total_tokens"],
                log_entry["prompt_tokens"],
                log_entry["completion_tokens"],
                log_entry["start_time"],
                log_entry["end_time"],
                log_entry["model"],
                log_entry["api_base"],
                log_entry["user"],
                log_entry["team_id"],
                log_entry["end_user"],
                log_entry["metadata"],
                log_entry["cache_hit"],
                log_entry["cache_key"],
                log_entry["request_tags"],
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("failed to write spend log", extra={"request_id": request_id, "error": str(exc)})

        await self.ledger.increment_spend(
            api_key=api_key,
            user_id=user_id,
            team_id=team_id,
            organization_id=organization_id,
            cost=float(cost),
        )


def _to_str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
