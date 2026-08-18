from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter
import json
import logging
import time
from typing import Any, TypeVar
from uuid import uuid4

from src.db.prompt_registry import (
    PromptBindingRecord,
    PromptRegistryRepository,
    PromptResolvedRecord,
)
from src.db.route_groups import RouteGroupRepository
from src.metrics import (
    increment_prompt_cache_lookup,
    increment_prompt_resolution,
    observe_prompt_resolution_latency,
)
from src.services.asset_scopes import (
    normalize_scope_type,
    prompt_binding_resolution_chain,
    scope_lookup_candidates,
)
from src.services.prompt_rendering import render_template_body, validate_variables_schema
from src.services.prompt_singleflight import PromptSingleflight
from src.services.runtime_scopes import RuntimeScopeContext
from src.telemetry.prompt_render import PromptRenderEvent, PromptRenderSink

logger = logging.getLogger(__name__)

PROMPT_CACHE_PREFIX = "deltallm:prompt:v2"
PROMPT_BINDING_CACHE_PREFIX = "deltallm:promptbinding:v2"
PROMPT_GROUP_DEFAULT_CACHE_PREFIX = "deltallm:promptgroupdefault:v2"
PROMPT_NAMESPACE_EPOCH_KEY = "deltallm:prompt-cache-epoch:v2"
_NEGATIVE_CACHE_STATE = "miss"
_CACHE_STATE_KEY = "_deltallm_cache_state"
_CACHE_FORMAT_VERSION = 2
_SingleFlightResult = TypeVar("_SingleFlightResult")


@dataclass(frozen=True)
class PromptReference:
    template_key: str
    label: str | None = None
    version: int | None = None
    variables: dict[str, Any] | None = None


@dataclass(frozen=True)
class PromptProvenance:
    source: str
    template_key: str
    version: int
    label: str | None
    binding_scope: str | None = None
    binding_scope_id: str | None = None
    route_preferences: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source": self.source,
            "template_key": self.template_key,
            "version": self.version,
            "label": self.label,
            "binding_scope": self.binding_scope,
            "binding_scope_id": self.binding_scope_id,
        }
        if self.route_preferences is not None:
            payload["route_preferences"] = dict(self.route_preferences)
        return payload


@dataclass(frozen=True)
class PromptRenderOutput:
    messages: list[dict[str, Any]]
    provenance: PromptProvenance
    rendered_prompt: Any


@dataclass
class _CacheEntry:
    value: dict[str, Any]
    expires_at: float


class _BoundedTTLCache:
    def __init__(self, max_entries: int) -> None:
        self.max_entries = max(1, int(max_entries))
        self._entries: OrderedDict[str, _CacheEntry] = OrderedDict()

    def get(self, key: str) -> _CacheEntry | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= time.monotonic():
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return entry

    def set(self, key: str, entry: _CacheEntry) -> None:
        self._prune_expired()
        self._entries[key] = entry
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def pop(self, key: str, default: Any = None) -> Any:
        del default
        self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()

    def keys(self) -> tuple[str, ...]:
        self._prune_expired()
        return tuple(self._entries.keys())

    def __len__(self) -> int:
        self._prune_expired()
        return len(self._entries)

    def _prune_expired(self) -> None:
        now = time.monotonic()
        expired = [key for key, entry in self._entries.items() if entry.expires_at <= now]
        for key in expired:
            self._entries.pop(key, None)


@dataclass(frozen=True)
class _PromptLookupResult:
    prompt: PromptResolvedRecord
    cache_tier: str


@dataclass(frozen=True)
class _GroupDefaultPrompt:
    template_key: str
    label: str | None = None


