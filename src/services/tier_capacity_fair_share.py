from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

from src.services.tier_policy_models import CompiledTierCapacityPoolPolicy, TierPolicySnapshot

ADVANCED_CAPACITY_POOL_STRATEGIES = frozenset({"weighted_fair", "reserved_burst"})
DEFAULT_SATURATION_THRESHOLD = 0.85
DEFAULT_ACTIVE_TTL_SECONDS = 10
FAIR_SHARE_WINDOW_SECONDS = 60
BOOST_INDEX_TTL_SECONDS = 604800
FAIR_SHARE_WEIGHT_SCALE = 1000
FAIR_SHARE_ACTIVE_CLEANUP_LIMIT = 64
DASHBOARD_MAX_POOL_LIMIT = 500
DASHBOARD_MAX_POOL_SCAN_LIMIT = 5000
DASHBOARD_MAX_TOP_ORG_LIMIT = 50
DASHBOARD_HEATMAP_LIMIT = 100
DASHBOARD_MAX_ACTIVE_BOOSTS_PER_POOL = 25

_PoolRef = tuple[str, str]


@dataclass(frozen=True, slots=True)
class TierFairShareCheck:
    pool_key: str
    callable_key: str
    organization_id: str
    tier_key: str | None
    assignment_weight: int
    rpm_capacity: int | None
    tpm_capacity: int | None
    request_amount: int
    token_amount: int
    strategy: str
    saturation_threshold: float | None
    burst_multiplier: float | None


@dataclass(frozen=True, slots=True)
class TierFairShareDecision:
    allowed: bool
    pool_key: str
    callable_key: str
    organization_id: str
    tier_key: str | None
    scope: str
    reason: str
    dimension: str
    active_org_count: int
    total_weight: float
    effective_weight: float
    pool_current: int
    org_current: int
    pool_limit: int
    share_limit: int
    saturation: float


@dataclass(frozen=True, slots=True)
class _PoolDashboardCandidate:
    pool: CompiledTierCapacityPoolPolicy
    advanced: bool
    member_count: int
    rpm_used: int
    tpm_used: int
    rpm_saturation: float | None
    tpm_saturation: float | None
    heatmap_hits: int

    @property
    def ref(self) -> _PoolRef:
        return (self.pool.pool_key, self.pool.callable_key)


def is_advanced_capacity_pool_strategy(strategy: object) -> bool:
    return str(strategy or "").strip().lower() in ADVANCED_CAPACITY_POOL_STRATEGIES


def normalized_saturation_threshold(value: object) -> float:
    try:
        threshold = float(value)
    except (TypeError, ValueError):
        return DEFAULT_SATURATION_THRESHOLD
    if threshold <= 0 or threshold > 1:
        return DEFAULT_SATURATION_THRESHOLD
    return threshold


def normalized_burst_multiplier(value: object) -> float:
    try:
        multiplier = float(value)
    except (TypeError, ValueError):
        return 1.0
    return multiplier if multiplier >= 1 else 1.0


def fair_share_active_key(pool_key: str, callable_key: str) -> str:
    return f"tier_fair_share:active:{pool_key}:{callable_key}"


def fair_share_weight_key(pool_key: str, callable_key: str) -> str:
    return f"tier_fair_share:weights:{pool_key}:{callable_key}"


def fair_share_active_count_key(pool_key: str, callable_key: str) -> str:
    return f"tier_fair_share:active_count:{pool_key}:{callable_key}"


def fair_share_total_weight_key(pool_key: str, callable_key: str) -> str:
    return f"tier_fair_share:total_weight:{pool_key}:{callable_key}"


def fair_share_usage_rank_key(*, pool_key: str, callable_key: str, window_id: int | None = None) -> str:
    return f"tier_fair_share:usage:{pool_key}:{callable_key}:{window_id if window_id is not None else _window_id()}"


def fair_share_cleanup_lag_key(pool_key: str, callable_key: str) -> str:
    return f"tier_fair_share:cleanup_lag:{pool_key}:{callable_key}"


def fair_share_pool_counter_key(
    *,
    dimension: str,
    pool_key: str,
    callable_key: str,
    window_id: int | None = None,
) -> str:
    return (
        f"tier_fair_share:{dimension}:pool:{pool_key}:{callable_key}:"
        f"{window_id if window_id is not None else _window_id()}"
    )


