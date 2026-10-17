"""Coding-agent key fields: the recurring budget period and the agent tag."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status

from src.billing.budgets.budget import _parse_duration

CODING_AGENTS = ("claude-code", "codex", "opencode", "other")
_MODEL_NAME_MAX_LENGTH = 256


def normalize_key_budget_duration(value: Any) -> str | None:
    """Return a budget period the budget service can reset, or None to clear it."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="budget_duration must be a string"
        )
    normalized = value.strip()
    if not normalized:
        return None
    if _parse_duration(normalized) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="budget_duration must be a positive integer followed by h, d, or mo",
        )
    return normalized


def first_budget_reset_at(duration: str | None, *, now: datetime | None = None) -> datetime | None:
    """Return the end of the current UTC period, as a naive UTC timestamp.

    Periods align to UTC boundaries so a "1d" budget is a calendar-day budget:
    the first reset is the next UTC midnight, not 24 hours after creation.
    """
    if duration is None:
        return None
    parsed = _parse_duration(duration)
    if parsed is None:
        return None
    amount, unit = parsed
    current = (now or datetime.now(tz=UTC)).astimezone(UTC).replace(tzinfo=None)
    if unit == "h":
        return current.replace(minute=0, second=0, microsecond=0) + timedelta(hours=amount)
    if unit == "d":
        midnight = current.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight + timedelta(days=amount)
    zero_based_month = current.month - 1 + amount
    year = current.year + zero_based_month // 12
    month = zero_based_month % 12 + 1
    return datetime(year, month, 1)


def normalize_coding_agent(value: Any) -> dict[str, str | None] | None:
    """Validate the coding-agent tag that the setup pages store in key metadata."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="coding_agent must be an object"
        )
    unknown = set(value) - {"agent", "main_model", "small_model"}
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"coding_agent has unknown fields: {', '.join(sorted(unknown))}",
        )
    agent = value.get("agent")
    if agent not in CODING_AGENTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"coding_agent.agent must be one of: {', '.join(CODING_AGENTS)}",
        )
    return {
        "agent": agent,
        "main_model": _optional_model_name(value.get("main_model"), "main_model"),
        "small_model": _optional_model_name(value.get("small_model"), "small_model"),
    }


def coding_agent_metadata_json(coding_agent: dict[str, str | None] | None) -> str | None:
    if coding_agent is None:
        return None
    return json.dumps({"coding_agent": coding_agent})


def coding_agent_json(coding_agent: dict[str, str | None] | None) -> str | None:
    if coding_agent is None:
        return None
    return json.dumps(coding_agent)


def _optional_model_name(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"coding_agent.{field_name} must be a string",
        )
    normalized = value.strip()
    if len(normalized) > _MODEL_NAME_MAX_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"coding_agent.{field_name} is too long",
        )
    return normalized or None
