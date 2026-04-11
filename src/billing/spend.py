from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, Literal

from src.billing.ledger import SpendLedgerService
from src.billing.spend_events import build_spend_event

logger = logging.getLogger(__name__)

SpendLogOnceResult = Literal["inserted", "duplicate"]


class SpendTrackingService:
    """Writes per-request spend logs and updates cumulative spend."""

    def __init__(self, db_client: Any | None, ledger: SpendLedgerService | None = None) -> None:
        self.db = db_client
        self.ledger = ledger or SpendLedgerService(db_client)

    def with_db(self, db_client: Any | None) -> SpendTrackingService:
        return SpendTrackingService(db_client, ledger=SpendLedgerService(db_client))

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
        await self._log_request_event(
            request_id=request_id,
            api_key=api_key,
            user_id=user_id,
            team_id=team_id,
            organization_id=organization_id,
            end_user_id=end_user_id,
            model=model,
            call_type=call_type,
            usage=usage,
            cost=cost,
            metadata=metadata,
            cache_hit=cache_hit,
            start_time=start_time,
            end_time=end_time,
            update_ledger=True,
        )

    async def log_request_failure(
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
        metadata: dict[str, Any] | None = None,
        cache_hit: bool = False,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        http_status_code: int | None = None,
        exc: Exception | None = None,
        error_type: str | None = None,
    ) -> None:
        await self._log_request_event(
            request_id=request_id,
            api_key=api_key,
            user_id=user_id,
            team_id=team_id,
            organization_id=organization_id,
            end_user_id=end_user_id,
            model=model,
            call_type=call_type,
            usage=None,
            cost=0.0,
            metadata=_failure_metadata(
                metadata=metadata,
                exc=exc,
                http_status_code=http_status_code,
            ),
            cache_hit=cache_hit,
            start_time=start_time,
            end_time=end_time,
            status="error",
            http_status_code=http_status_code,
            error_type=error_type or getattr(exc, "error_type", None) or (exc.__class__.__name__ if exc is not None else None),
            update_ledger=False,
        )

    async def log_spend_once(
        self,
        *,
        event_id: str,
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
    ) -> SpendLogOnceResult:
        if self.db is None:
            raise RuntimeError("spend tracking database is unavailable")

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
        result = await self._write_event_once(event_entry, event_id=event_id)
        if result == "duplicate":
            return result
        await self.ledger.increment_spend(
            api_key=api_key,
            user_id=user_id,
            team_id=team_id,
            organization_id=organization_id,
            model=model,
            cost=float(cost),
        )
        return result

    async def _log_request_event(
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
        metadata: dict[str, Any] | None,
        cache_hit: bool,
        start_time: datetime | None,
        end_time: datetime | None,
        status: str = "success",
        http_status_code: int | None = None,
        error_type: str | None = None,
        update_ledger: bool,
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
            status=status,
            http_status_code=http_status_code,
            error_type=error_type,
        )

        await self._write_event(event_entry)
        if not update_ledger:
            return

        await self.ledger.increment_spend(
            api_key=api_key,
            user_id=user_id,
            team_id=team_id,
            organization_id=organization_id,
            model=model,
            cost=float(cost),
        )

    async def _write_event(
        self,
        event_entry: dict[str, Any],
        *,
        event_id: str | None = None,
        on_conflict_do_nothing: bool = False,
    ) -> bool:
        try:
            import uuid as _uuid

            row_id = str(event_id or _uuid.uuid4())
            st = event_entry["start_time"]
            et = event_entry["end_time"]
            start_iso = st.isoformat() if isinstance(st, datetime) else str(st)
            end_iso = et.isoformat() if isinstance(et, datetime) else str(et)
            conflict_sql = "ON CONFLICT (id) DO NOTHING" if on_conflict_do_nothing else ""
            rows = await self.db.query_raw(
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
                    metadata,
                    status,
                    http_status_code,
                    error_type
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29::timestamp,$30::timestamp,$31,$32,$33,$34,$35,$36::jsonb,$37::jsonb,$38::jsonb,$39,$40,$41
                )
                """
                + conflict_sql
                + """
                RETURNING id
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
                event_entry["status"],
                event_entry["http_status_code"],
                event_entry["error_type"],
            )
            return bool(rows)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("failed to write normalized spend event: %s", exc)
            return False

    async def _write_event_once(self, event_entry: dict[str, Any], *, event_id: str) -> SpendLogOnceResult:
        import uuid as _uuid

        row_id = str(event_id or _uuid.uuid4())
        st = event_entry["start_time"]
        et = event_entry["end_time"]
        start_iso = st.isoformat() if isinstance(st, datetime) else str(st)
        end_iso = et.isoformat() if isinstance(et, datetime) else str(et)
        rows = await self.db.query_raw(
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
                metadata,
                status,
                http_status_code,
                error_type
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29::timestamp,$30::timestamp,$31,$32,$33,$34,$35,$36::jsonb,$37::jsonb,$38::jsonb,$39,$40,$41
            )
            ON CONFLICT (id) DO NOTHING
            RETURNING id
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
            event_entry["status"],
            event_entry["http_status_code"],
            event_entry["error_type"],
        )
        return "inserted" if rows else "duplicate"


def _failure_metadata(
    *,
    metadata: dict[str, Any] | None,
    exc: Exception | None,
    http_status_code: int | None,
) -> dict[str, Any]:
    base = dict(metadata or {})
    error_payload = dict(base.get("error") or {}) if isinstance(base.get("error"), dict) else {}
    if exc is not None:
        error_payload.setdefault("type", getattr(exc, "error_type", None) or exc.__class__.__name__)
        error_payload.setdefault("message", str(exc))
        if getattr(exc, "code", None):
            error_payload.setdefault("code", getattr(exc, "code"))
    if http_status_code is not None:
        error_payload.setdefault("http_status_code", int(http_status_code))
    if error_payload:
        base["error"] = error_payload
    return base