def fair_share_org_counter_key(
    *,
    dimension: str,
    pool_key: str,
    callable_key: str,
    organization_id: str,
    window_id: int | None = None,
) -> str:
    return (
        f"tier_fair_share:{dimension}:org:{pool_key}:{callable_key}:{organization_id}:"
        f"{window_id if window_id is not None else _window_id()}"
    )


def fair_share_boost_key(*, pool_key: str, callable_key: str, organization_id: str) -> str:
    return f"tier_fair_share:boost:{pool_key}:{callable_key}:{organization_id}"


def fair_share_boost_index_key(*, pool_key: str, callable_key: str) -> str:
    return f"tier_fair_share:boosts:{pool_key}:{callable_key}"


def fair_share_boost_metadata_key(*, pool_key: str, callable_key: str) -> str:
    return f"tier_fair_share:boost_meta:{pool_key}:{callable_key}"


def fair_share_limit_hit_heatmap_key(window_id: int | None = None) -> str:
    return f"tier_fair_share:limit_hits:{window_id if window_id is not None else _window_id()}"


def fair_share_limit_hit_heatmap_rank_key(window_id: int | None = None) -> str:
    return f"tier_fair_share:limit_hit_rank:{window_id if window_id is not None else _window_id()}"


def fair_share_limit_hit_total_key(window_id: int | None = None) -> str:
    return f"tier_fair_share:limit_hit_total:{window_id if window_id is not None else _window_id()}"


def static_pool_counter_key(
    *,
    scope: str,
    pool_key: str,
    callable_key: str,
    window_seconds: int = FAIR_SHARE_WINDOW_SECONDS,
    window_id: int | None = None,
) -> str:
    resolved_window_id = window_id if window_id is not None else _window_id(window_seconds)
    return f"ratelimit:{scope}:{pool_key}:{callable_key}:{resolved_window_id}"


async def build_tier_capacity_dashboard(
    *,
    tier_policy_service: Any,
    redis_client: Any | None,
    now: datetime | None = None,
    top_org_limit: int = 10,
    pool_limit: int = 100,
) -> dict[str, Any]:
    snapshot = _require_snapshot(tier_policy_service)
    generated_at = now or datetime.now(tz=UTC)
    timestamp = generated_at.timestamp()
    window_id = _window_id(FAIR_SHARE_WINDOW_SECONDS, timestamp=timestamp)
    now_ms = int(timestamp * 1000)
    normalized_top_org_limit = min(DASHBOARD_MAX_TOP_ORG_LIMIT, max(1, int(top_org_limit)))
    normalized_pool_limit = min(DASHBOARD_MAX_POOL_LIMIT, max(1, int(pool_limit)))
    heatmap = await _read_limit_hit_heatmap(
        redis_client,
        window_id=window_id,
        limit=DASHBOARD_HEATMAP_LIMIT,
    )
    heatmap_hits = _pool_heatmap_hit_map(heatmap)

    pools = sorted(snapshot.capacity_pool_policy.values(), key=lambda item: (item.pool_key, item.callable_key))
    scanned_pools = pools[:DASHBOARD_MAX_POOL_SCAN_LIMIT]
    usage_pairs = await _pool_usage_pairs(
        redis_client=redis_client,
        pools=scanned_pools,
        window_id=window_id,
    )
    candidates = _pool_dashboard_candidates(
        snapshot=snapshot,
        pools=scanned_pools,
        usage_pairs=usage_pairs,
        heatmap_hits=heatmap_hits,
    )
    candidates.sort(key=_candidate_sort_key)
    visible_candidates = candidates[:normalized_pool_limit]
    top_orgs_by_pool, active_counts_by_pool = await _top_orgs_for_pools(
        redis_client=redis_client,
        candidates=visible_candidates,
        window_id=window_id,
        limit=normalized_top_org_limit,
    )
    boosts_by_pool, boost_counts_by_pool = await _active_boosts_for_pools(
        redis_client=redis_client,
        candidates=visible_candidates,
        now_ms=now_ms,
    )
    cleanup_lagged_by_pool = await _cleanup_lagged_for_pools(
        redis_client=redis_client,
        candidates=visible_candidates,
    )
    visible_pool_summaries = [
        _pool_summary(
            candidate,
            active_org_count=active_counts_by_pool.get(candidate.ref, 0),
            top_orgs=top_orgs_by_pool.get(candidate.ref, []),
            active_boosts=boosts_by_pool.get(candidate.ref, []),
            active_boost_count=boost_counts_by_pool.get(candidate.ref, 0),
            cleanup_lagged=cleanup_lagged_by_pool.get(candidate.ref, False),
        )
        for candidate in visible_candidates
    ]

    return {
        "snapshot": _snapshot_info(tier_policy_service, snapshot),
        "window_seconds": FAIR_SHARE_WINDOW_SECONDS,
        "window_id": window_id,
        "generated_at": generated_at.isoformat(),
        "pools": visible_pool_summaries,
        "total_pool_count": len(pools),
        "scanned_pool_count": len(scanned_pools),
        "pool_scan_limit": DASHBOARD_MAX_POOL_SCAN_LIMIT,
        "pool_scan_truncated": len(pools) > len(scanned_pools),
        "advanced_pool_count": sum(1 for pool in pools if is_advanced_capacity_pool_strategy(pool.strategy)),
        "saturated_pool_count": sum(1 for candidate in candidates if _candidate_is_saturated(candidate)),
        "pool_limit": normalized_pool_limit,
        "truncated": len(candidates) > len(visible_pool_summaries),
        "limit_hit_count": await _read_limit_hit_total(
            redis_client,
            window_id=window_id,
            fallback_heatmap=heatmap,
        ),
        "limit_hit_heatmap": heatmap,
    }


