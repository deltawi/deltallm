from __future__ import annotations

import json
from collections.abc import Mapping

from src.providers.base import invalid_provider_response_error

_MAX_CANONICAL_FIRST_FRAME_CHARS = 1_048_576


def validate_first_downstream_stream_frame(line: str) -> None:
    """Require a bounded canonical response frame before failover is committed."""

    if len(line) > _MAX_CANONICAL_FIRST_FRAME_CHARS or not line.startswith("data:"):
        raise invalid_provider_response_error()
    raw_payload = line[len("data:") :].strip()
    if not raw_payload or raw_payload == "[DONE]":
        raise invalid_provider_response_error()
    try:
        payload = json.loads(raw_payload)
    except (RecursionError, TypeError, ValueError) as exc:
        raise invalid_provider_response_error() from exc
    if not isinstance(payload, Mapping) or not isinstance(payload.get("choices"), list):
        raise invalid_provider_response_error()
