from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# A tool loop sends several model requests for one client request. The provider
# charges each one, so the reported usage is the sum over all hops.


def add_hop_usage(
    total: Mapping[str, Any] | None, usage: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    if not isinstance(usage, Mapping):
        return dict(total) if isinstance(total, Mapping) else None
    if not isinstance(total, Mapping):
        return _copy_usage(usage)
    return _sum_counts(total, usage)


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