async def upsert_temporary_capacity_boost(
    *,
    redis_client: Any,
    pool_key: str,
    callable_key: str,
    organization_id: str,
    weight_multiplier: float,
    ttl_seconds: int,
    reason: str | None,
) -> dict[str, Any]:
    normalized_ttl = max(1, int(ttl_seconds))
    multiplier = normalized_burst_multiplier(weight_multiplier)
    expires_at_ms = int(time.time() * 1000) + (normalized_ttl * 1000)
    key = fair_share_boost_key(
        pool_key=pool_key,
        callable_key=callable_key,
        organization_id=organization_id,
    )
    index_key = fair_share_boost_index_key(pool_key=pool_key, callable_key=callable_key)
    metadata_key = fair_share_boost_metadata_key(pool_key=pool_key, callable_key=callable_key)
    metadata = {
        "weight_multiplier": str(multiplier),
        "reason": str(reason or ""),
        "expires_at_ms": str(expires_at_ms),
    }
    pipe = redis_client.pipeline()
    pipe.set(key, str(multiplier), ex=normalized_ttl)
    pipe.zadd(index_key, {organization_id: expires_at_ms})
    pipe.hset(metadata_key, mapping={organization_id: _encode_metadata(metadata)})
    pipe.expire(index_key, BOOST_INDEX_TTL_SECONDS)
    pipe.expire(metadata_key, BOOST_INDEX_TTL_SECONDS)
    await pipe.execute()
    return {
        "pool_key": pool_key,
        "callable_key": callable_key,
        "organization_id": organization_id,
        "weight_multiplier": multiplier,
        "ttl_seconds": normalized_ttl,
        "expires_at": datetime.fromtimestamp(expires_at_ms / 1000, tz=UTC).isoformat(),
        "reason": reason,
    }


async def delete_temporary_capacity_boost(
    *,
    redis_client: Any,
    pool_key: str,
    callable_key: str,
    organization_id: str,
) -> dict[str, Any]:
    key = fair_share_boost_key(
        pool_key=pool_key,
        callable_key=callable_key,
        organization_id=organization_id,
    )
    index_key = fair_share_boost_index_key(pool_key=pool_key, callable_key=callable_key)
    metadata_key = fair_share_boost_metadata_key(pool_key=pool_key, callable_key=callable_key)
    pipe = redis_client.pipeline()
    pipe.delete(key)
    pipe.zrem(index_key, organization_id)
    pipe.hdel(metadata_key, organization_id)
    await pipe.execute()
    return {
        "deleted": True,
        "pool_key": pool_key,
        "callable_key": callable_key,
        "organization_id": organization_id,
    }


def _window_id(window_seconds: int = FAIR_SHARE_WINDOW_SECONDS, *, timestamp: float | None = None) -> int:
    return math.floor((timestamp if timestamp is not None else time.time()) / window_seconds)


def _ratio(value: int, limit: int | None) -> float | None:
    if limit is None or limit <= 0:
        return None
    return max(0.0, float(value) / float(limit))