class PromptRegistryService:
    def __init__(
        self,
        repository: PromptRegistryRepository,
        route_group_repository: RouteGroupRepository | None = None,
        redis_client: Any | None = None,
        render_log_sink: PromptRenderSink | None = None,
        *,
        l1_ttl_seconds: int = 30,
        l2_ttl_seconds: int = 300,
        negative_cache_enabled: bool = True,
        negative_l1_ttl_seconds: int = 5,
        negative_l2_ttl_seconds: int = 30,
        l1_max_entries: int = 10_000,
        singleflight_max_keys: int = 256,
        singleflight_timeout_seconds: float = 2.0,
    ) -> None:
        self.repository = repository
        self.route_group_repository = route_group_repository
        self.redis = redis_client
        self.l1_ttl_seconds = max(1, int(l1_ttl_seconds))
        self.l2_ttl_seconds = max(1, int(l2_ttl_seconds))
        self.negative_cache_enabled = bool(negative_cache_enabled)
        self.negative_l1_ttl_seconds = max(1, int(negative_l1_ttl_seconds))
        self.negative_l2_ttl_seconds = max(1, int(negative_l2_ttl_seconds))
        self._prompt_l1 = _BoundedTTLCache(l1_max_entries)
        self._binding_l1 = _BoundedTTLCache(l1_max_entries)
        self._group_default_l1 = _BoundedTTLCache(l1_max_entries)
        self._singleflight = PromptSingleflight(
            max_keys=singleflight_max_keys,
            timeout_seconds=singleflight_timeout_seconds,
        )
        self._cache_generation = 0
        self._namespace_epoch = 1
        self._namespace_epoch_loaded = redis_client is None
        self._namespace_epoch_lock = asyncio.Lock()
        self._render_log_sink = render_log_sink

    @property
    def _inflight(self) -> dict[str, asyncio.Task[Any]]:
        """Compatibility inspection surface for focused cache tests."""

        return self._singleflight.tasks

    async def shutdown(self) -> None:
        await self._singleflight.shutdown()

    def configure_cache(
        self,
        *,
        l1_ttl_seconds: int,
        l2_ttl_seconds: int,
        negative_cache_enabled: bool,
        negative_l1_ttl_seconds: int,
        negative_l2_ttl_seconds: int,
        l1_max_entries: int,
    ) -> None:
        normalized_max = max(1, int(l1_max_entries))
        changed = (
            self.l1_ttl_seconds != max(1, int(l1_ttl_seconds))
            or self.l2_ttl_seconds != max(1, int(l2_ttl_seconds))
            or self.negative_cache_enabled != bool(negative_cache_enabled)
            or self.negative_l1_ttl_seconds != max(1, int(negative_l1_ttl_seconds))
            or self.negative_l2_ttl_seconds != max(1, int(negative_l2_ttl_seconds))
            or self._prompt_l1.max_entries != normalized_max
        )
        self.l1_ttl_seconds = max(1, int(l1_ttl_seconds))
        self.l2_ttl_seconds = max(1, int(l2_ttl_seconds))
        self.negative_cache_enabled = bool(negative_cache_enabled)
        self.negative_l1_ttl_seconds = max(1, int(negative_l1_ttl_seconds))
        self.negative_l2_ttl_seconds = max(1, int(negative_l2_ttl_seconds))
        for cache in (self._prompt_l1, self._binding_l1, self._group_default_l1):
            cache.max_entries = normalized_max
        if changed:
            self._invalidate_local_caches()

    async def resolve_and_render(
        self,
        *,
        explicit_reference: PromptReference | None,
        variables: dict[str, Any],
        api_key: str | None,
        user_id: str | None,
        team_id: str | None,
        organization_id: str | None,
        route_group_key: str | None,
        model: str | None,
        request_id: str | None,
        client_ip: str | None = None,
        user_agent: str | None = None,
        scope_context: RuntimeScopeContext | None = None,
    ) -> PromptRenderOutput | None:
        await self._ensure_namespace_epoch()
        started = perf_counter()
        selected: tuple[PromptResolvedRecord, PromptProvenance, str] | None = None

        if explicit_reference is not None:
            lookup = await self._resolve_prompt(
                template_key=explicit_reference.template_key,
                label=explicit_reference.label,
                version=explicit_reference.version,
            )
            if lookup is None:
                await self._log_render(
                    request_id=request_id,
                    api_key=api_key,
                    user_id=user_id,
                    team_id=team_id,
                    organization_id=organization_id,
                    route_group_key=route_group_key,
                    model=model,
                    prompt=None,
                    label=explicit_reference.label,
                    status="error",
                    latency_ms=int((perf_counter() - started) * 1000),
                    error_code="prompt_not_found",
                    error_message="Explicit prompt reference could not be resolved",
                    variables=variables,
                    metadata={"source": "explicit"},
                    client_ip=client_ip,
                    user_agent=user_agent,
                )
                increment_prompt_resolution(
                    source="explicit",
                    status="not_found",
                    binding_scope=None,
                    label=explicit_reference.label,
                )
                observe_prompt_resolution_latency(
                    source="explicit",
                    status="not_found",
                    latency_seconds=perf_counter() - started,
                )
                raise ValueError("Prompt reference could not be resolved")
            resolved = lookup.prompt
            selected = (
                resolved,
                PromptProvenance(
                    source="explicit",
                    template_key=resolved.template_key,
                    version=resolved.version,
                    label=resolved.label or explicit_reference.label,
                    route_preferences=_safe_normalize_route_preferences(resolved.route_preferences),
                ),
                lookup.cache_tier,
            )
        else:
            selected = await self._resolve_from_bindings(
                scope_context=scope_context,
                api_key=api_key,
                user_id=user_id,
                team_id=team_id,
                organization_id=organization_id,
                route_group_key=route_group_key,
            )

        if selected is None:
            increment_prompt_resolution(
                source="none", status="no_prompt", binding_scope=None, label=None
            )
            observe_prompt_resolution_latency(
                source="none", status="no_prompt", latency_seconds=perf_counter() - started
            )
            return None

        resolved, provenance, _cache_tier = selected
        schema_errors = validate_variables_schema(resolved.variables_schema, variables)
        if schema_errors:
            await self._log_render(
                request_id=request_id,
                api_key=api_key,
                user_id=user_id,
                team_id=team_id,
                organization_id=organization_id,
                route_group_key=route_group_key,
                model=model,
                prompt=resolved,
                label=provenance.label,
                status="error",
                latency_ms=int((perf_counter() - started) * 1000),
                error_code="variables_invalid",
                error_message="; ".join(schema_errors),
                variables=variables,
                metadata=provenance.to_dict(),
                client_ip=client_ip,
                user_agent=user_agent,
            )
            increment_prompt_resolution(
                source=provenance.source,
                status="validation_error",
                binding_scope=provenance.binding_scope,
                label=provenance.label,
            )
            observe_prompt_resolution_latency(
                source=provenance.source,
                status="validation_error",
                latency_seconds=perf_counter() - started,
            )
            raise ValueError("; ".join(schema_errors))

        try:
            rendered = render_template_body(resolved.template_body, variables)
            messages = _to_system_messages(rendered)
        except ValueError as exc:
            await self._log_render(
                request_id=request_id,
                api_key=api_key,
                user_id=user_id,
                team_id=team_id,
                organization_id=organization_id,
                route_group_key=route_group_key,
                model=model,
                prompt=resolved,
                label=provenance.label,
                status="error",
                latency_ms=int((perf_counter() - started) * 1000),
                error_code="render_invalid",
                error_message=str(exc),
                variables=variables,
                metadata=provenance.to_dict(),
                client_ip=client_ip,
                user_agent=user_agent,
            )
            increment_prompt_resolution(
                source=provenance.source,
                status="render_error",
                binding_scope=provenance.binding_scope,
                label=provenance.label,
            )
            observe_prompt_resolution_latency(
                source=provenance.source,
                status="render_error",
                latency_seconds=perf_counter() - started,
            )
            raise

        elapsed_ms = int((perf_counter() - started) * 1000)
        await self._log_render(
            request_id=request_id,
            api_key=api_key,
            user_id=user_id,
            team_id=team_id,
            organization_id=organization_id,
            route_group_key=route_group_key,
            model=model,
            prompt=resolved,
            label=provenance.label,
            status="success",
            latency_ms=elapsed_ms,
            error_code=None,
            error_message=None,
            variables=variables,
            metadata=provenance.to_dict(),
            client_ip=client_ip,
            user_agent=user_agent,
        )
        increment_prompt_resolution(
            source=provenance.source,
            status="success",
            binding_scope=provenance.binding_scope,
            label=provenance.label,
        )
        observe_prompt_resolution_latency(
            source=provenance.source,
            status="success",
            latency_seconds=perf_counter() - started,
        )
        return PromptRenderOutput(
            messages=messages, provenance=provenance, rendered_prompt=rendered
        )

    async def dry_run_render(
        self,
        *,
        template_key: str,
        label: str | None,
        version: int | None,
        variables: dict[str, Any],
    ) -> dict[str, Any]:
        await self._ensure_namespace_epoch()
        lookup = await self._resolve_prompt(template_key=template_key, label=label, version=version)
        if lookup is None:
            raise ValueError("Prompt reference could not be resolved")
        resolved = lookup.prompt
        schema_errors = validate_variables_schema(resolved.variables_schema, variables)
        if schema_errors:
            raise ValueError("; ".join(schema_errors))
        rendered = render_template_body(resolved.template_body, variables)
        return {
            "template_key": resolved.template_key,
            "version": resolved.version,
            "label": resolved.label or label,
            "rendered_prompt": rendered,
            "messages": _to_system_messages(rendered),
            "route_preferences": _safe_normalize_route_preferences(resolved.route_preferences),
            "provenance": PromptProvenance(
                source="dry_run",
                template_key=resolved.template_key,
                version=resolved.version,
                label=resolved.label or label,
                route_preferences=_safe_normalize_route_preferences(resolved.route_preferences),
            ).to_dict(),
            "cache_tier": lookup.cache_tier,
        }

    async def resolve_binding_preview(
        self,
        *,
        api_key: str | None = None,
        user_id: str | None = None,
        team_id: str | None = None,
        organization_id: str | None = None,
        route_group_key: str | None = None,
        scope_context: RuntimeScopeContext | None = None,
    ) -> dict[str, Any]:
        await self._ensure_namespace_epoch()
        checks = prompt_binding_resolution_chain(
            scope_context=scope_context,
            api_key=api_key,
            user_id=user_id,
            team_id=team_id,
            organization_id=organization_id,
            route_group_key=route_group_key,
        )
        candidates: list[dict[str, Any]] = []
        chosen: dict[str, Any] | None = None
        for scope_type, scope_id in checks:
            binding = await self._resolve_binding(scope_type=scope_type, scope_id=scope_id)
            if binding is None:
                continue
            resolved_scope_type = normalize_scope_type(binding.scope_type)
            candidate = {
                "scope_type": resolved_scope_type,
                "scope_id": binding.scope_id,
                "template_key": binding.template_key,
                "label": binding.label,
                "priority": binding.priority,
            }
            candidates.append(candidate)
            if chosen is None:
                chosen = candidate
        if route_group_key:
            default_prompt = await self._resolve_route_group_default(route_group_key)
            if default_prompt is not None:
                candidate = {
                    "scope_type": "group_default",
                    "scope_id": route_group_key,
                    "template_key": default_prompt.template_key,
                    "label": default_prompt.label or "production",
                    "priority": None,
                }
                candidates.append(candidate)
                if chosen is None:
                    chosen = candidate
        return {"winner": chosen, "candidates": candidates}

    async def invalidate_template(self, template_key: str) -> None:
        del template_key
        await self._bump_namespace_epoch()

    async def invalidate_scope(self, *, scope_type: str, scope_id: str) -> None:
        del scope_type, scope_id
        await self._bump_namespace_epoch()

    async def invalidate_all(self) -> None:
        await self._bump_namespace_epoch()

    async def refresh_namespace_epoch(self) -> None:
        previous_epoch = self._namespace_epoch
        self._namespace_epoch_loaded = False
        await self._ensure_namespace_epoch()
        if self._namespace_epoch != previous_epoch:
            self._invalidate_local_caches()

    def _invalidate_local_caches(self) -> None:
        self._cache_generation += 1
        self._prompt_l1.clear()
        self._binding_l1.clear()
        self._group_default_l1.clear()
        # In-flight lookups may finish for their current callers. The generation
        # fence prevents them from repopulating caches after this invalidation.

    async def _ensure_namespace_epoch(self) -> None:
        if self._namespace_epoch_loaded or self.redis is None:
            return
        async with self._namespace_epoch_lock:
            if self._namespace_epoch_loaded:
                return
            try:
                raw = await self.redis.get(PROMPT_NAMESPACE_EPOCH_KEY)
                if raw is None:
                    await self.redis.set(PROMPT_NAMESPACE_EPOCH_KEY, "1", nx=True)
                    raw = await self.redis.get(PROMPT_NAMESPACE_EPOCH_KEY)
                self._namespace_epoch = max(1, int(raw or 1))
            except Exception as exc:
                logger.debug("failed reading prompt cache namespace epoch: %s", exc)
            self._namespace_epoch_loaded = True

    async def _bump_namespace_epoch(self) -> None:
        next_epoch = self._namespace_epoch + 1
        if self.redis is not None:
            try:
                next_epoch = max(1, int(await self.redis.incr(PROMPT_NAMESPACE_EPOCH_KEY)))
            except Exception as exc:
                logger.warning("failed bumping prompt cache namespace epoch: %s", exc)
        self._namespace_epoch = next_epoch
        self._namespace_epoch_loaded = True
        self._invalidate_local_caches()

    async def _resolve_from_bindings(
        self,
        *,
        scope_context: RuntimeScopeContext | None = None,
        api_key: str | None,
        user_id: str | None,
        team_id: str | None,
        organization_id: str | None,
        route_group_key: str | None,
    ) -> tuple[PromptResolvedRecord, PromptProvenance, str] | None:
        precedence = prompt_binding_resolution_chain(
            scope_context=scope_context,
            api_key=api_key,
            user_id=user_id,
            team_id=team_id,
            organization_id=organization_id,
            route_group_key=route_group_key,
        )
        bindings = await self._resolve_binding_chain(precedence)
        for binding in bindings:
            if binding is None:
                continue
            lookup = await self._resolve_prompt(
                template_key=binding.template_key,
                label=binding.label,
                version=None,
            )
            if lookup is None:
                continue
            resolved = lookup.prompt
            return (
                resolved,
                PromptProvenance(
                    source="binding",
                    template_key=resolved.template_key,
                    version=resolved.version,
                    label=binding.label,
                    binding_scope=normalize_scope_type(binding.scope_type),
                    binding_scope_id=binding.scope_id,
                    route_preferences=_safe_normalize_route_preferences(resolved.route_preferences),
                ),
                lookup.cache_tier,
            )
        if route_group_key:
            default_prompt = await self._resolve_route_group_default(route_group_key)
            if default_prompt is not None:
                lookup = await self._resolve_prompt(
                    template_key=default_prompt.template_key,
                    label=default_prompt.label,
                    version=None,
                )
                if lookup is not None:
                    resolved = lookup.prompt
                    return (
                        resolved,
                        PromptProvenance(
                            source="group_default",
                            template_key=resolved.template_key,
                            version=resolved.version,
                            label=default_prompt.label or resolved.label,
                            binding_scope="group",
                            binding_scope_id=route_group_key,
                            route_preferences=_safe_normalize_route_preferences(
                                resolved.route_preferences
                            ),
                        ),
                        lookup.cache_tier,
                    )
        return None

    async def _resolve_binding_chain(
        self,
        precedence: list[tuple[str, str]],
    ) -> list[PromptBindingRecord | None]:
        if not precedence:
            return []
        cached = self._read_binding_chain_l1(precedence)
        if cached is not None:
            return cached
        key = "binding-chain:" + json.dumps(precedence, separators=(",", ":"))
        return await self._run_singleflight(
            key,
            lambda: self._resolve_binding_chain_cold(precedence),
        )

    def _read_binding_chain_l1(
        self,
        precedence: list[tuple[str, str]],
    ) -> list[PromptBindingRecord | None] | None:
        resolved: list[PromptBindingRecord | None] = []
        for scope_type, scope_id in precedence:
            cache_key = self._binding_cache_key(scope_type, scope_id)
            payload = self._read_l1(self._binding_l1, cache_key)
            if payload is None:
                return None
            if _is_negative_cache_payload(payload):
                increment_prompt_cache_lookup(entity="binding", tier="negative_l1")
                resolved.append(None)
            else:
                increment_prompt_cache_lookup(entity="binding", tier="l1")
                resolved.append(_binding_from_cache(payload))
        return resolved

    async def _resolve_binding_chain_cold(
        self,
        precedence: list[tuple[str, str]],
    ) -> list[PromptBindingRecord | None]:
        generation = self._cache_generation
        payloads: dict[str, dict[str, Any]] = {}
        missing_keys: list[str] = []
        scope_by_key: dict[str, tuple[str, str]] = {}

        for scope_type, scope_id in precedence:
            cache_key = self._binding_cache_key(scope_type, scope_id)
            scope_by_key[cache_key] = (normalize_scope_type(scope_type), scope_id)
            payload = self._read_l1(self._binding_l1, cache_key)
            if payload is None:
                missing_keys.append(cache_key)
            else:
                payloads[cache_key] = payload

        if missing_keys:
            l2_payloads = await self._read_l2_many(missing_keys)
            still_missing: list[str] = []
            for cache_key in missing_keys:
                payload = l2_payloads.get(cache_key)
                if payload is None:
                    still_missing.append(cache_key)
                    continue
                payloads[cache_key] = payload
                if generation == self._cache_generation:
                    self._write_l1(self._binding_l1, cache_key, payload)

            if still_missing:
                unresolved_scopes = [scope_by_key[key] for key in still_missing]
                bindings = await self._query_binding_chain(unresolved_scopes)
                bindings_by_scope = {
                    (normalize_scope_type(binding.scope_type), binding.scope_id): binding
                    for binding in bindings
                }
                for cache_key in still_missing:
                    scope = scope_by_key[cache_key]
                    binding = bindings_by_scope.get(scope)
                    payload = (
                        _binding_to_cache(binding)
                        if binding is not None
                        else _negative_cache_payload()
                    )
                    payloads[cache_key] = payload
                    if generation != self._cache_generation:
                        continue
                    if binding is not None or self.negative_cache_enabled:
                        self._write_l1(self._binding_l1, cache_key, payload)
                        await self._write_l2(cache_key, payload)

        resolved: list[PromptBindingRecord | None] = []
        for scope_type, scope_id in precedence:
            payload = payloads[self._binding_cache_key(scope_type, scope_id)]
            if _is_negative_cache_payload(payload):
                increment_prompt_cache_lookup(entity="binding", tier="db_miss")
                resolved.append(None)
            else:
                increment_prompt_cache_lookup(entity="binding", tier="db")
                resolved.append(_binding_from_cache(payload))
        return resolved

    async def _query_binding_chain(
        self,
        scopes: list[tuple[str, str]],
    ) -> list[PromptBindingRecord]:
        resolver = getattr(self.repository, "resolve_binding_chain", None)
        if callable(resolver):
            return list(await resolver(scopes=scopes))

        # Compatibility path for custom repositories. The production repository
        # implements the batched method above.
        resolved: list[PromptBindingRecord] = []
        for scope_type, scope_id in scopes:
            binding = None
            for candidate_scope_type in scope_lookup_candidates(scope_type):
                binding = await self.repository.resolve_binding(
                    scope_type=candidate_scope_type,
                    scope_id=scope_id,
                )
                if binding is not None:
                    break
            if binding is not None:
                resolved.append(binding)
        return resolved

    async def _resolve_prompt(
        self,
        *,
        template_key: str,
        label: str | None,
        version: int | None,
    ) -> _PromptLookupResult | None:
        cache_key = self._prompt_cache_key(template_key=template_key, label=label, version=version)
        cached = self._read_l1(self._prompt_l1, cache_key)
        if cached is not None:
            if _is_negative_cache_payload(cached):
                increment_prompt_cache_lookup(entity="prompt", tier="negative_l1")
                return None
            increment_prompt_cache_lookup(entity="prompt", tier="l1")
            return _PromptLookupResult(prompt=_prompt_from_cache(cached), cache_tier="l1")

        return await self._run_singleflight(
            f"prompt:{cache_key}",
            lambda: self._resolve_prompt_cold(
                cache_key=cache_key,
                template_key=template_key,
                label=label,
                version=version,
            ),
        )

    async def _resolve_prompt_cold(
        self,
        *,
        cache_key: str,
        template_key: str,
        label: str | None,
        version: int | None,
    ) -> _PromptLookupResult | None:
        generation = self._cache_generation
        cached = self._read_l1(self._prompt_l1, cache_key)
        if cached is not None:
            if _is_negative_cache_payload(cached):
                increment_prompt_cache_lookup(entity="prompt", tier="negative_l1")
                return None
            increment_prompt_cache_lookup(entity="prompt", tier="l1")
            return _PromptLookupResult(prompt=_prompt_from_cache(cached), cache_tier="l1")

        l2_cached = await self._read_l2(cache_key)
        if l2_cached is not None:
            if generation == self._cache_generation:
                self._write_l1(self._prompt_l1, cache_key, l2_cached)
            if _is_negative_cache_payload(l2_cached):
                increment_prompt_cache_lookup(entity="prompt", tier="negative_l2")
                return None
            increment_prompt_cache_lookup(entity="prompt", tier="l2")
            return _PromptLookupResult(prompt=_prompt_from_cache(l2_cached), cache_tier="l2")

        resolved = await self.repository.resolve_prompt(
            template_key=template_key, label=label, version=version
        )
        if resolved is None:
            if self.negative_cache_enabled and generation == self._cache_generation:
                payload = _negative_cache_payload()
                self._write_l1(self._prompt_l1, cache_key, payload)
                await self._write_l2(cache_key, payload)
            increment_prompt_cache_lookup(entity="prompt", tier="db_miss")
            return None
        payload = _prompt_to_cache(resolved)
        if generation == self._cache_generation:
            self._write_l1(self._prompt_l1, cache_key, payload)
            await self._write_l2(cache_key, payload)
        increment_prompt_cache_lookup(entity="prompt", tier="db")
        return _PromptLookupResult(prompt=resolved, cache_tier="db")

    async def _resolve_binding(
        self, *, scope_type: str, scope_id: str
    ) -> PromptBindingRecord | None:
        resolved = await self._resolve_binding_chain([(scope_type, scope_id)])
        return resolved[0] if resolved else None

    async def _resolve_route_group_default(
        self, route_group_key: str
    ) -> _GroupDefaultPrompt | None:
        if self.route_group_repository is None:
            return None
        cache_key = self._group_default_cache_key(route_group_key)
        cached = self._read_l1(self._group_default_l1, cache_key)
        if cached is not None:
            if _is_negative_cache_payload(cached):
                increment_prompt_cache_lookup(entity="group_default", tier="negative_l1")
                return None
            increment_prompt_cache_lookup(entity="group_default", tier="l1")
            return _group_default_from_cache(cached)

        return await self._run_singleflight(
            f"group-default:{cache_key}",
            lambda: self._resolve_route_group_default_cold(route_group_key, cache_key),
        )

    async def _resolve_route_group_default_cold(
        self,
        route_group_key: str,
        cache_key: str,
    ) -> _GroupDefaultPrompt | None:
        generation = self._cache_generation
        cached = self._read_l1(self._group_default_l1, cache_key)
        if cached is not None:
            if _is_negative_cache_payload(cached):
                increment_prompt_cache_lookup(entity="group_default", tier="negative_l1")
                return None
            increment_prompt_cache_lookup(entity="group_default", tier="l1")
            return _group_default_from_cache(cached)

        l2_cached = await self._read_l2(cache_key)
        if l2_cached is not None:
            if generation == self._cache_generation:
                self._write_l1(self._group_default_l1, cache_key, l2_cached)
            if _is_negative_cache_payload(l2_cached):
                increment_prompt_cache_lookup(entity="group_default", tier="negative_l2")
                return None
            increment_prompt_cache_lookup(entity="group_default", tier="l2")
            return _group_default_from_cache(l2_cached)

        resolved = await self.route_group_repository.get_default_prompt(route_group_key)
        if resolved is None:
            if self.negative_cache_enabled and generation == self._cache_generation:
                payload = _negative_cache_payload()
                self._write_l1(self._group_default_l1, cache_key, payload)
                await self._write_l2(cache_key, payload)
            increment_prompt_cache_lookup(entity="group_default", tier="db_miss")
            return None
        payload = _group_default_to_cache(resolved)
        if generation == self._cache_generation:
            self._write_l1(self._group_default_l1, cache_key, payload)
            await self._write_l2(cache_key, payload)
        increment_prompt_cache_lookup(entity="group_default", tier="db")
        return _group_default_from_cache(payload)

    async def _run_singleflight(
        self,
        key: str,
        factory: Callable[[], Awaitable[_SingleFlightResult]],
    ) -> _SingleFlightResult:
        return await self._singleflight.run(key, factory)

    def _read_l1(self, cache: _BoundedTTLCache, key: str) -> dict[str, Any] | None:
        entry = cache.get(key)
        if entry is None:
            return None
        return dict(entry.value)

    def _write_l1(self, cache: _BoundedTTLCache, key: str, value: dict[str, Any]) -> None:
        ttl = (
            self.negative_l1_ttl_seconds
            if _is_negative_cache_payload(value)
            else self.l1_ttl_seconds
        )
        cache.set(
            key,
            _CacheEntry(
                value=dict(value),
                expires_at=time.monotonic() + ttl,
            ),
        )

    def _clear_l1_by_prefix(self, prefix: str) -> None:
        keys = [key for key in self._prompt_l1.keys() if key.startswith(prefix)]
        for key in keys:
            self._prompt_l1.pop(key, None)

    async def _read_l2(self, key: str) -> dict[str, Any] | None:
        if self.redis is None:
            return None
        try:
            raw = await self.redis.get(key)
            if not raw:
                return None
            payload = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        except Exception as exc:
            logger.debug("failed to read prompt cache key=%s: %s", key, exc)
            return None
        return payload if isinstance(payload, dict) else None

    async def _read_l2_many(self, keys: list[str]) -> dict[str, dict[str, Any]]:
        if self.redis is None or not keys:
            return {}
        try:
            raw_values = await self.redis.mget(keys)
        except Exception as exc:
            logger.debug("failed to read prompt cache keys=%s: %s", keys, exc)
            return {}
        resolved: dict[str, dict[str, Any]] = {}
        for key, raw in zip(keys, raw_values, strict=False):
            if not raw:
                continue
            try:
                payload = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
                continue
            if isinstance(payload, dict):
                resolved[key] = payload
        return resolved

    async def _write_l2(self, key: str, payload: dict[str, Any]) -> None:
        if self.redis is None:
            return
        try:
            ttl = (
                self.negative_l2_ttl_seconds
                if _is_negative_cache_payload(payload)
                else self.l2_ttl_seconds
            )
            await self.redis.setex(key, ttl, json.dumps(payload))
        except Exception as exc:
            logger.debug("failed to write prompt cache key=%s: %s", key, exc)

    async def _delete_l2_keys(self, keys: list[str]) -> None:
        if self.redis is None:
            return
        unique = [key for key in set(keys) if key]
        if not unique:
            return
        try:
            await self.redis.delete(*unique)
        except Exception as exc:
            logger.debug("failed to delete prompt cache keys=%s: %s", unique, exc)

    def _prompt_cache_key(
        self, *, template_key: str, label: str | None, version: int | None
    ) -> str:
        prefix = f"{PROMPT_CACHE_PREFIX}:e{self._namespace_epoch}"
        if version is not None:
            return f"{prefix}:{template_key}:version:{version}"
        return f"{prefix}:{template_key}:label:{label or 'production'}"

    def _binding_cache_key(self, scope_type: str, scope_id: str) -> str:
        return (
            f"{PROMPT_BINDING_CACHE_PREFIX}:e{self._namespace_epoch}:"
            f"{normalize_scope_type(scope_type)}:{scope_id}"
        )

    def _group_default_cache_key(self, route_group_key: str) -> str:
        return f"{PROMPT_GROUP_DEFAULT_CACHE_PREFIX}:e{self._namespace_epoch}:{route_group_key}"

    async def _log_render(
        self,
        *,
        request_id: str | None,
        api_key: str | None,
        user_id: str | None,
        team_id: str | None,
        organization_id: str | None,
        route_group_key: str | None,
        model: str | None,
        prompt: PromptResolvedRecord | None,
        label: str | None,
        status: str,
        latency_ms: int,
        error_code: str | None,
        error_message: str | None,
        variables: dict[str, Any],
        metadata: dict[str, Any] | None,
        client_ip: str | None,
        user_agent: str | None,
    ) -> None:
        event = PromptRenderEvent(
            prompt_render_log_id=str(uuid4()),
            audit_event_id=str(uuid4()),
            request_id=request_id,
            api_key=api_key,
            user_id=user_id,
            team_id=team_id,
            organization_id=organization_id,
            route_group_key=route_group_key,
            model=model,
            prompt_template_id=prompt.prompt_template_id if prompt else None,
            prompt_version_id=prompt.prompt_version_id if prompt else None,
            prompt_key=prompt.template_key if prompt else None,
            label=label,
            status=status,
            latency_ms=latency_ms,
            error_code=error_code,
            error_message=error_message,
            ip=client_ip,
            user_agent=user_agent,
            variables=dict(variables),
            metadata=dict(metadata) if metadata is not None else None,
        )
        if self._render_log_sink is not None:
            await self._render_log_sink.enqueue_prompt_render(event)
            return
        await self.repository.create_render_log(**event.redacted().render_log_payload())


