from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
import time
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from src.db.callable_targets import CallableTargetBindingRecord
from src.db.repositories import KeyRecord
from src.router.runtime_generation import (
    RoutingRuntimeGeneration,
    RoutingRuntimeGenerationStore,
)
from src.guardrails.middleware import GuardrailMiddleware
from src.guardrails.registry import GuardrailRegistry
from src.main import create_app
from src.providers.bedrock import BedrockAdapter
from src.providers.azure import AzureOpenAIAdapter
from src.providers.anthropic import AnthropicAdapter
from src.providers.gemini import GeminiAdapter
from src.providers.openai import OpenAIAdapter
from src.providers.registry import ProviderErrorMapperRegistry
from src.router import (
    CooldownManager,
    FallbackConfig,
    FailoverManager,
    HealthEndpointHandler,
    RedisStateBackend,
    Router,
    RouterConfig,
    RoutingStrategy,
    build_deployment_registry,
)
from src.services.callable_target_grants import CallableTargetGrantService
from src.services.callable_targets import build_callable_target_catalog
from src.services.key_service import KeyService
from src.services.limit_counter import LimitCounter


class NoopBudgetService:
    async def check_budgets(self, **kwargs):
        return None


class NoopSpendTrackingService:
    async def log_spend(self, **kwargs):
        return None

    async def log_request_failure(self, **kwargs):
        return None


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, int | str] = {}
        self.hash_store: dict[str, dict[str, str]] = {}
        self.zset_store: dict[str, list[tuple[int, str]]] = {}
        self.ttl_store: dict[str, int] = {}
        self.script_store: dict[str, str] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str):
        self.store[key] = value
        self.ttl_store[key] = int(ttl)

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False):
        if nx and key in self.store:
            return False
        self.store[key] = value
        if ex is not None:
            self.ttl_store[key] = int(ex)
        return True

    async def getdel(self, key: str):
        value = self.store.get(key)
        self.store.pop(key, None)
        self.ttl_store.pop(key, None)
        return value

    async def incr(self, key: str):
        self.store[key] = int(self.store.get(key, 0)) + 1
        return int(self.store[key])

    async def incrby(self, key: str, amount: int):
        self.store[key] = int(self.store.get(key, 0)) + amount
        return int(self.store[key])

    async def decr(self, key: str):
        self.store[key] = int(self.store.get(key, 0)) - 1
        return int(self.store[key])

    async def expire(self, key: str, ttl: int):
        if key in self.store or key in self.hash_store or key in self.zset_store:
            self.ttl_store[key] = int(ttl)
        return True

    async def pexpire(self, key: str, ttl: int):
        if key in self.store or key in self.hash_store or key in self.zset_store:
            self.ttl_store[key] = max(1, int(ttl) // 1000)
        return True

    async def pexpireat(self, key: str, expires_at_ms: int):
        if key in self.store or key in self.hash_store or key in self.zset_store:
            remaining_ms = max(1, int(expires_at_ms) - int(time.time() * 1000))
            self.ttl_store[key] = max(1, remaining_ms // 1000)
        return True

    async def ttl(self, key: str):
        if key not in self.store and key not in self.hash_store and key not in self.zset_store:
            return -2
        return self.ttl_store.get(key, -1)

    async def pttl(self, key: str):
        ttl = await self.ttl(key)
        return ttl * 1000 if ttl >= 0 else ttl

    async def mget(self, keys):
        return [self.store.get(key) for key in keys]

    async def delete(self, *keys: str):
        for key in keys:
            self.store.pop(key, None)
            self.hash_store.pop(key, None)
            self.zset_store.pop(key, None)
            self.ttl_store.pop(key, None)

    async def exists(self, key: str):
        return 1 if key in self.store else 0

    async def hset(self, key: str, mapping: dict[str, str]):
        self.hash_store.setdefault(key, {}).update(mapping)

    async def hdel(self, key: str, *fields: str):
        entry = self.hash_store.get(key)
        if entry is None:
            return 0
        removed = 0
        for field in fields:
            if field in entry:
                entry.pop(field, None)
                removed += 1
        return removed

    async def hgetall(self, key: str):
        return self.hash_store.get(key, {})

    async def hmget(self, key: str, fields):
        entry = self.hash_store.get(key, {})
        return [entry.get(field) for field in fields]

    async def hincrby(self, key: str, field: str, amount: int):
        entry = self.hash_store.setdefault(key, {})
        entry[field] = str(int(entry.get(field, 0)) + int(amount))
        return int(entry[field])

    async def zadd(self, key: str, mapping: dict[str, int]):
        items = self.zset_store.setdefault(key, [])
        for member in mapping:
            items[:] = [(score, existing) for score, existing in items if existing != member]
        for member, score in mapping.items():
            items.append((int(score), member))

    async def zincrby(self, key: str, amount: int, member: str):
        items = self.zset_store.setdefault(key, [])
        current_score = 0
        retained: list[tuple[int, str]] = []
        for score, existing_member in items:
            if existing_member == member:
                current_score = int(score)
            else:
                retained.append((score, existing_member))
        new_score = current_score + int(amount)
        retained.append((new_score, member))
        self.zset_store[key] = retained
        return new_score

    async def zrem(self, key: str, *members: str):
        values = self.zset_store.get(key, [])
        before = len(values)
        self.zset_store[key] = [
            (score, member) for score, member in values if member not in set(members)
        ]
        if not self.zset_store[key]:
            self.zset_store.pop(key, None)
        return before - len(self.zset_store.get(key, []))

    async def zremrangebyscore(self, key: str, min_score: int, max_score: int):
        values = self.zset_store.get(key, [])
        self.zset_store[key] = [
            (s, m) for s, m in values if not (int(min_score) <= s <= int(max_score))
        ]

    async def zrangebyscore(
        self,
        key: str,
        min_score: int,
        max_score: str,
        start: int | None = None,
        num: int | None = None,
    ):
        values = self.zset_store.get(key, [])
        max_is_inf = str(max_score).lower() in {"+inf", "inf"}
        max_value = None if max_is_inf else int(max_score)
        filtered = [
            member
            for score, member in sorted(values, key=lambda item: (item[0], item[1]))
            if score >= int(min_score) and (max_value is None or score <= max_value)
        ]
        if start is not None and num is not None:
            filtered = filtered[int(start) : int(start) + int(num)]
        return filtered

    async def zcount(self, key: str, min_score: int, max_score: str):
        values = self.zset_store.get(key, [])
        max_is_inf = str(max_score).lower() in {"+inf", "inf"}
        max_value = None if max_is_inf else int(max_score)
        return sum(
            1
            for score, _member in values
            if score >= int(min_score) and (max_value is None or score <= max_value)
        )

    async def zcard(self, key: str):
        return len(self.zset_store.get(key, []))

    async def zrevrange(self, key: str, start: int, end: int):
        values = sorted(
            self.zset_store.get(key, []), key=lambda item: (item[0], item[1]), reverse=True
        )
        if end < 0:
            sliced = values[int(start) :]
        else:
            sliced = values[int(start) : int(end) + 1]
        return [member for _score, member in sliced]

    def pipeline(self):
        return FakePipeline(self)

    async def ping(self):
        return True

    async def script_load(self, script: str):
        sha = hashlib.sha1(script.encode("utf-8")).hexdigest()
        self.script_store[sha] = script
        return sha

    async def evalsha(self, sha: str, numkeys: int, *args):
        script = self.script_store.get(str(sha))
        if script is None:
            raise RuntimeError("NOSCRIPT No matching script. Please use EVAL.")
        return await self.eval(script, numkeys, *args)

    async def eval(self, script: str, numkeys: int, *args):
        if "redis.call('SETEX', KEYS[2]" in script:
            lock_key = str(args[0])
            cache_key = str(args[1])
            token = str(args[2])
            ttl = int(args[3])
            payload = str(args[4])
            if str(self.store.get(lock_key)) != token:
                return 0
            await self.setex(cache_key, ttl, payload)
            return 1

        if "return redis.call('EXPIRE', KEYS[1], ARGV[2])" in script:
            key = str(args[0])
            token = str(args[1])
            ttl = int(args[2])
            if str(self.store.get(key)) != token:
                return 0
            return int(await self.expire(key, ttl))

        if "return redis.call('DEL', KEYS[1])" in script:
            key = str(args[0])
            token = str(args[1])
            if str(self.store.get(key)) != token:
                return 0
            self.store.pop(key, None)
            self.ttl_store.pop(key, None)
            return 1

        keys = [str(item) for item in args[:numkeys]]
        argv = [str(item) for item in args[numkeys:]]
        n = len(keys)

        if "router_attempt_release_v2" in script:
            active_key, owners_key, recovery_key = keys
            owner_token = argv[0]
            owners = self.zset_store.get(owners_key, [])
            removed = any(member == owner_token for _score, member in owners)
            if removed:
                self.zset_store[owners_key] = [
                    (score, member) for score, member in owners if member != owner_token
                ]
                if not self.zset_store[owners_key]:
                    self.zset_store.pop(owners_key, None)
                    self.ttl_store.pop(owners_key, None)
            current = int(self.store.get(active_key, 0) or 0)
            if removed:
                current = max(0, current - 1)
            current = max(current, len(self.zset_store.get(owners_key, [])))
            if current <= 0:
                self.store.pop(active_key, None)
                self.ttl_store.pop(active_key, None)
            else:
                self.store[active_key] = current
            if self.store.get(recovery_key) == owner_token:
                self.store.pop(recovery_key, None)
                self.ttl_store.pop(recovery_key, None)
            return current

        if "router_attempt_admission_v2" in script:
            active_key, owners_key, cooldown_key, health_key, recovery_key = keys[:5]
            now_ms = int(time.time() * 1000)
            owners = self.zset_store.get(owners_key, [])
            expired_count = sum(1 for score, _member in owners if score <= now_ms)
            if expired_count:
                self.zset_store[owners_key] = [
                    (score, member) for score, member in owners if score > now_ms
                ]
                if not self.zset_store[owners_key]:
                    self.zset_store.pop(owners_key, None)
                    self.ttl_store.pop(owners_key, None)
            current_active = max(
                0,
                int(self.store.get(active_key, 0) or 0) - expired_count,
            )
            current_active = max(current_active, len(self.zset_store.get(owners_key, [])))
            if current_active:
                self.store[active_key] = current_active
            else:
                self.store.pop(active_key, None)
                self.ttl_store.pop(active_key, None)
            if cooldown_key in self.store:
                return [0, "cooldown", 0]
            capacity_count = int(argv[0])
            for index in range(capacity_count):
                current = int(self.store.get(keys[5 + index], 0) or 0)
                limit = int(argv[4 + index])
                if current >= limit:
                    return [0, "capacity", 0]

            lease_ttl_ms = int(argv[1])
            owner_token = argv[3]
            recovery = 0
            health = self.hash_store.get(health_key, {})
            if health.get("healthy") == "false":
                if health.get("recovery_required") != "true":
                    return [0, "unhealthy", 0]
                if recovery_key in self.store:
                    return [0, "recovery_in_progress", 0]
                self.store[recovery_key] = owner_token
                self.ttl_store[recovery_key] = max(1, lease_ttl_ms // 1000)
                recovery = 1
            expires_at_ms = now_ms + lease_ttl_ms
            await self.zadd(owners_key, {owner_token: expires_at_ms})
            active = current_active + 1
            self.store[active_key] = active
            ttl_seconds = max(1, (lease_ttl_ms + int(argv[4 + capacity_count])) // 1000)
            self.ttl_store[active_key] = ttl_seconds
            self.ttl_store[owners_key] = ttl_seconds
            return [1, "acquired", active, expires_at_ms, recovery]

        if "router_health_probe_claim_v1" in script:
            probe_key, cooldown_key, health_key, recovery_key = keys
            owner_token = argv[0]
            ttl_seconds = max(1, int(argv[1]) // 1000)
            if probe_key in self.store:
                return [0, 0]
            self.store[probe_key] = owner_token
            self.ttl_store[probe_key] = ttl_seconds
            health = self.hash_store.get(health_key, {})
            recoverable = (
                cooldown_key not in self.store
                and health.get("healthy") == "false"
                and health.get("recovery_required") == "true"
            )
            if recoverable:
                if recovery_key in self.store:
                    self.store.pop(probe_key, None)
                    self.ttl_store.pop(probe_key, None)
                    return [0, 0]
                self.store[recovery_key] = owner_token
                self.ttl_store[recovery_key] = ttl_seconds
            return [1, int(recoverable)]

        if "router_health_recovery_release_v1" in script:
            recovery_key = keys[0]
            owner_token = argv[0]
            if self.store.get(recovery_key) != owner_token:
                return 0
            self.store.pop(recovery_key, None)
            self.ttl_store.pop(recovery_key, None)
            return 1

        if "router_health_failure_v1" in script:
            failures_key, health_key, cooldown_key, recovery_key = keys
            recovery_token = argv[5]
            health = self.hash_store.setdefault(health_key, {})
            unhealthy = (
                health.get("healthy") == "false" or health.get("recovery_required") == "true"
            )
            if not recovery_token and (
                unhealthy or cooldown_key in self.store or recovery_key in self.store
            ):
                state = "cooldown" if cooldown_key in self.store else "recoverable"
                return [0, 0, 0, state]
            if recovery_token and self.store.get(recovery_key) != recovery_token:
                return [0, 0, 0, "recoverable"]
            failure_count = int(self.store.get(failures_key, 0) or 0) + 1
            self.store[failures_key] = failure_count
            self.ttl_store[failures_key] = int(argv[2])
            health.update(
                {
                    "consecutive_failures": str(failure_count),
                    "last_error": argv[0],
                    "last_error_at": argv[4],
                }
            )
            entered_cooldown = 0
            state = "healthy"
            if health.get("recovery_required") == "true" or failure_count > int(argv[1]):
                state = "cooldown"
                if cooldown_key not in self.store:
                    self.store[cooldown_key] = argv[0]
                    self.ttl_store[cooldown_key] = int(argv[3])
                    entered_cooldown = 1
                    health["cooldown_kind"] = "automatic"
                health.update({"healthy": "false", "recovery_required": "true"})
            if recovery_token and self.store.get(recovery_key) == recovery_token:
                self.store.pop(recovery_key, None)
                self.ttl_store.pop(recovery_key, None)
            self.ttl_store[health_key] = int(argv[6])
            return [1, failure_count, entered_cooldown, state]

        if "router_health_success_v1" in script:
            failures_key, health_key, cooldown_key, recovery_key = keys
            recovery_token = argv[1]
            health = self.hash_store.setdefault(health_key, {})
            if cooldown_key in self.store and health.get("cooldown_kind") == "manual":
                return [0, 0, 0, "cooldown"]
            unhealthy = (
                health.get("healthy") == "false" or health.get("recovery_required") == "true"
            )
            if not recovery_token and (
                unhealthy or cooldown_key in self.store or recovery_key in self.store
            ):
                state = "cooldown" if cooldown_key in self.store else "recoverable"
                return [0, 0, 0, state]
            if recovery_token and self.store.get(recovery_key) != recovery_token:
                return [0, 0, 0, "recoverable"]
            recovered = int(
                cooldown_key in self.store
                or self.hash_store.get(health_key, {}).get("healthy") == "false"
            )
            self.store.pop(failures_key, None)
            self.ttl_store.pop(failures_key, None)
            self.store.pop(cooldown_key, None)
            self.ttl_store.pop(cooldown_key, None)
            self.store.pop(recovery_key, None)
            self.ttl_store.pop(recovery_key, None)
            health.update(
                {
                    "healthy": "true",
                    "recovery_required": "false",
                    "consecutive_failures": "0",
                    "last_success_at": argv[0],
                }
            )
            health.pop("last_error", None)
            health.pop("last_error_at", None)
            health.pop("cooldown_kind", None)
            self.ttl_store[health_key] = int(argv[2])
            return [1, 0, recovered, "healthy"]

        if "router_health_manual_cooldown_v1" in script:
            cooldown_key, health_key, recovery_key = keys
            self.store[cooldown_key] = argv[1]
            self.ttl_store[cooldown_key] = int(argv[0])
            self.store.pop(recovery_key, None)
            self.ttl_store.pop(recovery_key, None)
            self.hash_store.setdefault(health_key, {}).update(
                {
                    "healthy": "false",
                    "recovery_required": "true",
                    "cooldown_kind": "manual",
                    "last_error": argv[1],
                    "last_error_at": argv[2],
                }
            )
            self.ttl_store[health_key] = int(argv[3])
            return 1

        if "router_attempt_active_batch_v1" in script:
            now_ms = int(time.time() * 1000)
            results: list[int] = []
            for index in range(0, len(keys), 2):
                active_key, owners_key = keys[index : index + 2]
                owners = self.zset_store.get(owners_key, [])
                expired_count = sum(1 for score, _member in owners if score <= now_ms)
                if expired_count:
                    self.zset_store[owners_key] = [
                        (score, member) for score, member in owners if score > now_ms
                    ]
                    if not self.zset_store[owners_key]:
                        self.zset_store.pop(owners_key, None)
                        self.ttl_store.pop(owners_key, None)
                current = max(0, int(self.store.get(active_key, 0) or 0) - expired_count)
                current = max(current, len(self.zset_store.get(owners_key, [])))
                if current:
                    self.store[active_key] = current
                else:
                    self.store.pop(active_key, None)
                    self.ttl_store.pop(active_key, None)
                results.append(current)
            return results

        staged_fair_counters: dict[str, int] = {}
        staged_active_states: dict[str, dict[str, Any]] = {}
        staged_active_order: list[dict[str, Any]] = []

        def fair_counter_value(key: str) -> int:
            if key not in staged_fair_counters:
                staged_fair_counters[key] = int(self.store.get(key, 0) or 0)
            return staged_fair_counters[key]

        def stage_fair_counter_increment(key: str, amount: int) -> int:
            next_value = fair_counter_value(key) + int(amount)
            staged_fair_counters[key] = next_value
            return next_value

        def zscore_value(key: str, member: str) -> int | None:
            for score, existing_member in self.zset_store.get(key, []):
                if existing_member == member:
                    return int(score)
            return None

        def active_state_for(
            *,
            active_key: str,
            weight_key: str,
            active_count_key: str,
            total_weight_key: str,
            cleanup_lag_key: str,
            now_ms: int,
            cleanup_limit: int,
        ) -> dict[str, Any]:
            state = staged_active_states.get(active_key)
            if state is not None:
                return state

            active_count = int(self.store.get(active_count_key, 0) or 0)
            total_weight = int(float(self.store.get(total_weight_key, 0) or 0))
            expired = [
                member
                for score, member in sorted(
                    self.zset_store.get(active_key, []), key=lambda item: item[0]
                )
                if score <= now_ms
            ][:cleanup_limit]
            cleanup_lagged = cleanup_limit > 0 and len(expired) >= cleanup_limit
            removed_orgs: list[str] = []
            staged_scores: dict[str, int] = {}
            staged_weights: dict[str, int] = {}
            for member in expired:
                expired_weight = int(float(self.hash_store.get(weight_key, {}).get(member, 0) or 0))
                total_weight -= max(0, expired_weight)
                active_count -= 1
                removed_orgs.append(member)
                staged_scores[member] = 0
                staged_weights[member] = 0

            state = {
                "active_key": active_key,
                "weight_key": weight_key,
                "active_count_key": active_count_key,
                "total_weight_key": total_weight_key,
                "cleanup_lag_key": cleanup_lag_key,
                "active_count": max(0, active_count),
                "total_weight": max(0, total_weight),
                "cleanup_lagged": cleanup_lagged,
                "cleanup_lag_at": now_ms,
                "active_ttl_seconds": 1,
                "removed_orgs": removed_orgs,
                "scores": staged_scores,
                "weights": staged_weights,
                "touched_orgs": [],
                "touched_flags": set(),
            }
            staged_active_states[active_key] = state
            staged_active_order.append(state)
            return state

        def staged_active_score(state: dict[str, Any], organization_id: str) -> int:
            scores = state["scores"]
            if organization_id in scores:
                return int(scores[organization_id])
            return zscore_value(state["active_key"], organization_id) or 0

        def staged_active_weight(state: dict[str, Any], organization_id: str) -> int:
            weights = state["weights"]
            if organization_id in weights:
                return int(weights[organization_id])
            return int(
                float(self.hash_store.get(state["weight_key"], {}).get(organization_id, 0) or 0)
            )

        def stage_active_organization(
            *,
            active_key: str,
            weight_key: str,
            active_count_key: str,
            total_weight_key: str,
            cleanup_lag_key: str,
            now_ms: int,
            cleanup_limit: int,
            organization_id: str,
            effective_weight: int,
            expires_at_ms: int,
            active_ttl_seconds: int,
        ) -> dict[str, Any]:
            state = active_state_for(
                active_key=active_key,
                weight_key=weight_key,
                active_count_key=active_count_key,
                total_weight_key=total_weight_key,
                cleanup_lag_key=cleanup_lag_key,
                now_ms=now_ms,
                cleanup_limit=cleanup_limit,
            )
            state["active_ttl_seconds"] = max(int(state["active_ttl_seconds"]), active_ttl_seconds)
            previous_score = staged_active_score(state, organization_id)
            previous_weight = staged_active_weight(state, organization_id)
            if previous_score > now_ms:
                state["total_weight"] = (
                    int(state["total_weight"]) - previous_weight + effective_weight
                )
            else:
                state["active_count"] = int(state["active_count"]) + 1
                state["total_weight"] = int(state["total_weight"]) + effective_weight

            state["active_count"] = max(1, int(state["active_count"]))
            state["total_weight"] = max(effective_weight, int(state["total_weight"]))
            if organization_id not in state["touched_flags"]:
                state["touched_orgs"].append(organization_id)
                state["touched_flags"].add(organization_id)
            state["scores"][organization_id] = expires_at_ms
            state["weights"][organization_id] = effective_weight
            return state

        async def validate_fair_share(fair_keys: list[str], fair_argv: list[str]):
            active_key, weight_key, active_count_key, total_weight_key = fair_keys[:4]
            rpm_pool_key, rpm_org_key, tpm_pool_key, tpm_org_key = fair_keys[4:8]
            boost_key, usage_rank_key, cleanup_lag_key = fair_keys[8:11]
            limit_hit_heatmap_key, limit_hit_rank_key, limit_hit_total_key = fair_keys[11:14]
            now_ms = int(fair_argv[0])
            active_ttl_ms = int(fair_argv[1])
            organization_id = fair_argv[2]
            base_weight = int(float(fair_argv[3]))
            saturation_threshold = float(fair_argv[4])
            burst_multiplier = max(1.0, float(fair_argv[5]))
            rpm_capacity = int(fair_argv[6])
            rpm_amount = int(fair_argv[7])
            tpm_capacity = int(fair_argv[8])
            tpm_amount = int(fair_argv[9])
            window_seconds = int(fair_argv[10])
            strategy = fair_argv[11] if len(fair_argv) > 11 else "weighted_fair"
            cleanup_limit = int(fair_argv[12]) if len(fair_argv) > 12 else 64
            limit_hit_field_prefix = fair_argv[13] if len(fair_argv) > 13 else ""
            tier_key = fair_argv[14] if len(fair_argv) > 14 else "none"
            boost_multiplier = float(self.store.get(boost_key, 1) or 1)
            if boost_multiplier < 1:
                boost_multiplier = 1.0
            effective_weight = max(1, int(base_weight * boost_multiplier))
            expires_at_ms = now_ms + active_ttl_ms
            ttl_seconds = max(1, active_ttl_ms // 1000)
            active_state = stage_active_organization(
                active_key=active_key,
                weight_key=weight_key,
                active_count_key=active_count_key,
                total_weight_key=total_weight_key,
                cleanup_lag_key=cleanup_lag_key,
                now_ms=now_ms,
                cleanup_limit=cleanup_limit,
                organization_id=organization_id,
                effective_weight=effective_weight,
                expires_at_ms=expires_at_ms,
                active_ttl_seconds=ttl_seconds,
            )
            active_count = int(active_state["active_count"])
            total_weight = int(active_state["total_weight"])
            cleanup_lagged = bool(active_state["cleanup_lagged"])

            async def record_limit_hit(scope: str) -> None:
                field = f"{limit_hit_field_prefix}{scope}|{tier_key}"
                await self.hincrby(limit_hit_heatmap_key, field, 1)
                await self.zincrby(limit_hit_rank_key, 1, field)
                await self.incr(limit_hit_total_key)
                await self.expire(limit_hit_heatmap_key, window_seconds)
                await self.expire(limit_hit_rank_key, window_seconds)
                await self.expire(limit_hit_total_key, window_seconds)

            def check_dimension(
                pool_key: str, org_key: str, capacity: int, amount: int, scope: str, dimension: str
            ):
                if capacity <= 0 or amount <= 0:
                    return [1, scope, "not_configured", 0, 0, capacity, 0, 0.0, dimension]
                pool_current = fair_counter_value(pool_key)
                org_current = fair_counter_value(org_key)
                next_pool = pool_current + amount
                saturation = next_pool / capacity
                share_multiplier = burst_multiplier if strategy == "reserved_burst" else 1.0
                share_limit = max(
                    1,
                    int((capacity * effective_weight * share_multiplier) // max(1.0, total_weight)),
                )
                share_limit = min(capacity, share_limit)
                if next_pool > capacity:
                    return [
                        0,
                        scope,
                        "pool_capacity_exceeded",
                        pool_current,
                        org_current,
                        capacity,
                        share_limit,
                        saturation,
                        dimension,
                    ]
                if cleanup_lagged:
                    return [
                        1,
                        scope,
                        "cleanup_lagged",
                        pool_current,
                        org_current,
                        capacity,
                        share_limit,
                        saturation,
                        dimension,
                    ]
                if saturation > saturation_threshold and org_current + amount > share_limit:
                    return [
                        0,
                        scope,
                        "weighted_share_exceeded",
                        pool_current,
                        org_current,
                        capacity,
                        share_limit,
                        saturation,
                        dimension,
                    ]
                return [
                    1,
                    scope,
                    "allowed",
                    pool_current,
                    org_current,
                    capacity,
                    share_limit,
                    saturation,
                    dimension,
                ]

            rpm = check_dimension(
                rpm_pool_key,
                rpm_org_key,
                rpm_capacity,
                rpm_amount,
                "tier_pool_fair_share_rpm",
                "rpm",
            )
            if rpm[0] == 0:
                await record_limit_hit(str(rpm[1]))
                return [
                    [
                        0,
                        rpm[1],
                        rpm[2],
                        active_count,
                        total_weight,
                        effective_weight,
                        rpm[3],
                        rpm[4],
                        rpm[5],
                        rpm[6],
                        int(rpm[7] * 1_000_000),
                        rpm[8],
                    ],
                    None,
                ]
            rpm_org_total = int(rpm[4])
            if rpm_capacity > 0 and rpm_amount > 0:
                stage_fair_counter_increment(rpm_pool_key, rpm_amount)
                rpm_org_total = stage_fair_counter_increment(rpm_org_key, rpm_amount)

            tpm = check_dimension(
                tpm_pool_key,
                tpm_org_key,
                tpm_capacity,
                tpm_amount,
                "tier_pool_fair_share_tpm",
                "tpm",
            )
            if tpm[0] == 0:
                await record_limit_hit(str(tpm[1]))
                return [
                    [
                        0,
                        tpm[1],
                        tpm[2],
                        active_count,
                        total_weight,
                        effective_weight,
                        tpm[3],
                        tpm[4],
                        tpm[5],
                        tpm[6],
                        int(tpm[7] * 1_000_000),
                        tpm[8],
                    ],
                    None,
                ]
            tpm_org_total = int(tpm[4])
            if tpm_capacity > 0 and tpm_amount > 0:
                stage_fair_counter_increment(tpm_pool_key, tpm_amount)
                tpm_org_total = stage_fair_counter_increment(tpm_org_key, tpm_amount)

            commit = {
                "rpm_pool_key": rpm_pool_key,
                "rpm_org_key": rpm_org_key,
                "tpm_pool_key": tpm_pool_key,
                "tpm_org_key": tpm_org_key,
                "usage_rank_key": usage_rank_key,
                "organization_id": organization_id,
                "rpm_capacity": rpm_capacity,
                "rpm_amount": rpm_amount,
                "tpm_capacity": tpm_capacity,
                "tpm_amount": tpm_amount,
                "window_seconds": window_seconds,
                "rpm_org_current": rpm_org_total,
                "tpm_org_current": tpm_org_total,
            }
            reason = (
                "cleanup_lagged"
                if rpm[2] == "cleanup_lagged" or tpm[2] == "cleanup_lagged"
                else "allowed"
            )
            selected = tpm if float(tpm[7]) > float(rpm[7]) else rpm
            return [
                [
                    1,
                    selected[1],
                    reason,
                    active_count,
                    total_weight,
                    effective_weight,
                    selected[3],
                    selected[4],
                    selected[5],
                    selected[6],
                    int(selected[7] * 1_000_000),
                    selected[8],
                ],
                commit,
            ]

        async def commit_active_states() -> None:
            for state in staged_active_order:
                for organization_id in state["removed_orgs"]:
                    await self.zrem(state["active_key"], organization_id)
                    await self.hdel(state["weight_key"], organization_id)
                for organization_id in state["touched_orgs"]:
                    await self.zadd(
                        state["active_key"], {organization_id: state["scores"][organization_id]}
                    )
                    await self.hset(
                        state["weight_key"],
                        {organization_id: str(state["weights"][organization_id])},
                    )
                self.store[state["active_count_key"]] = str(state["active_count"])
                self.store[state["total_weight_key"]] = str(state["total_weight"])
                ttl_seconds = int(state["active_ttl_seconds"])
                await self.expire(state["active_key"], ttl_seconds)
                await self.expire(state["weight_key"], ttl_seconds)
                await self.expire(state["active_count_key"], ttl_seconds)
                await self.expire(state["total_weight_key"], ttl_seconds)
                if state["cleanup_lagged"]:
                    self.store[state["cleanup_lag_key"]] = str(state["cleanup_lag_at"])
                    await self.expire(state["cleanup_lag_key"], ttl_seconds)

        async def commit_fair_share(commit: dict[str, Any]):
            rpm_org_total = int(commit["rpm_org_current"])
            tpm_org_total = int(commit["tpm_org_current"])
            if int(commit["rpm_capacity"]) > 0 and int(commit["rpm_amount"]) > 0:
                await self.incrby(commit["rpm_pool_key"], int(commit["rpm_amount"]))
                rpm_org_total = await self.incrby(commit["rpm_org_key"], int(commit["rpm_amount"]))
                await self.expire(commit["rpm_pool_key"], int(commit["window_seconds"]))
                await self.expire(commit["rpm_org_key"], int(commit["window_seconds"]))
            if int(commit["tpm_capacity"]) > 0 and int(commit["tpm_amount"]) > 0:
                await self.incrby(commit["tpm_pool_key"], int(commit["tpm_amount"]))
                tpm_org_total = await self.incrby(commit["tpm_org_key"], int(commit["tpm_amount"]))
                await self.expire(commit["tpm_pool_key"], int(commit["window_seconds"]))
                await self.expire(commit["tpm_org_key"], int(commit["window_seconds"]))
            usage_score = rpm_org_total + tpm_org_total
            if usage_score > 0:
                await self.zadd(commit["usage_rank_key"], {commit["organization_id"]: usage_score})
                await self.expire(commit["usage_rank_key"], int(commit["window_seconds"]))

        if "tier_admission_v2" in script:
            rate_count = int(argv[0])
            fair_count = int(argv[1])
            legacy_parallel_count = int(argv[2])
            parallel_count = int(argv[3])
            now_ms = int(argv[4])
            parallel_expires_at_ms = int(argv[5])
            parallel_ttl_seconds = int(argv[6])
            cursor = 7
            amounts = [int(argv[cursor + i]) for i in range(rate_count)]
            cursor += rate_count
            limits = [int(argv[cursor + i]) for i in range(rate_count)]
            cursor += rate_count
            ttls = [int(argv[cursor + i]) for i in range(rate_count)]
            cursor += rate_count
            legacy_parallel_limits = [int(argv[cursor + i]) for i in range(legacy_parallel_count)]
            cursor += legacy_parallel_count
            parallel_limits = [int(argv[cursor + i]) for i in range(parallel_count)]
            cursor += parallel_count
            parallel_requested = [int(argv[cursor + i]) for i in range(parallel_count)]
            cursor += parallel_count
            parallel_token_count = sum(parallel_requested)
            parallel_tokens = argv[cursor : cursor + parallel_token_count]
            cursor += parallel_token_count
            capacity_arg_start = cursor + (fair_count * 15)
            capacity_count = int(argv[capacity_arg_start]) if capacity_arg_start < len(argv) else 0
            capacity_by_rate_index: dict[int, tuple[str, int]] = {}
            for idx in range(capacity_count):
                arg_start = capacity_arg_start + 1 + (idx * 3)
                capacity_by_rate_index[int(argv[arg_start])] = (
                    argv[arg_start + 1],
                    int(argv[arg_start + 2]),
                )
            capacity_key_start = (
                rate_count + legacy_parallel_count + parallel_count + (fair_count * 14)
            )

            async def record_capacity_limit_hit(rate_index: int) -> None:
                metadata = capacity_by_rate_index.get(rate_index)
                if metadata is None:
                    return
                field, ttl = metadata
                heatmap_key, rank_key, total_key = keys[capacity_key_start : capacity_key_start + 3]
                await self.hincrby(heatmap_key, field, 1)
                await self.zincrby(rank_key, 1, field)
                await self.incr(total_key)
                await self.expire(heatmap_key, ttl)
                await self.expire(rank_key, ttl)
                await self.expire(total_key, ttl)

            for idx in range(rate_count):
                current = int(self.store.get(keys[idx], 0) or 0)
                attempted = current + amounts[idx]
                if attempted > limits[idx]:
                    await record_capacity_limit_hit(idx + 1)
                    return [0, "rate", idx + 1, attempted, current, limits[idx]]
            legacy_key_start = rate_count
            for idx in range(legacy_parallel_count):
                key = keys[legacy_key_start + idx]
                current = max(0, int(self.store.get(key, 0) or 0))
                if current + 1 > legacy_parallel_limits[idx]:
                    return [0, "parallel", "legacy", idx + 1]
            parallel_key_start = rate_count + legacy_parallel_count
            for idx in range(parallel_count):
                key = keys[parallel_key_start + idx]
                self.zset_store[key] = [
                    (score, member)
                    for score, member in self.zset_store.get(key, [])
                    if score > now_ms
                ]
                if (
                    len(self.zset_store.get(key, [])) + parallel_requested[idx]
                    > parallel_limits[idx]
                ):
                    return [0, "parallel", "lease", idx + 1]
            decisions = []
            commits = []
            for idx in range(fair_count):
                fair_key_start = rate_count + legacy_parallel_count + parallel_count + (idx * 14)
                fair_arg_start = cursor + (idx * 15)
                decision, commit = await validate_fair_share(
                    keys[fair_key_start : fair_key_start + 14],
                    argv[fair_arg_start : fair_arg_start + 15],
                )
                if decision[0] == 0:
                    return [0, "fair", idx + 1, *decision]
                decisions.append(decision)
                commits.append(commit)
            result = [1, rate_count, fair_count]
            for idx in range(rate_count):
                new_val = int(self.store.get(keys[idx], 0) or 0) + amounts[idx]
                self.store[keys[idx]] = new_val
                self.ttl_store[keys[idx]] = ttls[idx]
                result.append(new_val)
            for idx in range(legacy_parallel_count):
                key = keys[legacy_key_start + idx]
                self.store[key] = int(self.store.get(key, 0) or 0) + 1
                self.ttl_store[key] = parallel_ttl_seconds
            token_index = 0
            for idx in range(parallel_count):
                key = keys[parallel_key_start + idx]
                for _ in range(parallel_requested[idx]):
                    token = parallel_tokens[token_index]
                    token_index += 1
                    self.zset_store[key] = [
                        (score, member)
                        for score, member in self.zset_store.get(key, [])
                        if member != token
                    ]
                    self.zset_store.setdefault(key, []).append((parallel_expires_at_ms, token))
                self.ttl_store[key] = parallel_ttl_seconds
            await commit_active_states()
            for decision, commit in zip(decisions, commits, strict=True):
                await commit_fair_share(commit)
                result.extend(decision)
            return result

        if keys and all(key.startswith("parallel:") for key in keys):
            key = keys[0]
            if len(argv) == 2:
                limit = int(argv[0])
                ttl = int(argv[1])
                current = max(0, int(self.store.get(key, 0)))
                if current + 1 > limit:
                    return [0, current]
                self.store[key] = current + 1
                self.ttl_store[key] = ttl
                return [1, current + 1]

            if len(argv) == 1:
                ttl = int(argv[0])
                current = int(self.store.get(key, 0))
                if current <= 0:
                    self.store.pop(key, None)
                    self.ttl_store.pop(key, None)
                    return [1, 0]
                self.ttl_store[key] = ttl
                return [1, current]

            current = int(self.store.get(key, 0))
            if current <= 1:
                self.store.pop(key, None)
                self.ttl_store.pop(key, None)
                return [1, 0]
            next_value = current - 1
            self.store[key] = next_value
            return [1, next_value]

        if keys and all(key.startswith("parallel_lease:") for key in keys):
            if "ZSCORE" in script:
                tokens = argv[:n]
                expires_at_values = [int(value) for value in argv[n : 2 * n]]
                for idx, key in enumerate(keys):
                    token = tokens[idx]
                    if not any(member == token for _score, member in self.zset_store.get(key, [])):
                        continue
                    self.zset_store[key] = [
                        (score, member)
                        for score, member in self.zset_store.get(key, [])
                        if member != token
                    ]
                    self.zset_store.setdefault(key, []).append((expires_at_values[idx], token))
                    self.ttl_store[key] = max(
                        1, (expires_at_values[idx] - int(argv[(2 * n)])) // 1000
                    )
                return [1, 0]

            if len(argv) == n:
                for idx, key in enumerate(keys):
                    token = argv[idx]
                    self.zset_store[key] = [
                        (score, member)
                        for score, member in self.zset_store.get(key, [])
                        if member != token
                    ]
                    if not self.zset_store[key]:
                        self.zset_store.pop(key, None)
                        self.ttl_store.pop(key, None)
                return [1, 0]

            now_ms = int(argv[0])
            expires_at_ms = int(argv[1])
            limits = [int(argv[2 + i]) for i in range(n)]
            requested_counts = [int(argv[2 + n + i]) for i in range(n)]
            token_index = 2 + (2 * n)
            for idx, key in enumerate(keys):
                self.zset_store[key] = [
                    (score, member)
                    for score, member in self.zset_store.get(key, [])
                    if score > now_ms
                ]
                if len(self.zset_store.get(key, [])) + requested_counts[idx] > limits[idx]:
                    return [0, idx + 1]
            for idx, key in enumerate(keys):
                for _ in range(requested_counts[idx]):
                    token = argv[token_index]
                    token_index += 1
                    self.zset_store[key] = [
                        (score, member)
                        for score, member in self.zset_store.get(key, [])
                        if member != token
                    ]
                    self.zset_store.setdefault(key, []).append((expires_at_ms, token))
                self.ttl_store[key] = max(1, (expires_at_ms - now_ms) // 1000)
            return [1, 0]

        amounts = [int(argv[i]) for i in range(n)]
        limits = [int(argv[n + i]) for i in range(n)]

        for idx, key in enumerate(keys):
            current = int(self.store.get(key, 0))
            if current + amounts[idx] > limits[idx]:
                return [0, idx + 1]

        result = [1, 0]
        for idx, key in enumerate(keys):
            new_val = int(self.store.get(key, 0)) + amounts[idx]
            self.store[key] = new_val
            self.ttl_store[key] = 60
            result.append(new_val)

        return result


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self.redis = redis
        self.ops: list[tuple[str, tuple, dict]] = []

    def zadd(self, *args, **kwargs):
        self.ops.append(("zadd", args, kwargs))
        return self

    def zincrby(self, *args, **kwargs):
        self.ops.append(("zincrby", args, kwargs))
        return self

    def zremrangebyscore(self, *args, **kwargs):
        self.ops.append(("zremrangebyscore", args, kwargs))
        return self

    def pexpire(self, *args, **kwargs):
        self.ops.append(("pexpire", args, kwargs))
        return self

    def incr(self, *args, **kwargs):
        self.ops.append(("incr", args, kwargs))
        return self

    def incrby(self, *args, **kwargs):
        self.ops.append(("incrby", args, kwargs))
        return self

    def get(self, *args, **kwargs):
        self.ops.append(("get", args, kwargs))
        return self

    def expire(self, *args, **kwargs):
        self.ops.append(("expire", args, kwargs))
        return self

    def set(self, *args, **kwargs):
        self.ops.append(("set", args, kwargs))
        return self

    def delete(self, *args, **kwargs):
        self.ops.append(("delete", args, kwargs))
        return self

    def hset(self, *args, **kwargs):
        self.ops.append(("hset", args, kwargs))
        return self

    def hgetall(self, *args, **kwargs):
        self.ops.append(("hgetall", args, kwargs))
        return self

    def hmget(self, *args, **kwargs):
        self.ops.append(("hmget", args, kwargs))
        return self

    def hincrby(self, *args, **kwargs):
        self.ops.append(("hincrby", args, kwargs))
        return self

    def hdel(self, *args, **kwargs):
        self.ops.append(("hdel", args, kwargs))
        return self

    def zrem(self, *args, **kwargs):
        self.ops.append(("zrem", args, kwargs))
        return self

    def zrangebyscore(self, *args, **kwargs):
        self.ops.append(("zrangebyscore", args, kwargs))
        return self

    def zcount(self, *args, **kwargs):
        self.ops.append(("zcount", args, kwargs))
        return self

    def zrevrange(self, *args, **kwargs):
        self.ops.append(("zrevrange", args, kwargs))
        return self

    async def execute(self):
        results = []
        for name, args, kwargs in self.ops:
            result = await getattr(self.redis, name)(*args, **kwargs)
            results.append(result)
        self.ops.clear()
        return results


class InMemoryKeyRepository:
    def __init__(self, records: dict[str, KeyRecord]) -> None:
        self.records = records
        self.calls = 0

    async def get_by_token(self, token_hash: str) -> KeyRecord | None:
        self.calls += 1
        return self.records.get(token_hash)


class InMemoryCallableTargetBindingRepository:
    def __init__(self, bindings: list[CallableTargetBindingRecord]) -> None:
        self.bindings = list(bindings)

    async def list_bindings(
        self, *, callable_key=None, scope_type=None, scope_id=None, limit=200, offset=0
    ):  # noqa: ANN001, ANN201
        items = list(self.bindings)
        if callable_key:
            items = [item for item in items if item.callable_key == callable_key]
        if scope_type:
            items = [item for item in items if item.scope_type == scope_type]
        if scope_id:
            items = [item for item in items if item.scope_id == scope_id]
        sliced = items[offset : offset + limit]
        return sliced, len(items)


class MockHTTPStreamResponse:
    def __init__(self) -> None:
        self.status_code = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        yield 'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}'
        yield 'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}'
        yield "data: [DONE]"


class MockHTTPClient:
    def __init__(self) -> None:
        self.post_calls = 0
        self.stream_calls = 0

    async def post(self, url: str, headers: dict[str, str], json: dict[str, Any], timeout: int):
        self.post_calls += 1
        if url.endswith("/chat/completions"):
            payload = {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1700000000,
                "model": json["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
            return httpx.Response(200, json=payload)

        if url.endswith("/embeddings"):
            payload = {
                "object": "list",
                "data": [{"object": "embedding", "embedding": [0.1, 0.2], "index": 0}],
                "model": json["model"],
                "usage": {"prompt_tokens": 2, "completion_tokens": 0, "total_tokens": 2},
            }
            return httpx.Response(200, json=payload)

        return httpx.Response(404, json={"error": "not found"})

    def stream(
        self, method: str, url: str, headers: dict[str, str], json: dict[str, Any], timeout: int
    ):
        self.stream_calls += 1
        return MockHTTPStreamResponse()


@pytest.fixture
async def test_app() -> FastAPI:
    app = create_app()
    redis = FakeRedis()
    salt = "test-salt"
    raw_key = "sk-test"
    token_hash = hashlib.sha256(f"{salt}:{raw_key}".encode("utf-8")).hexdigest()

    record = KeyRecord(
        token=token_hash,
        team_id="team-default",
        organization_id="org-default",
        models=["gpt-4o-mini", "text-embedding-3-small"],
        rpm_limit=2,
        tpm_limit=10000,
        max_parallel_requests=5,
        expires=datetime.now(tz=UTC) + timedelta(hours=1),
    )
    repo = InMemoryKeyRepository(records={token_hash: record})
    mock_http = MockHTTPClient()

    app.state.redis = redis
    app.state.settings = type("Settings", (), {"openai_base_url": "https://api.openai.com/v1"})()
    app.state.key_service = KeyService(repository=repo, redis_client=redis, salt=salt)
    app.state.limit_counter = LimitCounter(redis_client=redis)
    app.state.callable_target_grant_service = CallableTargetGrantService(
        repository=InMemoryCallableTargetBindingRepository(
            [
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-default-1",
                    callable_key="gpt-4o-mini",
                    scope_type="organization",
                    scope_id="org-default",
                    enabled=True,
                ),
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-default-2",
                    callable_key="text-embedding-3-small",
                    scope_type="organization",
                    scope_id="org-default",
                    enabled=True,
                ),
            ]
        ),
        policy_repository=None,
    )
    await app.state.callable_target_grant_service.reload()
    app.state.model_registry = {
        "gpt-4o-mini": [
            {"deltallm_params": {"model": "openai/gpt-4o-mini", "api_key": "provider-key"}}
        ],
        "text-embedding-3-small": [
            {
                "deltallm_params": {
                    "model": "openai/text-embedding-3-small",
                    "api_key": "provider-key",
                },
                "model_info": {"mode": "embedding"},
            }
        ],
    }
    app.state.http_client = mock_http
    app.state.openai_adapter = OpenAIAdapter(mock_http)  # type: ignore[arg-type]
    app.state.azure_openai_adapter = AzureOpenAIAdapter(mock_http)  # type: ignore[arg-type]
    app.state.anthropic_adapter = AnthropicAdapter(mock_http)  # type: ignore[arg-type]
    app.state.gemini_adapter = GeminiAdapter(mock_http)  # type: ignore[arg-type]
    app.state.bedrock_adapter = BedrockAdapter(mock_http)  # type: ignore[arg-type]
    app.state.provider_error_mapper_registry = ProviderErrorMapperRegistry(
        openai=app.state.openai_adapter,
        azure_openai=app.state.azure_openai_adapter,
        anthropic=app.state.anthropic_adapter,
        gemini=app.state.gemini_adapter,
        bedrock=app.state.bedrock_adapter,
    )
    app.state.app_config = type(
        "Cfg",
        (),
        {
            "router_settings": type("RouterCfg", (), {"num_retries": 0})(),
            "general_settings": type(
                "GeneralCfg",
                (),
                {
                    "callable_target_scope_policy_mode": "enforce",
                    "spend_reporting_v2_enabled": True,
                },
            )(),
        },
    )()

    state_backend = RedisStateBackend(redis)
    deployment_registry = build_deployment_registry(app.state.model_registry)
    router = Router(
        strategy=RoutingStrategy.SIMPLE_SHUFFLE,
        state_backend=state_backend,
        config=RouterConfig(),
        deployment_registry=deployment_registry,
    )
    cooldown_manager = CooldownManager(state_backend=state_backend)
    failover_manager = FailoverManager(
        config=FallbackConfig(),
        candidate_planner=router,
        state_backend=state_backend,
        cooldown_manager=cooldown_manager,
    )

    app.state.router_state_backend = state_backend
    app.state.router = router
    app.state.cooldown_manager = cooldown_manager
    app.state.failover_manager = failover_manager
    app.state.routing_runtime_generation_store = RoutingRuntimeGenerationStore(
        RoutingRuntimeGeneration.create(
            revision=0,
            app_config=app.state.app_config,
            model_registry=app.state.model_registry,
            route_groups=[],
            callable_target_catalog=build_callable_target_catalog(app.state.model_registry),
            authorization_snapshot=app.state.callable_target_grant_service.snapshot(),
            deployment_registry=router.deployment_registry,
            strategy=router.strategy,
            router_config=router.config,
            failover_config=failover_manager.config,
            salt_key="",
            router=router,
            failover_manager=failover_manager,
            cooldown_manager=cooldown_manager,
        )
    )
    app.state.router_health_handler = HealthEndpointHandler(
        deployment_registry=deployment_registry,
        state_backend=state_backend,
    )
    app.state.guardrail_registry = GuardrailRegistry()
    app.state.guardrail_middleware = GuardrailMiddleware(
        registry=app.state.guardrail_registry, cache_backend=redis
    )
    app.state.budget_service = NoopBudgetService()
    app.state.spend_tracking_service = NoopSpendTrackingService()

    app.state._test_key = raw_key
    app.state._test_repo = repo
    return app


@pytest.fixture
async def client(test_app: FastAPI):
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
