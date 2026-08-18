from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from datetime import UTC, datetime
from typing import Any, Literal, Sequence

from src.billing.ledger import SpendLedgerService
from src.billing.money import canonical_money, money_string
from src.billing.spend_events import build_spend_event
from src.db.client import is_prisma_transaction_client

logger = logging.getLogger(__name__)

SpendLogOnceResult = Literal["inserted", "duplicate"]

_POSTGRES_INTEGER_FIELDS = (
    "total_tokens",
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "cached_output_tokens",
    "input_audio_tokens",
    "output_audio_tokens",
    "input_characters",
    "output_characters",
    "image_count",
    "rerank_units",
    "latency_ms",
    "http_status_code",
)


@dataclass(frozen=True, slots=True)
class PreparedSpendEvent:
    event_id: str
    event_type: str
    row: dict[str, Any]
    event_entry: dict[str, Any]


class SpendTrackingService:
    """Writes per-request spend logs and updates cumulative spend."""

    def __init__(self, db_client: Any | None, ledger: SpendLedgerService | None = None) -> None:
        self.db = db_client
        self.ledger = ledger or SpendLedgerService(db_client)

    def with_db(self, db_client: Any | None) -> SpendTrackingService:
        return SpendTrackingService(db_client, ledger=SpendLedgerService(db_client, strict=True))

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
        owner_account_id: str | None = None,
        owner_snapshot_complete: bool = True,
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
            owner_account_id=owner_account_id,
            owner_snapshot_complete=owner_snapshot_complete,
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
        owner_account_id: str | None = None,
        owner_snapshot_complete: bool = True,
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
            error_type=error_type
            or getattr(exc, "error_type", None)
            or (exc.__class__.__name__ if exc is not None else None),
            owner_account_id=owner_account_id,
            owner_snapshot_complete=owner_snapshot_complete,
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
        owner_account_id: str | None = None,
        owner_snapshot_complete: bool = True,
    ) -> SpendLogOnceResult:
        if self.db is None:
            raise RuntimeError("spend tracking database is unavailable")

        if not is_prisma_transaction_client(self.db):
            tx_factory = getattr(self.db, "tx", None)
            if callable(tx_factory):
                async with tx_factory() as tx:
                    return await self.with_db(tx)._log_spend_once_in_current_transaction(
                        event_id=event_id,
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
                        owner_account_id=owner_account_id,
                        owner_snapshot_complete=owner_snapshot_complete,
                    )

        # A caller may hand us an already-open Prisma transaction directly
        # (batch completion does this). Keep ledger failures strict in that
        # case so the caller's transaction cannot commit only the event row.
        current_service = self if self.ledger.strict else self.with_db(self.db)
        return await current_service._log_spend_once_in_current_transaction(
            event_id=event_id,
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
            owner_account_id=owner_account_id,
            owner_snapshot_complete=owner_snapshot_complete,
        )

    async def _log_spend_once_in_current_transaction(
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
        metadata: dict[str, Any] | None,
        cache_hit: bool,
        start_time: datetime | None,
        end_time: datetime | None,
        owner_account_id: str | None,
        owner_snapshot_complete: bool,
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
            owner_account_id=owner_account_id,
            owner_snapshot_complete=owner_snapshot_complete,
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
            cost=money_string(cost),
        )
        return result

    async def log_request_failure_once(
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
        metadata: dict[str, Any] | None = None,
        cache_hit: bool = False,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        http_status_code: int | None = None,
        error_type: str | None = None,
        owner_account_id: str | None = None,
        owner_snapshot_complete: bool = True,
    ) -> SpendLogOnceResult:
        if self.db is None:
            raise RuntimeError("spend tracking database is unavailable")
        now = datetime.now(tz=UTC)
        event_entry = build_spend_event(
            request_id=request_id,
            api_key=api_key,
            user_id=user_id,
            team_id=team_id,
            organization_id=organization_id,
            end_user_id=end_user_id,
            model=model,
            call_type=call_type,
            usage={},
            cost=0.0,
            metadata=metadata or {},
            cache_hit=cache_hit,
            start_time=start_time or now,
            end_time=end_time or now,
            status="error",
            http_status_code=http_status_code,
            error_type=error_type,
            owner_account_id=owner_account_id,
            owner_snapshot_complete=owner_snapshot_complete,
        )
        return await self._write_event_once(event_entry, event_id=event_id)

    async def log_batch_once(
        self,
        events: Sequence[tuple[str, str, dict[str, Any]]],
    ) -> tuple[set[str], dict[str, int]]:
        """Insert a claimed outbox batch and aggregate only newly inserted spend."""

        prepared = [
            self.prepare_batch_event(
                event_id=event_id,
                event_type=event_type,
                payload=payload,
            )
            for event_id, event_type, payload in events
        ]
        return await self.log_prepared_batch_once(prepared)

    def prepare_batch_event(
        self,
        *,
        event_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> PreparedSpendEvent:
        """Normalize and validate one outbox record before a bulk transaction."""

        if event_type not in {"spend", "request_failure"}:
            raise ValueError(f"unsupported spend outbox event type: {event_type}")
        usage = payload.get("usage") if event_type == "spend" else None
        if usage is not None and not isinstance(usage, dict):
            raise ValueError("spend usage must be an object")
        metadata = payload.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("spend metadata must be an object")

        now = datetime.now(tz=UTC)
        start_time = payload.get("start_time") or now
        end_time = payload.get("end_time") or now
        if not isinstance(start_time, datetime) or not isinstance(end_time, datetime):
            raise ValueError("spend timestamps must be datetime values")
        exact_cost = canonical_money(
            payload.get("cost_exact", payload.get("cost")) if event_type == "spend" else 0
        )
        cost = float(exact_cost)

        event_entry = build_spend_event(
            request_id=str(payload.get("request_id") or ""),
            api_key=str(payload.get("api_key") or ""),
            user_id=payload.get("user_id"),
            team_id=payload.get("team_id"),
            organization_id=payload.get("organization_id"),
            end_user_id=payload.get("end_user_id"),
            model=str(payload.get("model") or ""),
            call_type=str(payload.get("call_type") or ""),
            usage=usage,
            cost=cost,
            metadata=dict(metadata or {}),
            cache_hit=bool(payload.get("cache_hit", False)),
            start_time=start_time,
            end_time=end_time,
            status="success" if event_type == "spend" else "error",
            http_status_code=payload.get("http_status_code"),
            error_type=payload.get("error_type"),
            owner_account_id=payload.get("owner_account_id"),
            owner_snapshot_complete=bool(payload.get("owner_snapshot_complete", True)),
        )
        for field in _POSTGRES_INTEGER_FIELDS:
            value = event_entry.get(field)
            if value is not None and not -(2**31) <= int(value) <= 2**31 - 1:
                raise ValueError(f"{field} is outside the PostgreSQL integer range")

        row = dict(event_entry)
        row["id"] = str(event_id)
        row["spend_exact"] = money_string(exact_cost)
        provider_cost = event_entry.get("provider_cost")
        row["provider_cost_exact"] = (
            money_string(provider_cost) if provider_cost is not None else None
        )
        row["start_time"] = start_time.isoformat()
        row["end_time"] = end_time.isoformat()
        # Fail before batching if nested metadata contains non-finite values.
        json.dumps(row, default=str, allow_nan=False)
        return PreparedSpendEvent(
            event_id=str(event_id),
            event_type=event_type,
            row=row,
            event_entry=event_entry,
        )

    async def log_prepared_batch_once(
        self,
        prepared: Sequence[PreparedSpendEvent],
    ) -> tuple[set[str], dict[str, int]]:
        """Persist a validated batch and aggregate only newly inserted spend."""

        if self.db is None:
            raise RuntimeError("spend tracking database is unavailable")

        if not prepared:
            return set(), {
                "api_key": 0,
                "user": 0,
                "team": 0,
                "organization": 0,
                "team_model": 0,
            }
        rows = await self.db.query_raw(
            """
            WITH input AS (
                SELECT *
                FROM jsonb_to_recordset($1::jsonb) AS x(
                    id text,
                    request_id text,
                    call_type text,
                    api_key text,
                    user_id text,
                    team_id text,
                    organization_id text,
                    owner_account_id text,
                    end_user_id text,
                    model text,
                    deployment_model text,
                    provider text,
                    api_base text,
                    spend double precision,
                    provider_cost double precision,
                    spend_exact numeric,
                    provider_cost_exact numeric,
                    billing_unit text,
                    pricing_tier text,
                    total_tokens integer,
                    input_tokens integer,
                    output_tokens integer,
                    cached_input_tokens integer,
                    cached_output_tokens integer,
                    input_audio_tokens integer,
                    output_audio_tokens integer,
                    input_characters integer,
                    output_characters integer,
                    duration_seconds double precision,
                    image_count integer,
                    rerank_units integer,
                    start_time text,
                    end_time text,
                    latency_ms integer,
                    cache_hit boolean,
                    cache_key text,
                    request_tags text[],
                    unpriced_reason text,
                    pricing_fields_used jsonb,
                    usage_snapshot jsonb,
                    metadata jsonb,
                    status text,
                    http_status_code integer,
                    error_type text
                )
            )
            INSERT INTO deltallm_spendlog_events (
                id, request_id, call_type, api_key, user_id, team_id,
                organization_id, owner_account_id, end_user_id, model,
                deployment_model, provider, api_base, spend, provider_cost,
                spend_exact, provider_cost_exact,
                billing_unit, pricing_tier, total_tokens, input_tokens,
                output_tokens, cached_input_tokens, cached_output_tokens,
                input_audio_tokens, output_audio_tokens, input_characters,
                output_characters, duration_seconds, image_count, rerank_units,
                start_time, end_time, latency_ms, cache_hit, cache_key,
                request_tags, unpriced_reason, pricing_fields_used,
                usage_snapshot, metadata, status, http_status_code, error_type
            )
            SELECT
                id, request_id, call_type, api_key, user_id, team_id,
                organization_id, owner_account_id, end_user_id, model,
                deployment_model, provider, api_base, spend, provider_cost,
                spend_exact, provider_cost_exact,
                billing_unit, pricing_tier, total_tokens, input_tokens,
                output_tokens, cached_input_tokens, cached_output_tokens,
                input_audio_tokens, output_audio_tokens, input_characters,
                output_characters, duration_seconds, image_count, rerank_units,
                start_time::timestamp, end_time::timestamp, latency_ms,
                cache_hit, cache_key, request_tags, unpriced_reason,
                pricing_fields_used, usage_snapshot, metadata, status,
                http_status_code, error_type
            FROM input
            ORDER BY id
            ON CONFLICT (id) DO NOTHING
            RETURNING id
            """,
            json.dumps([event.row for event in prepared], default=str, allow_nan=False),
        )
        inserted_ids = sorted(str(row.get("id")) for row in rows if row.get("id"))
        event_entries = {event.event_id: event for event in prepared}
        api_keys: dict[str, Decimal] = {}
        users: dict[str, Decimal] = {}
        teams: dict[str, Decimal] = {}
        organizations: dict[str, Decimal] = {}
        team_models: dict[tuple[str, str], Decimal] = {}

        def _add(target: dict[Any, Decimal], key: Any, amount: Decimal) -> None:
            if key:
                target[key] = target.get(key, Decimal(0)) + amount

        for event_id in inserted_ids:
            prepared_event = event_entries[event_id]
            event_type = prepared_event.event_type
            event_entry = prepared_event.event_entry
            amount = canonical_money(prepared_event.row.get("spend_exact"))
            if event_type != "spend" or amount <= 0:
                continue
            _add(api_keys, event_entry.get("api_key"), amount)
            _add(users, event_entry.get("user_id"), amount)
            _add(teams, event_entry.get("team_id"), amount)
            _add(organizations, event_entry.get("organization_id"), amount)
            team_id = event_entry.get("team_id")
            model = event_entry.get("model")
            if team_id and model:
                _add(team_models, (str(team_id), str(model)), amount)

        counts = await self.ledger.increment_spend_batch(
            api_keys=api_keys,
            users=users,
            teams=teams,
            organizations=organizations,
            team_models=team_models,
        )
        return set(inserted_ids), counts

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
        owner_account_id: str | None,
        owner_snapshot_complete: bool,
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
            owner_account_id=owner_account_id,
            owner_snapshot_complete=owner_snapshot_complete,
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
            cost=money_string(cost),
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
                    error_type,
                    owner_account_id,
                    spend_exact,
                    provider_cost_exact
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29::timestamp,$30::timestamp,$31,$32,$33,$34,$35,$36::jsonb,$37::jsonb,$38::jsonb,$39,$40,$41,$42,$43::numeric,$44::numeric
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
                event_entry["owner_account_id"],
                money_string(event_entry.get("spend") or 0),
                money_string(event_entry["provider_cost"])
                if event_entry.get("provider_cost") is not None
                else None,
            )
            return bool(rows)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("failed to write normalized spend event: %s", exc)
            return False

    async def _write_event_once(
        self, event_entry: dict[str, Any], *, event_id: str
    ) -> SpendLogOnceResult:
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
                error_type,
                owner_account_id,
                spend_exact,
                provider_cost_exact
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29::timestamp,$30::timestamp,$31,$32,$33,$34,$35,$36::jsonb,$37::jsonb,$38::jsonb,$39,$40,$41,$42,$43::numeric,$44::numeric
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
            event_entry["owner_account_id"],
            money_string(event_entry.get("spend") or 0),
            money_string(event_entry["provider_cost"])
            if event_entry.get("provider_cost") is not None
            else None,
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
