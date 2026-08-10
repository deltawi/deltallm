from __future__ import annotations

from datetime import UTC, datetime, timedelta
import math
import time
from typing import Any

from src.models.errors import ServiceUnavailableError
from src.services.limit_counter import (
    fair_share_active_key,
    fair_share_boost_key,
    fair_share_denial_key,
    fair_share_weight_key,
)

_WINDOW_SECONDS = 60


class TierCapacityRuntimeService:
    def __init__(self, *, redis_client: Any | None, tier_policy_service: Any | None) -> None:
        self.redis = redis_client
        self.tier_policy_service = tier_policy_service

    async def set_temporary_boost(
        self,
        *,
        pool_key: str,
        callable_key: str,
        organization_id: str,
        multiplier: float,
        expires_in_seconds: int,
    ) -> dict[str, Any]:
        policy = self._require_pool_policy(pool_key, callable_key)
        if str(getattr(policy, "strategy", "hard_cap")) not in {
            "weighted_fair",
            "reserved_burst",
        }:
            raise ValueError("Temporary boosts require a weighted fair-share capacity pool")
        redis = self._require_redis()
        normalized_multiplier = float(multiplier)
        if normalized_multiplier < 1 or normalized_multiplier > 100:
            raise ValueError("multiplier must be between 1 and 100")
        normalized_ttl = int(expires_in_seconds)
        if normalized_ttl < 1 or normalized_ttl > 604_800:
            raise ValueError("expires_in_seconds must be between 1 and 604800")
        entity_id = _entity_id(pool_key, callable_key)
        key = fair_share_boost_key(entity_id, organization_id)
        await redis.set(key, str(normalized_multiplier), ex=normalized_ttl)
        expires_at = datetime.now(tz=UTC) + timedelta(seconds=normalized_ttl)
        return {
            "pool_key": pool_key,
            "callable_key": callable_key,
            "organization_id": organization_id,
            "multiplier": normalized_multiplier,
            "expires_in_seconds": normalized_ttl,
            "expires_at": expires_at.isoformat(),
        }

    async def clear_temporary_boost(
        self,
        *,
        pool_key: str,
        callable_key: str,
        organization_id: str,
    ) -> dict[str, Any]:
        self._require_pool_policy(pool_key, callable_key)
        redis = self._require_redis()
        entity_id = _entity_id(pool_key, callable_key)
        deleted = int(await redis.delete(fair_share_boost_key(entity_id, organization_id)))
        return {
            "pool_key": pool_key,
            "callable_key": callable_key,
            "organization_id": organization_id,
            "deleted": deleted > 0,
        }

    async def dashboard(self, *, top_org_limit: int = 20) -> dict[str, Any]:
        redis = self._require_redis()
        snapshot = self._snapshot()
        window_id = math.floor(time.time() / _WINDOW_SECONDS)
        now_ms = int(time.time() * 1000)
        pools: list[dict[str, Any]] = []
        heatmap: list[dict[str, Any]] = []
        policies = sorted(
            getattr(snapshot, "capacity_pool_policy", {}).values(),
            key=lambda item: (item.pool_key, item.callable_key),
        )
        for policy in policies:
            entity_id = _entity_id(policy.pool_key, policy.callable_key)
            active_raw = await redis.zrangebyscore(
                fair_share_active_key(entity_id),
                now_ms,
                "+inf",
            )
            active_orgs = [_text(value) for value in active_raw]
            weight_map = _mapping(await redis.hgetall(fair_share_weight_key(entity_id)))
            rpm_used = await _counter_value(
                redis,
                f"ratelimit:tier_pool_model_rpm:{entity_id}:{window_id}",
            )
            tpm_used = await _counter_value(
                redis,
                f"ratelimit:tier_pool_model_tpm:{entity_id}:{window_id}",
            )
            org_rows: list[dict[str, Any]] = []
            for organization_id in active_orgs:
                boost_key = fair_share_boost_key(entity_id, organization_id)
                boost_raw = await redis.get(boost_key)
                boost_ttl = int(await redis.ttl(boost_key)) if boost_raw is not None else -1
                org_rpm = await _counter_value(
                    redis,
                    (
                        "ratelimit:tier_pool_model_rpm_org:"
                        f"{entity_id}:{organization_id}:{window_id}"
                    ),
                )
                org_tpm = await _counter_value(
                    redis,
                    (
                        "ratelimit:tier_pool_model_tpm_org:"
                        f"{entity_id}:{organization_id}:{window_id}"
                    ),
                )
                org_rows.append(
                    {
                        "organization_id": organization_id,
                        "effective_weight": _float_value(weight_map.get(organization_id), 1.0),
                        "rpm_used": org_rpm,
                        "tpm_used": org_tpm,
                        "boost_multiplier": _float_value(boost_raw, 1.0),
                        "boost_ttl_seconds": max(0, boost_ttl),
                    }
                )

            for scope in ("tier_pool_model_rpm", "tier_pool_model_tpm"):
                denied = _mapping(
                    await redis.hgetall(fair_share_denial_key(scope, entity_id, window_id))
                )
                for organization_id, count in denied.items():
                    heatmap.append(
                        {
                            "pool_key": policy.pool_key,
                            "callable_key": policy.callable_key,
                            "scope": scope,
                            "organization_id": organization_id,
                            "limit_hits": int(_float_value(count, 0)),
                        }
                    )

            org_rows.sort(
                key=lambda item: (item["rpm_used"] + item["tpm_used"], item["organization_id"]),
                reverse=True,
            )
            pools.append(
                {
                    "pool_key": policy.pool_key,
                    "callable_key": policy.callable_key,
                    "strategy": policy.strategy,
                    "rpm_capacity": policy.rpm_capacity,
                    "tpm_capacity": policy.tpm_capacity,
                    "max_parallel_requests": policy.max_parallel_requests,
                    "saturation_threshold": policy.saturation_threshold,
                    "burst_multiplier": policy.burst_multiplier,
                    "active_organization_count": len(active_orgs),
                    "rpm_used": rpm_used,
                    "tpm_used": tpm_used,
                    "rpm_saturation": _ratio(rpm_used, policy.rpm_capacity),
                    "tpm_saturation": _ratio(tpm_used, policy.tpm_capacity),
                    "top_organizations": org_rows[:top_org_limit],
                }
            )

        heatmap.sort(
            key=lambda item: (item["limit_hits"], item["pool_key"], item["organization_id"]),
            reverse=True,
        )
        return {
            "snapshot_etag": getattr(snapshot, "etag", None),
            "window_seconds": _WINDOW_SECONDS,
            "window_id": window_id,
            "pools": pools,
            "limit_hit_heatmap": heatmap,
        }

    def _snapshot(self) -> Any:
        getter = getattr(self.tier_policy_service, "get_snapshot", None)
        if not callable(getter):
            raise ServiceUnavailableError(message="Tier policy snapshot unavailable")
        return getter()

    def _require_pool_policy(self, pool_key: str, callable_key: str) -> Any:
        getter = getattr(self.tier_policy_service, "get_capacity_pool_policy", None)
        policy = getter(pool_key, callable_key) if callable(getter) else None
        if policy is None:
            raise ValueError("Capacity pool policy was not found in the active tier snapshot")
        return policy

    def _require_redis(self) -> Any:
        if self.redis is None:
            raise ServiceUnavailableError(message="Tier capacity Redis backend unavailable")
        return self.redis


def _entity_id(pool_key: str, callable_key: str) -> str:
    return f"{str(pool_key).strip()}:{str(callable_key).strip()}"


async def _counter_value(redis: Any, key: str) -> int:
    return int(_float_value(await redis.get(key), 0))


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {_text(key): nested for key, nested in value.items()}


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _float_value(value: object, default: float) -> float:
    try:
        return float(_text(value))
    except (TypeError, ValueError):
        return default


def _ratio(value: int, limit: int | None) -> float | None:
    if limit is None or limit <= 0:
        return None
    return min(1.0, max(0.0, value / limit))
