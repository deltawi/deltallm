from __future__ import annotations

from src.services.runtime_scopes import RuntimeScopeContext

CANONICAL_SCOPE_ALIASES: dict[str, tuple[str, ...]] = {
    "api_key": ("api_key", "key"),
    "team": ("team",),
    "organization": ("organization", "org"),
    "user": ("user",),
    "group": ("group",),
}

LEGACY_TO_CANONICAL_SCOPE: dict[str, str] = {
    "key": "api_key",
    "api_key": "api_key",
    "team": "team",
    "org": "organization",
    "organization": "organization",
    "user": "user",
    "group": "group",
}


def normalize_scope_type(scope_type: str) -> str:
    normalized = str(scope_type or "").strip().lower()
    return LEGACY_TO_CANONICAL_SCOPE.get(normalized, normalized)


def scope_lookup_candidates(scope_type: str) -> tuple[str, ...]:
    canonical = normalize_scope_type(scope_type)
    return CANONICAL_SCOPE_ALIASES.get(canonical, (canonical,))


def prompt_binding_resolution_chain(
    *,
    scope_context: RuntimeScopeContext | None,
    api_key: str | None,
    user_id: str | None,
    team_id: str | None,
    organization_id: str | None,
    route_group_key: str | None,
) -> list[tuple[str, str]]:
    if scope_context is not None:
        chain: list[tuple[str, str]] = []
        if scope_context.user_id:
            chain.append(("user", scope_context.user_id))
        if scope_context.api_key_scope_id:
            chain.append(("api_key", scope_context.api_key_scope_id))
        if scope_context.team_id:
            chain.append(("team", scope_context.team_id))
        if scope_context.organization_id:
            chain.append(("organization", scope_context.organization_id))
    else:
        chain = []
        if user_id:
            chain.append(("user", str(user_id).strip()))
        if api_key:
            chain.append(("api_key", str(api_key).strip()))
        if team_id:
            chain.append(("team", str(team_id).strip()))
        if organization_id:
            chain.append(("organization", str(organization_id).strip()))

    if route_group_key:
        chain.append(("group", str(route_group_key).strip()))
    return [(scope_type, scope_id) for scope_type, scope_id in chain if scope_id]