def _require_snapshot(tier_policy_service: Any) -> TierPolicySnapshot:
    if tier_policy_service is None or not callable(getattr(tier_policy_service, "get_snapshot", None)):
        raise RuntimeError("Tier policy service unavailable")
    snapshot = tier_policy_service.get_snapshot()
    if not isinstance(snapshot, TierPolicySnapshot):
        raise RuntimeError("Tier policy snapshot unavailable")
    return snapshot


def _snapshot_info(tier_policy_service: Any, snapshot: TierPolicySnapshot) -> dict[str, Any]:
    info_getter = getattr(tier_policy_service, "snapshot_info", None)
    info = info_getter() if callable(info_getter) else None
    if info is not None:
        return _json_value(info)
    return {
        "etag": snapshot.etag,
        "generated_at": snapshot.generated_at.isoformat(),
        "org_count": snapshot.org_count,
        "assignment_count": snapshot.assignment_count,
        "model_policy_count": snapshot.model_policy_count,
        "capacity_pool_count": snapshot.capacity_pool_count,
        "next_transition_at": snapshot.next_transition_at.isoformat() if snapshot.next_transition_at else None,
    }


async def _mget_ints(redis_client: Any | None, keys: tuple[str, ...]) -> tuple[int, ...]:
    if redis_client is None:
        return tuple(0 for _ in keys)
    if not keys:
        return ()
    try:
        values = await redis_client.mget(keys)
    except Exception:
        return tuple(0 for _ in keys)
    return tuple(_int_value(value) for value in values)


async def _pool_usage_pairs(
    *,
    redis_client: Any | None,
    pools: Sequence[CompiledTierCapacityPoolPolicy],
    window_id: int,
) -> list[tuple[int, int]]:
    keys: list[str] = []
    for pool in pools:
        keys.extend(_pool_counter_keys(pool, window_id=window_id))
    values = await _mget_ints(redis_client, tuple(keys))
    return [(values[index], values[index + 1]) for index in range(0, len(values), 2)]


def _pool_counter_keys(pool: CompiledTierCapacityPoolPolicy, *, window_id: int) -> tuple[str, str]:
    advanced = is_advanced_capacity_pool_strategy(pool.strategy)
    if advanced:
        return (
            fair_share_pool_counter_key(
                dimension="rpm",
                pool_key=pool.pool_key,
                callable_key=pool.callable_key,
                window_id=window_id,
            ),
            fair_share_pool_counter_key(
                dimension="tpm",
                pool_key=pool.pool_key,
                callable_key=pool.callable_key,
                window_id=window_id,
            ),
        )
    return (
        static_pool_counter_key(
            scope="tier_pool_model_rpm",
            pool_key=pool.pool_key,
            callable_key=pool.callable_key,
            window_id=window_id,
        ),
        static_pool_counter_key(
            scope="tier_pool_model_tpm",
            pool_key=pool.pool_key,
            callable_key=pool.callable_key,
            window_id=window_id,
        ),
    )


def _pool_dashboard_candidates(
    *,
    snapshot: TierPolicySnapshot,
    pools: Sequence[CompiledTierCapacityPoolPolicy],
    usage_pairs: Sequence[tuple[int, int]],
    heatmap_hits: Mapping[_PoolRef, int],
) -> list[_PoolDashboardCandidate]:
    candidates: list[_PoolDashboardCandidate] = []
    for index, pool in enumerate(pools):
        rpm_used, tpm_used = usage_pairs[index] if index < len(usage_pairs) else (0, 0)
        pool_ref = (pool.pool_key, pool.callable_key)
        candidates.append(
            _PoolDashboardCandidate(
                pool=pool,
                advanced=is_advanced_capacity_pool_strategy(pool.strategy),
                member_count=len(snapshot.capacity_pool_members.get(pool_ref, ())),
                rpm_used=rpm_used,
                tpm_used=tpm_used,
                rpm_saturation=_ratio(rpm_used, pool.rpm_capacity),
                tpm_saturation=_ratio(tpm_used, pool.tpm_capacity),
                heatmap_hits=int(heatmap_hits.get(pool_ref, 0)),
            )
        )
    return candidates


def _candidate_sort_key(candidate: _PoolDashboardCandidate) -> tuple[int, float, str, str]:
    return (
        -candidate.heatmap_hits,
        -max(float(candidate.rpm_saturation or 0.0), float(candidate.tpm_saturation or 0.0)),
        candidate.pool.pool_key,
        candidate.pool.callable_key,
    )


