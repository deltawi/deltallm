from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
import json
import logging
import time
from typing import Any

from src.db.prompt_registry import PromptBindingRecord, PromptRegistryRepository, PromptResolvedRecord
from src.db.route_groups import RouteGroupRepository
from src.metrics import increment_prompt_cache_lookup, increment_prompt_resolution, observe_prompt_resolution_latency
from src.services.prompt_rendering import render_template_body, validate_variables_schema

logger = logging.getLogger(__name__)

PROMPT_CACHE_PREFIX = "deltallm:prompt:v1"
PROMPT_BINDING_CACHE_PREFIX = "deltallm:promptbinding:v1"
PROMPT_GROUP_DEFAULT_CACHE_PREFIX = "deltallm:promptgroupdefault:v1"


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
        *,
        l1_ttl_seconds: int = 30,
        l2_ttl_seconds: int = 300,
    ) -> None:
        self.repository = repository
        self.route_group_repository = route_group_repository
        self.redis = redis_client
        self.l1_ttl_seconds = max(1, int(l1_ttl_seconds))
        self.l2_ttl_seconds = max(1, int(l2_ttl_seconds))
        self._prompt_l1: dict[str, _CacheEntry] = {}
        self._binding_l1: dict[str, _CacheEntry] = {}
        self._group_default_l1: dict[str, _CacheEntry] = {}
        self._l2_known_keys: set[str] = set()

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
    ) -> PromptRenderOutput | None:
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
                api_key=api_key,
                team_id=team_id,
                organization_id=organization_id,
                route_group_key=route_group_key,
            )

        if selected is None:
            increment_prompt_resolution(source="none", status="no_prompt", binding_scope=None, label=None)
            observe_prompt_resolution_latency(source="none", status="no_prompt", latency_seconds=perf_counter() - started)
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
        return PromptRenderOutput(messages=messages, provenance=provenance, rendered_prompt=rendered)

    async def dry_run_render(
        self,
        *,
        template_key: str,
        label: str | None,
        version: int | None,
        variables: dict[str, Any],
    ) -> dict[str, Any]:
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
        api_key: str | None,
        team_id: str | None,
        organization_id: str | None,
        route_group_key: str | None,
    ) -> dict[str, Any]:
        checks = [
            ("key", api_key),
            ("team", team_id),
            ("org", organization_id),
            ("group", route_group_key),
        ]
        candidates: list[dict[str, Any]] = []
        chosen: dict[str, Any] | None = None
        for scope_type, scope_id in checks:
            if not scope_id:
                continue
            binding = await self._resolve_binding(scope_type=scope_type, scope_id=scope_id)
            if binding is None:
                continue
            candidate = {
                "scope_type": scope_type,
                "scope_id": scope_id,
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
        self._clear_l1_by_prefix(f"{PROMPT_CACHE_PREFIX}:{template_key}:")
        keys = [key for key in list(self._l2_known_keys) if key.startswith(f"{PROMPT_CACHE_PREFIX}:{template_key}:")]
        await self._delete_l2_keys(keys)

    async def invalidate_scope(self, *, scope_type: str, scope_id: str) -> None:
        cache_key = self._binding_cache_key(scope_type, scope_id)
        self._binding_l1.pop(cache_key, None)
        extra_keys: list[str] = []
        if scope_type == "group":
            group_key = self._group_default_cache_key(scope_id)
            self._group_default_l1.pop(group_key, None)
            extra_keys.append(group_key)
        await self._delete_l2_keys([cache_key, *extra_keys])

    async def invalidate_all(self) -> None:
        self._prompt_l1.clear()
        self._binding_l1.clear()
        self._group_default_l1.clear()
        await self._delete_l2_keys(list(self._l2_known_keys))

    async def _resolve_from_bindings(
        self,
        *,
        api_key: str | None,
        team_id: str | None,
        organization_id: str | None,
        route_group_key: str | None,
    ) -> tuple[PromptResolvedRecord, PromptProvenance, str] | None:
        precedence = [
            ("key", api_key),
            ("team", team_id),
            ("org", organization_id),
            ("group", route_group_key),
        ]
        for scope_type, scope_id in precedence:
            if not scope_id:
                continue
            binding = await self._resolve_binding(scope_type=scope_type, scope_id=scope_id)
            if binding is None:
                continue
            lookup = await self._resolve_prompt(template_key=binding.template_key, label=binding.label, version=None)
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
                    binding_scope=scope_type,
                    binding_scope_id=scope_id,
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
                            route_preferences=_safe_normalize_route_preferences(resolved.route_preferences),
                        ),
                        lookup.cache_tier,
                    )
        return None

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
            increment_prompt_cache_lookup(entity="prompt", tier="l1")
            return _PromptLookupResult(prompt=_prompt_from_cache(cached), cache_tier="l1")

        l2_cached = await self._read_l2(cache_key)
        if l2_cached is not None:
            self._write_l1(self._prompt_l1, cache_key, l2_cached)
            increment_prompt_cache_lookup(entity="prompt", tier="l2")
            return _PromptLookupResult(prompt=_prompt_from_cache(l2_cached), cache_tier="l2")

        resolved = await self.repository.resolve_prompt(template_key=template_key, label=label, version=version)
        if resolved is None:
            increment_prompt_cache_lookup(entity="prompt", tier="miss")
            return None
        payload = _prompt_to_cache(resolved)
        self._write_l1(self._prompt_l1, cache_key, payload)
        await self._write_l2(cache_key, payload)
        increment_prompt_cache_lookup(entity="prompt", tier="db")
        return _PromptLookupResult(prompt=resolved, cache_tier="db")

    async def _resolve_binding(self, *, scope_type: str, scope_id: str) -> PromptBindingRecord | None:
        cache_key = self._binding_cache_key(scope_type, scope_id)
        cached = self._read_l1(self._binding_l1, cache_key)
        if cached is not None:
            increment_prompt_cache_lookup(entity="binding", tier="l1")
            return _binding_from_cache(cached)

        l2_cached = await self._read_l2(cache_key)
        if l2_cached is not None:
            self._write_l1(self._binding_l1, cache_key, l2_cached)
            increment_prompt_cache_lookup(entity="binding", tier="l2")
            return _binding_from_cache(l2_cached)

        binding = await self.repository.resolve_binding(scope_type=scope_type, scope_id=scope_id)
        if binding is None:
            increment_prompt_cache_lookup(entity="binding", tier="miss")
            return None
        payload = _binding_to_cache(binding)
        self._write_l1(self._binding_l1, cache_key, payload)
        await self._write_l2(cache_key, payload)
        increment_prompt_cache_lookup(entity="binding", tier="db")
        return binding

    async def _resolve_route_group_default(self, route_group_key: str) -> _GroupDefaultPrompt | None:
        if self.route_group_repository is None:
            return None
        cache_key = self._group_default_cache_key(route_group_key)
        cached = self._read_l1(self._group_default_l1, cache_key)
        if cached is not None:
            increment_prompt_cache_lookup(entity="group_default", tier="l1")
            return _group_default_from_cache(cached)

        l2_cached = await self._read_l2(cache_key)
        if l2_cached is not None:
            self._write_l1(self._group_default_l1, cache_key, l2_cached)
            increment_prompt_cache_lookup(entity="group_default", tier="l2")
            return _group_default_from_cache(l2_cached)

        resolved = await self.route_group_repository.get_default_prompt(route_group_key)
        if resolved is None:
            increment_prompt_cache_lookup(entity="group_default", tier="miss")
            return None
        payload = _group_default_to_cache(resolved)
        self._write_l1(self._group_default_l1, cache_key, payload)
        await self._write_l2(cache_key, payload)
        increment_prompt_cache_lookup(entity="group_default", tier="db")
        return _group_default_from_cache(payload)

    def _read_l1(self, cache: dict[str, _CacheEntry], key: str) -> dict[str, Any] | None:
        entry = cache.get(key)
        if entry is None:
            return None
        if entry.expires_at <= time.monotonic():
            cache.pop(key, None)
            return None
        return dict(entry.value)

    def _write_l1(self, cache: dict[str, _CacheEntry], key: str, value: dict[str, Any]) -> None:
        cache[key] = _CacheEntry(
            value=dict(value),
            expires_at=time.monotonic() + self.l1_ttl_seconds,
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

    async def _write_l2(self, key: str, payload: dict[str, Any]) -> None:
        if self.redis is None:
            return
        try:
            await self.redis.setex(key, self.l2_ttl_seconds, json.dumps(payload))
            self._l2_known_keys.add(key)
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
            for key in unique:
                self._l2_known_keys.discard(key)
        except Exception as exc:
            logger.debug("failed to delete prompt cache keys=%s: %s", unique, exc)

    def _prompt_cache_key(self, *, template_key: str, label: str | None, version: int | None) -> str:
        if version is not None:
            return f"{PROMPT_CACHE_PREFIX}:{template_key}:version:{version}"
        return f"{PROMPT_CACHE_PREFIX}:{template_key}:label:{label or 'production'}"

    def _binding_cache_key(self, scope_type: str, scope_id: str) -> str:
        return f"{PROMPT_BINDING_CACHE_PREFIX}:{scope_type}:{scope_id}"

    def _group_default_cache_key(self, route_group_key: str) -> str:
        return f"{PROMPT_GROUP_DEFAULT_CACHE_PREFIX}:{route_group_key}"

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
    ) -> None:
        try:
            await self.repository.create_render_log(
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
                variables=variables,
                metadata=metadata,
            )
        except Exception as exc:
            logger.debug("failed to persist prompt render log: %s", exc)


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
        variables_schema=dict(data.get("variables_schema") or {}) if isinstance(data.get("variables_schema"), dict) else None,
        model_hints=dict(data.get("model_hints") or {}) if isinstance(data.get("model_hints"), dict) else None,
        route_preferences=dict(data.get("route_preferences") or {}) if isinstance(data.get("route_preferences"), dict) else None,
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
        metadata=dict(data.get("metadata") or {}) if isinstance(data.get("metadata"), dict) else None,
        created_at=None,
        updated_at=None,
    )


