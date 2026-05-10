from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

from src.batch.endpoints import BATCH_ENDPOINT_CHAT_COMPLETIONS, BATCH_ENDPOINT_EMBEDDINGS

ESTIMATOR_VERSION = "v1"


def _ceil_units(value: int, divisor: int) -> int:
    return max(1, int(math.ceil(max(0, int(value)) / float(divisor))))


def _json_char_count(value: Any) -> int:
    try:
        return len(json.dumps(value, separators=(",", ":"), ensure_ascii=True, default=str))
    except (TypeError, ValueError):
        return len(str(value or ""))


def _estimate_embedding_input(value: Any) -> int:
    if isinstance(value, str):
        return _ceil_units(len(value), 256)
    if isinstance(value, list):
        if not value:
            return 1
        if all(isinstance(item, int) for item in value):
            return _ceil_units(len(value), 256)
        return max(1, sum(_estimate_embedding_input(item) for item in value))
    return 1


def _estimate_chat_request(request_body: Mapping[str, Any]) -> int:
    prompt_units = _ceil_units(_json_char_count(request_body.get("messages") or []), 512)
    max_tokens = request_body.get("max_tokens")
    completion_units = 1
    if isinstance(max_tokens, int):
        completion_units = _ceil_units(max_tokens, 256)
    return max(1, prompt_units + completion_units)


def estimate_request_work_units(endpoint: str | None, request_body: Mapping[str, Any] | None) -> int:
    body = request_body if isinstance(request_body, Mapping) else {}
    normalized_endpoint = str(endpoint or "").strip()
    if normalized_endpoint == BATCH_ENDPOINT_CHAT_COMPLETIONS or "messages" in body:
        return _estimate_chat_request(body)
    if normalized_endpoint == BATCH_ENDPOINT_EMBEDDINGS or "input" in body:
        return _estimate_embedding_input(body.get("input"))
    return 1


def size_class_for_work_units(work_units: int | None) -> str:
    bounded = max(0, int(work_units or 0))
    if bounded <= 10:
        return "xs"
    if bounded <= 100:
        return "s"
    if bounded <= 1_000:
        return "m"
    if bounded <= 10_000:
        return "l"
    return "xl"