def _candidate_is_saturated(candidate: _PoolDashboardCandidate) -> bool:
    threshold = normalized_saturation_threshold(candidate.pool.saturation_threshold)
    return max(float(candidate.rpm_saturation or 0.0), float(candidate.tpm_saturation or 0.0)) >= threshold


def _pool_summary(
    candidate: _PoolDashboardCandidate,
    *,
    active_org_count: int,
    top_orgs: list[dict[str, Any]],
    active_boosts: list[dict[str, Any]],
    active_boost_count: int,
    cleanup_lagged: bool,
) -> dict[str, Any]:
    pool = candidate.pool
    return {
        "pool_key": pool.pool_key,
        "callable_key": pool.callable_key,
        "strategy": pool.strategy,
        "advanced_fair_share": candidate.advanced,
        "rpm_capacity": pool.rpm_capacity,
        "tpm_capacity": pool.tpm_capacity,
        "rpm_used": candidate.rpm_used,
        "tpm_used": candidate.tpm_used,
        "rpm_saturation": candidate.rpm_saturation,
        "tpm_saturation": candidate.tpm_saturation,
        "saturation_threshold": pool.saturation_threshold,
        "burst_multiplier": pool.burst_multiplier,
        "member_count": candidate.member_count,
        "active_org_count": active_org_count,
        "top_orgs": top_orgs,
        "active_boosts": active_boosts,
        "active_boost_count": active_boost_count,
        "cleanup_lagged": cleanup_lagged,
    }


async def _top_orgs_for_pools(
    *,
    redis_client: Any | None,
    candidates: Sequence[_PoolDashboardCandidate],
    window_id: int,
    limit: int,
) -> tuple[dict[_PoolRef, list[dict[str, Any]]], dict[_PoolRef, int]]:
    if redis_client is None or not candidates:
        return {}, {}
    org_limit = max(1, int(limit))
    try:
        pipe = redis_client.pipeline()
        for candidate in candidates:
            pool_key, callable_key = candidate.ref
            pipe.get(fair_share_active_count_key(pool_key, callable_key))
            pipe.zrevrange(
                fair_share_usage_rank_key(
                    pool_key=pool_key,
                    callable_key=callable_key,
                    window_id=window_id,
                ),
                0,
                org_limit - 1,
            )
        raw_results = await pipe.execute()
    except Exception:
        return {}, {}

    active_counts: dict[_PoolRef, int] = {}
    ranked_orgs: dict[_PoolRef, list[str]] = {}
    for index, candidate in enumerate(candidates):
        base_index = index * 2
        active_counts[candidate.ref] = _int_value(_value_at(raw_results, base_index))
        raw_orgs = _value_at(raw_results, base_index + 1, default=()) or ()
        ranked_orgs[candidate.ref] = [_text_value(raw_org) for raw_org in raw_orgs]

    counter_keys: list[str] = []
    counter_refs: list[tuple[_PoolRef, str]] = []
    for candidate in candidates:
        for organization_id in ranked_orgs.get(candidate.ref, ()):
            pool_key, callable_key = candidate.ref
            counter_keys.extend(
                (
                    fair_share_org_counter_key(
                        dimension="rpm",
                        pool_key=pool_key,
                        callable_key=callable_key,
                        organization_id=organization_id,
                        window_id=window_id,
                    ),
                    fair_share_org_counter_key(
                        dimension="tpm",
                        pool_key=pool_key,
                        callable_key=callable_key,
                        organization_id=organization_id,
                        window_id=window_id,
                    ),
                )
            )
            counter_refs.append((candidate.ref, organization_id))
    counter_values = await _mget_ints(redis_client, tuple(counter_keys))

    rows: list[dict[str, Any]] = []
    top_orgs: dict[_PoolRef, list[dict[str, Any]]] = {candidate.ref: [] for candidate in candidates}
    for index, (pool_ref, organization_id) in enumerate(counter_refs):
        value_index = index * 2
        rpm_used = counter_values[value_index] if value_index < len(counter_values) else 0
        tpm_used = counter_values[value_index + 1] if value_index + 1 < len(counter_values) else 0
        rows.append(
            {
                "pool_ref": pool_ref,
                "organization_id": organization_id,
                "rpm_used": rpm_used,
                "tpm_used": tpm_used,
                "total_usage": rpm_used + tpm_used,
            }
        )
    rows.sort(key=lambda item: (int(item["total_usage"]), str(item["organization_id"])), reverse=True)
    for row in rows:
        pool_ref = row.pop("pool_ref")
        top_orgs.setdefault(pool_ref, []).append(row)
    return top_orgs, active_counts


