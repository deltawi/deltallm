from __future__ import annotations

from datetime import datetime
from functools import cmp_to_key
from typing import Literal

from src.services.admin_list_health import ListHealthSnapshot

ModelSortKey = Literal[
    "name", "mode", "provider", "health", "created_by", "updated_at", "visibility"
]
SortDirection = Literal["asc", "desc"]


def model_list_page(
    entries: list[dict[str, object]],
    *,
    health: ListHealthSnapshot,
    search: str | None,
    provider: str | None,
    mode: str | None,
    sort_by: ModelSortKey | None,
    sort_direction: SortDirection,
    limit: int,
    offset: int,
) -> dict[str, object]:
    healthy, unknown = set(health.healthy_ids), set(health.unknown_ids)
    selected: list[dict[str, object]] = []
    for source in entries:
        row = dict(source)
        key = str(row["deployment_id"])
        row["health_status"] = (
            "unhealthy"
            if row.get("routable") is False
            else "unknown"
            if key in unknown
            else "healthy"
            if key in healthy
            else "unhealthy"
        )
        row["healthy"] = (
            None if row["health_status"] == "unknown" else row["health_status"] == "healthy"
        )
        access = row.get("access")
        row["visibility"] = (
            access.get("visibility", "platform") if isinstance(access, dict) else "platform"
        )
        text = " ".join(
            str(row.get(field) or "")
            for field in ["model_name", "display_name", "deployment_id", "provider"]
        )
        if search and search.casefold() not in text.casefold():
            continue
        if provider and str(row.get("provider", "")).casefold() != provider.casefold():
            continue
        if mode and str(row.get("mode") or "chat").casefold() != mode.casefold():
            continue
        selected.append(row)
    if sort_by:
        selected.sort(key=cmp_to_key(lambda a, b: _compare_models(a, b, sort_by, sort_direction)))
    total = len(selected)
    return {
        "data": selected[offset : offset + limit],
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
        },
    }


def _sort_value(row: dict[str, object], key: ModelSortKey) -> str | float | None:
    if key == "health":
        return float({"unhealthy": 0, "unknown": 2, "healthy": 5}[str(row["health_status"])])
    field = {
        "name": "display_name",
        "mode": "mode",
        "provider": "provider",
        "created_by": "created_by_user_id",
        "updated_at": "updated_at",
        "visibility": "visibility",
    }[key]
    value = row.get(field) or (row.get("model_name") if key == "name" else None)
    if value is None:
        return None
    if key == "updated_at":
        return (
            value.timestamp()
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        )
    return str(value).casefold()


def _compare_models(
    a: dict[str, object], b: dict[str, object], key: ModelSortKey, direction: SortDirection
) -> int:
    left, right = _sort_value(a, key), _sort_value(b, key)
    if left is None or right is None:
        result = (left is None) - (right is None)
    else:
        result = (left > right) - (left < right)
        if direction == "desc":
            result = -result
    return result or (str(a["deployment_id"]) > str(b["deployment_id"])) - (
        str(a["deployment_id"]) < str(b["deployment_id"])
    )
