from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from src.models.responses import UserAPIKeyAuth

AuthSource = Literal["api_key", "jwt", "custom", "master_key", "unknown"]


@dataclass(frozen=True, slots=True)
class RuntimeScopeContext:
    auth_source: AuthSource
    is_master_key: bool
    actor_id: str | None
    api_key_scope_id: str | None
    user_id: str | None
    team_id: str | None
    organization_id: str | None
    scope_chain: tuple[tuple[str, str], ...]
    binding_scopes: tuple[tuple[str, str], ...]


def annotate_auth_metadata(
    auth: UserAPIKeyAuth,
    *,
    auth_source: AuthSource,
    api_key_scope_id: str | None = None,
    is_master_key: bool = False,
) -> UserAPIKeyAuth:
    metadata = dict(auth.metadata or {})
    metadata["auth_source"] = _normalize_auth_source(auth_source)
    if api_key_scope_id:
        metadata["api_key_scope_id"] = str(api_key_scope_id)
    if is_master_key:
        metadata["is_master_key"] = True
    auth.metadata = metadata
    return auth


def resolve_runtime_scope_context(auth: UserAPIKeyAuth) -> RuntimeScopeContext:
    metadata = getattr(auth, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    api_key = _normalize_optional(getattr(auth, "api_key", None))
    user_id = _normalize_optional(getattr(auth, "user_id", None))
    team_id = _normalize_optional(getattr(auth, "team_id", None))
    organization_id = _normalize_optional(getattr(auth, "organization_id", None))
    is_master_key = bool(metadata.get("is_master_key")) or api_key == "master_key"
    auth_source = _normalize_auth_source(
        metadata.get("auth_source") or _infer_auth_source(api_key, metadata, is_master_key=is_master_key)
    )
    api_key_scope_id = _normalize_optional(metadata.get("api_key_scope_id"))
    if api_key_scope_id is None and auth_source == "api_key":
        api_key_scope_id = api_key

    return _build_runtime_scope_context(
        auth_source=auth_source,
        is_master_key=is_master_key,
        api_key=api_key,
        api_key_scope_id=api_key_scope_id,
        user_id=user_id,
        team_id=team_id,
        organization_id=organization_id,
    )


@lru_cache(maxsize=2048)
def _build_runtime_scope_context(
    *,
    auth_source: AuthSource,
    is_master_key: bool,
    api_key: str | None,
    api_key_scope_id: str | None,
    user_id: str | None,
    team_id: str | None,
    organization_id: str | None,
) -> RuntimeScopeContext:
    scope_chain: list[tuple[str, str]] = []
    if not is_master_key and user_id is not None:
        scope_chain.append(("user", user_id))
    binding_scopes: list[tuple[str, str]] = []
    if not is_master_key and api_key_scope_id is not None:
        scope = ("api_key", api_key_scope_id)
        scope_chain.append(scope)
        binding_scopes.append(scope)
    if not is_master_key and team_id is not None:
        scope = ("team", team_id)
        scope_chain.append(scope)
        binding_scopes.append(scope)
    if not is_master_key and organization_id is not None:
        scope = ("organization", organization_id)
        scope_chain.append(scope)
        binding_scopes.append(scope)

    return RuntimeScopeContext(
        auth_source=auth_source,
        is_master_key=is_master_key,
        actor_id=user_id or api_key_scope_id or api_key,
        api_key_scope_id=api_key_scope_id,
        user_id=user_id,
        team_id=team_id,
        organization_id=organization_id,
        scope_chain=tuple(scope_chain),
        binding_scopes=tuple(binding_scopes),
    )


def _infer_auth_source(
    api_key: str | None,
    metadata: dict[str, object],
    *,
    is_master_key: bool,
) -> AuthSource:
    if is_master_key:
        return "master_key"
    if "jwt_claims" in metadata or str(api_key or "").startswith("jwt:"):
        return "jwt"
    return "api_key"


def _normalize_optional(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalize_auth_source(value: object) -> AuthSource:
    normalized = str(value).strip().lower()
    if normalized in {"api_key", "jwt", "custom", "master_key"}:
        return normalized  # type: ignore[return-value]
    return "unknown"