async def _active_boosts_for_pools(
    *,
    redis_client: Any | None,
    candidates: Sequence[_PoolDashboardCandidate],
    now_ms: int,
) -> tuple[dict[_PoolRef, list[dict[str, Any]]], dict[_PoolRef, int]]:
    if redis_client is None or not candidates:
        return {}, {}
    boost_limit = DASHBOARD_MAX_ACTIVE_BOOSTS_PER_POOL
    try:
        pipe = redis_client.pipeline()
        for candidate in candidates:
            pool_key, callable_key = candidate.ref
            index_key = fair_share_boost_index_key(pool_key=pool_key, callable_key=callable_key)
            pipe.zcount(index_key, now_ms, "+inf")
            pipe.zrangebyscore(index_key, now_ms, "+inf", start=0, num=boost_limit)
        raw_results = await pipe.execute()
    except Exception:
        return {}, {}

    active_counts: dict[_PoolRef, int] = {}
    orgs_by_pool: dict[_PoolRef, list[str]] = {}
    for index, candidate in enumerate(candidates):
        base_index = index * 2
        active_counts[candidate.ref] = _int_value(_value_at(raw_results, base_index))
        raw_orgs = _value_at(raw_results, base_index + 1, default=()) or ()
        orgs_by_pool[candidate.ref] = [_text_value(raw_org) for raw_org in raw_orgs]

    metadata_refs: list[tuple[_PoolRef, list[str]]] = []
    try:
        metadata_pipe = redis_client.pipeline()
        for candidate in candidates:
            orgs = orgs_by_pool.get(candidate.ref, [])
            if not orgs:
                continue
            pool_key, callable_key = candidate.ref
            metadata_pipe.hmget(fair_share_boost_metadata_key(pool_key=pool_key, callable_key=callable_key), orgs)
            metadata_refs.append((candidate.ref, orgs))
        raw_metadata_results = await metadata_pipe.execute() if metadata_refs else []
    except Exception:
        raw_metadata_results = []

    boosts_by_pool: dict[_PoolRef, list[dict[str, Any]]] = {}
    for index, (pool_ref, orgs) in enumerate(metadata_refs):
        raw_metadata = _value_at(raw_metadata_results, index, default=()) or ()
        boosts: list[dict[str, Any]] = []
        for org_index, organization_id in enumerate(orgs):
            metadata_value = _value_at(raw_metadata, org_index)
            boosts.append(_boost_row_from_metadata(organization_id, metadata_value))
        boosts_by_pool[pool_ref] = boosts
    for candidate in candidates:
        boosts_by_pool.setdefault(candidate.ref, [])
    return boosts_by_pool, active_counts


async def _cleanup_lagged_for_pools(
    *,
    redis_client: Any | None,
    candidates: Sequence[_PoolDashboardCandidate],
) -> dict[_PoolRef, bool]:
    if redis_client is None or not candidates:
        return {}
    keys = tuple(fair_share_cleanup_lag_key(*candidate.ref) for candidate in candidates)
    values = await _mget_ints(redis_client, keys)
    return {
        candidate.ref: bool(values[index])
        for index, candidate in enumerate(candidates)
        if index < len(values)
    }


async def _read_limit_hit_heatmap(
    redis_client: Any | None,
    *,
    window_id: int,
    limit: int,
) -> list[dict[str, Any]]:
    if redis_client is None:
        return []
    normalized_limit = max(1, int(limit))
    heatmap_key = fair_share_limit_hit_heatmap_key(window_id)
    rank_key = fair_share_limit_hit_heatmap_rank_key(window_id)
    try:
        raw_fields = await redis_client.zrevrange(rank_key, 0, normalized_limit - 1)
        fields = [_text_value(raw_field) for raw_field in raw_fields or ()]
        if fields:
            raw_counts = await redis_client.hmget(heatmap_key, fields)
            rows = _heatmap_rows_from_fields(fields, raw_counts)
            if rows:
                return _sort_heatmap_rows(rows)[:normalized_limit]
    except Exception:
        pass

    try:
        raw = await redis_client.hgetall(heatmap_key)
    except Exception:
        return []
    rows = _heatmap_rows_from_mapping(raw or {})
    return _sort_heatmap_rows(rows)[:normalized_limit]


