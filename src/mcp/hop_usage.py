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
    total, usage = _with_same_cache_count_form(total, usage)
    return _sum_counts(total, usage)


_CACHE_DETAILS = (
    ("cached_tokens", "prompt_tokens_cached"),
    ("cache_write_tokens", "prompt_tokens_cache_write"),
)


def _with_valid_cache_details(usage: Mapping[str, Any]) -> Mapping[str, Any]:
    # Billing checks the cache counts against prompt_tokens. Check each hop before the
    # sum, so a bad count in one hop cannot pass beside the prompt tokens of another hop.
    details = usage.get("prompt_tokens_details")
    if details is None:
        return usage
    if not isinstance(details, Mapping):
        # A list, string or boolean here must not replace the valid details of other hops.
        return {key: value for key, value in usage.items() if key != "prompt_tokens_details"}
    valid = with_reported_prompt_cache(
        {"prompt_tokens": usage.get("prompt_tokens"), "prompt_tokens_details": details}
    )
    fixed = {
        key: valid.get(count, 0)
        for key, count in _CACHE_DETAILS
        if key in details
        and details[key] is not None
        and not _is_same_int(details[key], valid.get(count, 0))
    }
    if not fixed:
        return usage
    return {**usage, "prompt_tokens_details": {**details, **fixed}}


def _is_same_int(value: Any, expected: int) -> bool:
    # 0.0 and False are equal to 0, but billing does not accept them as counts.
    return type(value) is int and value == expected


def _with_same_cache_count_form(
    total: Mapping[str, Any], usage: Mapping[str, Any]
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    # Billing reads an explicit count (prompt_tokens_cached) before the details count.
    # After a failover, one hop can report the explicit count and another hop only the
    # details count. Give both sides the explicit count, so the sum includes every hop.
    for _, count in _CACHE_DETAILS:
        if (total.get(count) is None) != (usage.get(count) is None):
            total = _with_explicit_count(total, count)
            usage = _with_explicit_count(usage, count)
    return total, usage


def _with_explicit_count(usage: Mapping[str, Any], count: str) -> Mapping[str, Any]:
    if usage.get(count) is not None:
        return usage
    return {**usage, count: with_reported_prompt_cache(usage).get(count, 0)}


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
