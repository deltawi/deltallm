"""Write-merge rules for optional route policy blocks."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

ALLOWED_CONTEXT_KEYS = {
    "mode",
    "unknown_capacity",
    "default_output_tokens",
    "safety_margin_tokens",
}


def merge_context_policy_block(
    existing: object,
    replacement: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Preserve omitted context, while treating explicit null as a deletion tombstone."""

    current = existing if isinstance(existing, dict) else None
    if "context" not in replacement:
        return deepcopy(current) if current is not None else None
    replacement_context = replacement.get("context")
    if replacement_context is None:
        return None
    if not isinstance(replacement_context, dict):
        raise ValueError("context must be an object or null")
    merged = {
        key: deepcopy(value)
        for key, value in (current or {}).items()
        if key not in ALLOWED_CONTEXT_KEYS
    }
    merged.update(deepcopy(replacement_context))
    return merged


def merge_selector_policy_block(
    existing: object,
    replacement: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Preserve a v3 selector when omitted and remove it only through explicit null."""

    return merge_optional_policy_block(existing, replacement, "selector")


def merge_optional_policy_block(
    existing: object, replacement: Mapping[str, Any], key: str
) -> dict[str, Any] | None:
    """Keep an omitted block, replace a given one, and remove it through explicit null."""

    if key not in replacement:
        return deepcopy(existing) if isinstance(existing, dict) else None
    value = replacement.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object or null")
    return deepcopy(value)