async def _read_limit_hit_total(
    redis_client: Any | None,
    *,
    window_id: int,
    fallback_heatmap: Sequence[Mapping[str, Any]],
) -> int:
    fallback_count = sum(int(row.get("count") or 0) for row in fallback_heatmap)
    if redis_client is None:
        return fallback_count
    try:
        value = await redis_client.get(fair_share_limit_hit_total_key(window_id))
    except Exception:
        return fallback_count
    total_count = _int_value(value)
    return total_count if total_count > 0 else fallback_count


def _pool_heatmap_hit_map(heatmap: Sequence[Mapping[str, Any]]) -> dict[_PoolRef, int]:
    hits: dict[_PoolRef, int] = {}
    for row in heatmap:
        pool_key = _text_value(row.get("pool_key"))
        callable_key = _text_value(row.get("callable_key"))
        if not pool_key or not callable_key:
            continue
        pool_ref = (pool_key, callable_key)
        hits[pool_ref] = hits.get(pool_ref, 0) + _int_value(row.get("count"))
    return hits


def _heatmap_rows_from_mapping(raw: Mapping[Any, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for field, count in raw.items():
        row = _heatmap_row_from_field(_text_value(field), count)
        if row is not None:
            rows.append(row)
    return rows


def _heatmap_rows_from_fields(fields: Sequence[str], counts: Sequence[Any] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw_counts = counts or ()
    for index, field in enumerate(fields):
        row = _heatmap_row_from_field(field, _value_at(raw_counts, index, default=0))
        if row is not None:
            rows.append(row)
    return rows


def _heatmap_row_from_field(field: str, count: Any) -> dict[str, Any] | None:
    parsed_count = _int_value(count)
    if parsed_count <= 0:
        return None
    pool_key, callable_key, organization_id, scope, tier_key = _split_heatmap_field(field)
    return {
        "pool_key": pool_key,
        "callable_key": callable_key,
        "organization_id": organization_id,
        "scope": scope,
        "tier_key": tier_key,
        "count": parsed_count,
    }


def _sort_heatmap_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows.sort(
        key=lambda item: (
            -int(item.get("count") or 0),
            str(item.get("pool_key") or ""),
            str(item.get("callable_key") or ""),
            str(item.get("organization_id") or ""),
            str(item.get("scope") or ""),
        )
    )
    return rows


def _split_heatmap_field(field: str) -> tuple[str, str, str, str, str | None]:
    legacy_parts = field.split("|", 3)
    if len(legacy_parts) == 4 and legacy_parts[2].startswith("tier_pool_"):
        return legacy_parts[0], legacy_parts[1], "", legacy_parts[2], legacy_parts[3] or None
    parts = field.split("|", 4)
    while len(parts) < 5:
        parts.append("")
    return parts[0], parts[1], parts[2], parts[3], parts[4] or None


def _boost_row_from_metadata(organization_id: str, metadata_value: Any) -> dict[str, Any]:
    boost = _decode_metadata(metadata_value)
    expires_at_ms = _float_value(boost.get("expires_at_ms"))
    return {
        "organization_id": organization_id,
        "weight_multiplier": max(1.0, _float_value(boost.get("weight_multiplier"), default=1.0)),
        "reason": boost.get("reason") or None,
        "expires_at": datetime.fromtimestamp(expires_at_ms / 1000, tz=UTC).isoformat() if expires_at_ms > 0 else None,
    }


def _float_value(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_value(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value)


def _value_at(values: Sequence[Any], index: int, *, default: Any = None) -> Any:
    try:
        return values[index]
    except (IndexError, TypeError):
        return default


def _encode_metadata(metadata: Mapping[str, Any]) -> str:
    return "|".join(f"{key}={str(value).replace('|', ' ')}" for key, value in metadata.items())


def _decode_metadata(value: Any) -> dict[str, str]:
    text = str(value or "")
    parsed: dict[str, str] = {}
    for part in text.split("|"):
        if "=" not in part:
            continue
        key, raw_value = part.split("=", 1)
        parsed[key] = raw_value
    return parsed


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "__dataclass_fields__"):
        return {
            key: _json_value(getattr(value, key))
            for key in value.__dataclass_fields__
        }
    if hasattr(value, "__dict__"):
        return {
            str(key): _json_value(item)
            for key, item in vars(value).items()
            if not str(key).startswith("_")
        }
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value