def _negative_cache_payload() -> dict[str, Any]:
    return {
        _CACHE_STATE_KEY: _NEGATIVE_CACHE_STATE,
        "version": _CACHE_FORMAT_VERSION,
    }


def _is_negative_cache_payload(payload: dict[str, Any]) -> bool:
    return (
        payload.get(_CACHE_STATE_KEY) == _NEGATIVE_CACHE_STATE
        and int(payload.get("version") or 0) == _CACHE_FORMAT_VERSION
    )


def _prompt_to_cache(record: PromptResolvedRecord) -> dict[str, Any]:
    return {
        "prompt_template_id": record.prompt_template_id,
        "template_key": record.template_key,
        "prompt_version_id": record.prompt_version_id,
        "version": record.version,
        "status": record.status,
        "label": record.label,
        "template_body": record.template_body,
        "variables_schema": record.variables_schema,
        "model_hints": record.model_hints,
        "route_preferences": record.route_preferences,
    }


def _prompt_from_cache(data: dict[str, Any]) -> PromptResolvedRecord:
    return PromptResolvedRecord(
        prompt_template_id=str(data.get("prompt_template_id") or ""),
        template_key=str(data.get("template_key") or ""),
        prompt_version_id=str(data.get("prompt_version_id") or ""),
        version=int(data.get("version") or 0),
        status=str(data.get("status") or ""),
        label=str(data.get("label")) if data.get("label") is not None else None,
        template_body=dict(data.get("template_body") or {}),
        variables_schema=dict(data.get("variables_schema") or {})
        if isinstance(data.get("variables_schema"), dict)
        else None,
        model_hints=dict(data.get("model_hints") or {})
        if isinstance(data.get("model_hints"), dict)
        else None,
        route_preferences=dict(data.get("route_preferences") or {})
        if isinstance(data.get("route_preferences"), dict)
        else None,
    )


