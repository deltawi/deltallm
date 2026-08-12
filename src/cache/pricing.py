from __future__ import annotations

from typing import Any, Mapping

_SYNC_BASE_PRICING_KEYS = ("input_cost_per_token", "output_cost_per_token", "cost_per_request")
_CACHE_HIT_PRICING_KEYS = ("input_cost_per_token_cache_hit", "output_cost_per_token_cache_hit")


def cache_pricing_snapshot_from_deployment(deployment: Any) -> dict[str, Any]:
    snapshot = dict(getattr(deployment, "model_info", None) or {})
    _set_if_missing(snapshot, "input_cost_per_token", getattr(deployment, "input_cost_per_token", None))
    _set_if_missing(snapshot, "output_cost_per_token", getattr(deployment, "output_cost_per_token", None))
    return snapshot


def provider_cache_miss_usage(usage: Mapping[str, Any] | None) -> dict[str, Any]:
    provider_usage = dict(usage or {})
    provider_usage.pop("prompt_tokens_cached", None)
    return provider_usage


def has_cache_hit_only_pricing(pricing_snapshot: Mapping[str, Any] | None) -> bool:
    snapshot = dict(pricing_snapshot or {})
    return _has_value(snapshot, _CACHE_HIT_PRICING_KEYS) and not _has_value(snapshot, _SYNC_BASE_PRICING_KEYS)


def _set_if_missing(snapshot: dict[str, Any], key: str, value: Any) -> None:
    if snapshot.get(key) is None and value is not None:
        snapshot[key] = value


def _has_value(snapshot: Mapping[str, Any], keys: tuple[str, ...]) -> bool:
    return any(snapshot.get(key) not in (None, "") for key in keys)
