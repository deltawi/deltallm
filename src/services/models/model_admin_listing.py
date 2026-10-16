from __future__ import annotations

import math
from datetime import datetime
from functools import cmp_to_key
from typing import Literal

from src.services.reporting.admin_list_health import ListHealthSnapshot

ModelSortKey = Literal[
    "name", "mode", "provider", "cost", "health", "created_by", "updated_at", "visibility"
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


_TOKEN_PRICE = ("input_cost_per_token", "output_cost_per_token")
_CHARACTER_PRICE = ("input_cost_per_character", "output_cost_per_character")
_AUDIO_TOKEN_PRICE = ("input_cost_per_audio_token", "output_cost_per_audio_token")
_SECOND_PRICE = ("input_cost_per_second", "output_cost_per_second")
_IMAGE_PRICE = ("input_cost_per_image", "output_cost_per_image")
_REQUEST_PRICE = ("cost_per_request",)
# Same lead price per model type as the Models list Cost column (ui/src/lib/modelCost.ts).
_COST_PRICE_ORDER: dict[str, tuple[tuple[str, ...], ...]] = {
    "audio_speech": (
        _CHARACTER_PRICE,
        _AUDIO_TOKEN_PRICE,
        _SECOND_PRICE,
        _TOKEN_PRICE,
        _REQUEST_PRICE,
    ),
    "audio_transcription": (_SECOND_PRICE, _AUDIO_TOKEN_PRICE, _TOKEN_PRICE, _REQUEST_PRICE),
    "image_generation": (_IMAGE_PRICE, _TOKEN_PRICE, _REQUEST_PRICE),
}
_DEFAULT_COST_PRICE_ORDER = (
    _TOKEN_PRICE,
    _AUDIO_TOKEN_PRICE,
    _CHARACTER_PRICE,
    _SECOND_PRICE,
    _IMAGE_PRICE,
    _REQUEST_PRICE,
)
_NON_TOKEN_MODE_ORDER = ("audio_speech", "audio_transcription", "image_generation")


def _price(info: dict[object, object], field: str) -> float | None:
    raw = info.get(field)
    if raw is None or raw == "" or isinstance(raw, bool):
        return None
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _cost_sort_value(row: dict[str, object]) -> tuple[tuple[int, int], float] | None:
    """Rank token-priced models first by price, then other units grouped by type.

    Prices in different units (per token, per image, per second) are never
    compared with each other. Models without a standard price return None.
    """
    info = row.get("model_info")
    if not isinstance(info, dict):
        return None
    mode = str(row.get("mode") or "chat")
    for fields in _COST_PRICE_ORDER.get(mode, _DEFAULT_COST_PRICE_ORDER):
        prices = [price for price in (_price(info, field) for field in fields) if price is not None]
        if not prices:
            continue
        if fields == _TOKEN_PRICE:
            group = (0, 0)
        else:
            mode_rank = (
                _NON_TOKEN_MODE_ORDER.index(mode)
                if mode in _NON_TOKEN_MODE_ORDER
                else len(_NON_TOKEN_MODE_ORDER)
            )
            group = (1 + mode_rank, _DEFAULT_COST_PRICE_ORDER.index(fields))
        input_price = _price(info, fields[0])
        return group, input_price if input_price is not None else prices[0]
    return None


def _sort_value(row: dict[str, object], key: ModelSortKey) -> str | float | None:
    if key == "health":
        return float({"unhealthy": 0, "unknown": 2, "healthy": 5}[str(row["health_status"])])
    field = {
        "name": "display_name",
        "mode": "mode",
        "provider": "provider",
        "created_by": "created_by_email",
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
    if key == "cost":
        result = _compare_cost(_cost_sort_value(a), _cost_sort_value(b), direction)
        return result or (str(a["deployment_id"]) > str(b["deployment_id"])) - (
            str(a["deployment_id"]) < str(b["deployment_id"])
        )
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


def _compare_cost(
    left: tuple[tuple[int, int], float] | None,
    right: tuple[tuple[int, int], float] | None,
    direction: SortDirection,
) -> int:
    # Unpriced models stay last and price groups keep their order in both directions;
    # only the price inside a group follows the sort direction.
    if left is None or right is None:
        return (left is None) - (right is None)
    if left[0] != right[0]:
        return (left[0] > right[0]) - (left[0] < right[0])
    result = (left[1] > right[1]) - (left[1] < right[1])
    return -result if direction == "desc" else result