def _binding_to_cache(binding: PromptBindingRecord) -> dict[str, Any]:
    return {
        "prompt_binding_id": binding.prompt_binding_id,
        "scope_type": binding.scope_type,
        "scope_id": binding.scope_id,
        "prompt_template_id": binding.prompt_template_id,
        "template_key": binding.template_key,
        "label": binding.label,
        "priority": binding.priority,
        "enabled": binding.enabled,
        "metadata": binding.metadata,
    }


def _binding_from_cache(data: dict[str, Any]) -> PromptBindingRecord:
    return PromptBindingRecord(
        prompt_binding_id=str(data.get("prompt_binding_id") or ""),
        scope_type=str(data.get("scope_type") or ""),
        scope_id=str(data.get("scope_id") or ""),
        prompt_template_id=str(data.get("prompt_template_id") or ""),
        template_key=str(data.get("template_key") or ""),
        label=str(data.get("label") or ""),
        priority=int(data.get("priority") or 0),
        enabled=bool(data.get("enabled", True)),
        metadata=dict(data.get("metadata") or {})
        if isinstance(data.get("metadata"), dict)
        else None,
        created_at=None,
        updated_at=None,
    )


def _group_default_to_cache(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "template_key": str(data.get("template_key") or ""),
        "label": str(data.get("label")).strip()
        if data.get("label") is not None and str(data.get("label")).strip()
        else None,
    }


