from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from src.billing.ledger import SpendLedgerService
from src.billing.spend_events import build_spend_event

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
        log_start = start_time or now
        log_end = end_time or now

        event_entry = build_spend_event(
            request_id=request_id,
            api_key=api_key,
            user_id=user_id,
            team_id=team_id,
            organization_id=organization_id,
            end_user_id=end_user_id,
            model=model,
            call_type=call_type,
            usage=usage_data,
            cost=cost,
            metadata=meta,
            cache_hit=cache_hit,
            start_time=log_start,
            end_time=log_end,
        )

        try:
            import uuid as _uuid

            row_id = str(_uuid.uuid4())
            st = event_entry["start_time"]
            et = event_entry["end_time"]
            start_iso = st.isoformat() if isinstance(st, datetime) else str(st)
            end_iso = et.isoformat() if isinstance(et, datetime) else str(et)
            await self.db.execute_raw(
                """
                INSERT INTO deltallm_spendlog_events (
                    id,
                    request_id,
                    call_type,
                    api_key,
                    user_id,
                    team_id,
                    organization_id,
                    end_user_id,
                    model,
                    deployment_model,
                    provider,
                    api_base,
                    spend,
                    provider_cost,
                    billing_unit,
                    pricing_tier,
                    total_tokens,
                    input_tokens,
                    output_tokens,
                    cached_input_tokens,
                    cached_output_tokens,
                    input_audio_tokens,
                    output_audio_tokens,
                    input_characters,
                    output_characters,
                    duration_seconds,
                    image_count,
                    rerank_units,
                    start_time,
                    end_time,
                    latency_ms,
                    cache_hit,
                    cache_key,
                    request_tags,
                    unpriced_reason,
                    pricing_fields_used,
                    usage_snapshot,
                    metadata
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29::timestamp,$30::timestamp,$31,$32,$33,$34,$35,$36::jsonb,$37::jsonb,$38::jsonb
                )
                """,
                row_id,
                event_entry["request_id"],
                event_entry["call_type"],
                event_entry["api_key"],
                event_entry["user_id"],
                event_entry["team_id"],
                event_entry["organization_id"],
                event_entry["end_user_id"],
                event_entry["model"],
                event_entry["deployment_model"],
                event_entry["provider"],
                event_entry["api_base"],
                event_entry["spend"],
                event_entry["provider_cost"],
                event_entry["billing_unit"],
                event_entry["pricing_tier"],
                event_entry["total_tokens"],
                event_entry["input_tokens"],
                event_entry["output_tokens"],
                event_entry["cached_input_tokens"],
                event_entry["cached_output_tokens"],
                event_entry["input_audio_tokens"],
                event_entry["output_audio_tokens"],
                event_entry["input_characters"],
                event_entry["output_characters"],
                event_entry["duration_seconds"],
                event_entry["image_count"],
                event_entry["rerank_units"],
                start_iso,
                end_iso,
                event_entry["latency_ms"],
                event_entry["cache_hit"],
                event_entry["cache_key"],
                event_entry["request_tags"],
                event_entry["unpriced_reason"],
                json.dumps(event_entry["pricing_fields_used"], default=str),
                json.dumps(event_entry["usage_snapshot"], default=str),
                json.dumps(event_entry["metadata"], default=str),
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("failed to write normalized spend event: %s", exc)

        await self.ledger.increment_spend(
            api_key=api_key,
            user_id=user_id,
            team_id=team_id,
            organization_id=organization_id,
            model=model,
            cost=float(cost),
        )
