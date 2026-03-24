from __future__ import annotations

import logging
import math
from typing import Any, Mapping

logger = logging.getLogger(__name__)


def normalize_router_usage(
    *,
    mode: str,
    usage: Mapping[str, Any] | None,
) -> dict[str, int]:
    data = dict(usage or {})
    counters: dict[str, int] = {"rpm": 1}

    normalized_mode = mode.strip().lower() or "chat"
    if normalized_mode in {"chat", "embedding"}:
        token_count = _token_units(data)
        if token_count > 0:
            counters["tpm"] = token_count
        return counters

    if normalized_mode == "image_generation":
        image_count = _to_int(data.get("images"))
        if image_count > 0:
            counters["image_pm"] = image_count
        return counters

    if normalized_mode in {"audio_speech", "audio_transcription"}:
        duration_seconds = _duration_units(data)
        if duration_seconds > 0:
            counters["audio_seconds_pm"] = duration_seconds
        character_count = _character_units(data)
        if character_count > 0:
            counters["char_pm"] = character_count
        return counters

    if normalized_mode == "rerank":
        rerank_units = _to_int(data.get("rerank_units"))
        if rerank_units > 0:
            counters["rerank_units_pm"] = rerank_units
        return counters

    token_count = _token_units(data)
    if token_count > 0:
        counters["tpm"] = token_count
        return counters

    character_count = _character_units(data)
    if character_count > 0:
        counters["char_pm"] = character_count
        return counters

    image_count = _to_int(data.get("images"))
    if image_count > 0:
        counters["image_pm"] = image_count

    return counters


async def record_router_usage(
    state_backend: Any,
    deployment_id: str,
    *,
    mode: str,
    usage: Mapping[str, Any] | None,
) -> bool:
    if not deployment_id:
        return False

    counters = normalize_router_usage(mode=mode, usage=usage)
    try:
        increment_usage_counters = getattr(state_backend, "increment_usage_counters", None)
        if callable(increment_usage_counters):
            await increment_usage_counters(deployment_id, counters)
        else:
            await state_backend.increment_usage(deployment_id, int(counters.get("tpm", 0)))
        return True
    except Exception as exc:
        logger.warning(
            "router usage recording failed for deployment=%s mode=%s: %s",
            deployment_id,
            mode,
            exc,
        )
        return False


def _token_units(data: Mapping[str, Any]) -> int:
    total_tokens = _to_int(data.get("total_tokens"))
    if total_tokens > 0:
        return total_tokens

    token_count = _to_int(data.get("prompt_tokens")) + _to_int(data.get("completion_tokens"))

    input_audio_tokens = _to_int(data.get("input_audio_tokens"))
    output_audio_tokens = _to_int(data.get("output_audio_tokens"))
    if input_audio_tokens > 0 or output_audio_tokens > 0:
        return token_count + input_audio_tokens + output_audio_tokens

    return token_count + _to_int(data.get("audio_tokens"))


def _duration_units(data: Mapping[str, Any]) -> int:
    duration_seconds = _to_float(data.get("billable_duration_seconds"))
    if duration_seconds <= 0:
        duration_seconds = _to_float(data.get("duration_seconds"))
    if duration_seconds <= 0:
        return 0
    return max(1, int(math.ceil(duration_seconds)))


def _character_units(data: Mapping[str, Any]) -> int:
    return sum(
        _to_int(data.get(key))
        for key in (
            "input_characters",
            "output_characters",
            "characters",
        )
    )


def _to_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float:
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return 0.0
