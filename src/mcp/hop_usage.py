from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.billing.pricing.pricing import with_reported_prompt_cache

# A tool loop sends several model requests for one client request. The provider
# charges each one, so the reported usage is the sum over all hops.


def add_hop_usage(
    total: Mapping[str, Any] | None, usage: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    if not isinstance(usage, Mapping):
        return dict(total) if isinstance(total, Mapping) else None
    usage = _with_valid_cache_details(usage)
    if not isinstance(total, Mapping):
        return _copy_usage(usage)
    return _sum_counts(total, usage)


_CACHE_DETAILS = (
    ("cached_tokens", "prompt_tokens_cached"),
    ("cache_write_tokens", "prompt_tokens_cache_write"),
)


def _with_valid_cache_details(usage: Mapping[str, Any]) -> Mapping[str, Any]:
    # Billing checks the cache counts against prompt_tokens. Check each hop before the
    # sum, so a bad count in one hop cannot pass beside the prompt tokens of another hop.
    details = usage.get("prompt_tokens_details")
    if not isinstance(details, Mapping):
        return usage
    valid = with_reported_prompt_cache(
        {"prompt_tokens": usage.get("prompt_tokens"), "prompt_tokens_details": details}
    )
    fixed = {
        key: valid.get(count, 0)
        for key, count in _CACHE_DETAILS
        if key in details and details[key] is not None and details[key] != valid.get(count, 0)
    }
    if not fixed:
        return usage
    return {**usage, "prompt_tokens_details": {**details, **fixed}}


def _copy_usage(usage: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: _copy_usage(value) if isinstance(value, Mapping) else value
        for key, value in usage.items()
    }


def _sum_counts(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    merged = _copy_usage(left)
    for key, value in right.items():
        current = merged.get(key)
        if isinstance(value, Mapping):
            merged[key] = (
                _sum_counts(current, value) if isinstance(current, Mapping) else _copy_usage(value)
            )
        elif value is None:
            # A hop without this field must not erase the earlier hops' values.
            continue
        elif _is_count(value) and (current is None or _is_count(current)):
            merged[key] = (current or 0) + value
        else:
            # Non-count fields (for example a provider tier name) keep the latest value.
            merged[key] = value
    return merged


def _is_count(value: Any) -> bool:
    # Token counts are ints; some providers also report a float cost.
    return isinstance(value, (int, float)) and not isinstance(value, bool)