def _group_default_from_cache(data: dict[str, Any]) -> _GroupDefaultPrompt | None:
    template_key = str(data.get("template_key") or "").strip()
    if not template_key:
        return None
    label = (
        str(data.get("label")).strip()
        if data.get("label") is not None and str(data.get("label")).strip()
        else None
    )
    return _GroupDefaultPrompt(template_key=template_key, label=label)


def _to_system_messages(rendered_prompt: Any) -> list[dict[str, Any]]:
    if isinstance(rendered_prompt, dict):
        messages = rendered_prompt.get("messages")
        if isinstance(messages, list):
            valid = [
                item
                for item in messages
                if isinstance(item, dict) and isinstance(item.get("role"), str)
            ]
            if valid:
                return valid
        text = rendered_prompt.get("text")
        if isinstance(text, str) and text.strip():
            return [{"role": "system", "content": text}]
    if isinstance(rendered_prompt, list):
        valid = [
            item
            for item in rendered_prompt
            if isinstance(item, dict) and isinstance(item.get("role"), str)
        ]
        if valid:
            return valid
    if isinstance(rendered_prompt, str) and rendered_prompt.strip():
        return [{"role": "system", "content": rendered_prompt}]
    raise ValueError("Rendered prompt must resolve to a text prompt or chat messages")


