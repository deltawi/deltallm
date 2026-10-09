from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.billing.money import canonical_money, money_string
from src.billing.spend_events import build_spend_event

# Compatibility boundary for historical outbox payloads. New domain contracts
# supply exact string amounts; only this mapper accepts the legacy dynamic shape.
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


def prepare_spend_event(
    *, event_id: str, event_type: str, payload: dict[str, Any]
) -> PreparedSpendEvent:
    """Validate an accepted outbox event without I/O or current-price lookups."""

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
    provider_cost = payload.get("provider_cost_exact", event_entry.get("provider_cost"))
    row["provider_cost_exact"] = money_string(provider_cost) if provider_cost is not None else None
    if provider_cost is not None:
        # The legacy float is a reporting mirror, never the exact amount source.
        row["provider_cost"] = float(canonical_money(provider_cost))
        event_entry["provider_cost"] = row["provider_cost"]
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