def _group_default_to_cache(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "template_key": str(data.get("template_key") or ""),
        "label": str(data.get("label")).strip() if data.get("label") is not None and str(data.get("label")).strip() else None,
    }


def _group_default_from_cache(data: dict[str, Any]) -> _GroupDefaultPrompt | None:
    template_key = str(data.get("template_key") or "").strip()
    if not template_key:
        return None
    label = str(data.get("label")).strip() if data.get("label") is not None and str(data.get("label")).strip() else None
    return _GroupDefaultPrompt(template_key=template_key, label=label)


def _to_system_messages(rendered_prompt: Any) -> list[dict[str, Any]]:
    if isinstance(rendered_prompt, dict):
        messages = rendered_prompt.get("messages")
        if isinstance(messages, list):
            valid = [item for item in messages if isinstance(item, dict) and isinstance(item.get("role"), str)]
            if valid:
                return valid
        text = rendered_prompt.get("text")
        if isinstance(text, str) and text.strip():
            return [{"role": "system", "content": text}]
    if isinstance(rendered_prompt, list):
        valid = [item for item in rendered_prompt if isinstance(item, dict) and isinstance(item.get("role"), str)]
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
        existing_tags = [item.strip() for item in existing if isinstance(item, str) and item.strip()] if isinstance(existing, list) else []
        for tag in tags:
            if tag not in existing_tags:
                existing_tags.append(tag)
        output["tags"] = existing_tags

    output["prompt_route_preferences"] = dict(normalized)
    if isinstance(normalized.get("route_group"), str):
        output["prompt_route_group_hint"] = normalized["route_group"]
    return output, normalized