def parse_prompt_reference(value: Any) -> PromptReference | None:
    if value is None:
        return None
    if isinstance(value, str):
        token = value.strip()
        if not token:
            return None
        if "@" in token:
            key, raw_version = token.split("@", 1)
            if not key.strip():
                return None
            try:
                parsed_version = int(raw_version.strip())
            except ValueError:
                parsed_version = None
            return PromptReference(template_key=key.strip(), version=parsed_version)
        if ":" in token:
            key, label = token.split(":", 1)
            if key.strip() and label.strip():
                return PromptReference(template_key=key.strip(), label=label.strip())
        return PromptReference(template_key=token)
    if isinstance(value, dict):
        template_key = str(value.get("key") or value.get("template_key") or "").strip()
        if not template_key:
            return None
        label = value.get("label")
        version = value.get("version")
        parsed_version: int | None = None
        if version is not None:
            try:
                parsed_version = int(version)
            except (TypeError, ValueError):
                parsed_version = None
        variables = value.get("variables")
        return PromptReference(
            template_key=template_key,
            label=str(label).strip() if isinstance(label, str) and label.strip() else None,
            version=parsed_version,
            variables=variables if isinstance(variables, dict) else None,
        )
    return None


def normalize_route_preferences(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("route_preferences must be an object")

    normalized: dict[str, Any] = {}
    raw_route_group = value.get("route_group")
    if raw_route_group is not None:
        if not isinstance(raw_route_group, str) or not raw_route_group.strip():
            raise ValueError("route_preferences.route_group must be a non-empty string")
        normalized["route_group"] = raw_route_group.strip()

    raw_tags = value.get("tags")
    if raw_tags is not None:
        if not isinstance(raw_tags, list):
            raise ValueError("route_preferences.tags must be an array of non-empty strings")
        tags: list[str] = []
        for item in raw_tags:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("route_preferences.tags must be an array of non-empty strings")
            tag = item.strip()
            if tag not in tags:
                tags.append(tag)
        if tags:
            normalized["tags"] = tags

    return normalized or None


def _safe_normalize_route_preferences(value: Any) -> dict[str, Any] | None:
    try:
        return normalize_route_preferences(value)
    except ValueError as exc:
        logger.warning("invalid prompt route_preferences ignored: %s", exc)
        return None


def apply_route_preferences_to_metadata(
    metadata: dict[str, Any] | None,
    route_preferences: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    output = dict(metadata or {})
    normalized = normalize_route_preferences(route_preferences)
    if normalized is None:
        return output, None

    tags = normalized.get("tags")
    if isinstance(tags, list) and tags:
        existing = output.get("tags")
        existing_tags = (
            [item.strip() for item in existing if isinstance(item, str) and item.strip()]
            if isinstance(existing, list)
            else []
        )
        for tag in tags:
            if tag not in existing_tags:
                existing_tags.append(tag)
        output["tags"] = existing_tags

    output["prompt_route_preferences"] = dict(normalized)
    if isinstance(normalized.get("route_group"), str):
        output["prompt_route_group_hint"] = normalized["route_group"]
    return output, normalized
