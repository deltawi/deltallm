from __future__ import annotations

from typing import Any


def apply_default_params(
    upstream_payload: dict[str, Any],
    model_info: dict[str, Any],
) -> dict[str, Any]:
    defaults = model_info.get("default_params")
    if not isinstance(defaults, dict) or not defaults:
        return upstream_payload
    for key, value in defaults.items():
        if key not in upstream_payload:
            upstream_payload[key] = value
    return upstream_payload
