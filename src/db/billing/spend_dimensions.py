"""SQL fragments that group or filter spend events by one usage dimension.

Every identifier here comes from a static allowlist. Callers add values only
as bound parameters.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from src.billing.spend.spend_read import SpendReadSource
from src.providers.resolution import provider_from_model, resolve_provider

SpendDimension = Literal["model", "provider", "organization", "team", "user", "api_key"]


@dataclass(frozen=True, slots=True)
class SpendDimensionSql:
    group_expr: str
    display_expr: str
    group_by_exprs: tuple[str, ...]
    search_clause: str
    joins: tuple[str, ...] = ()


def search_pattern(search: str) -> str:
    """ILIKE pattern for a plain substring search: ``%`` and ``_`` match only themselves."""

    escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def search_clause_sql(clause: str, index: int) -> str:
    """Bind a dimension search clause to ``$index`` without ``str.format``.

    The clause can hold model names from the registry, and those can contain braces.
    """

    return clause.replace("${i}", f"${index}")


def provider_from_api_base_expr(api_base_expr: str) -> str:
    lowered_api_base = f"LOWER(COALESCE({api_base_expr}, ''))"
    return f"""
        CASE
            WHEN {lowered_api_base} LIKE '%openai.azure.com%' THEN 'azure_openai'
            WHEN {lowered_api_base} LIKE '%api.groq.com%' OR {lowered_api_base} LIKE '%groq%' THEN 'groq'
            WHEN {lowered_api_base} LIKE '%openrouter.ai%' OR {lowered_api_base} LIKE '%openrouter%' THEN 'openrouter'
            WHEN {lowered_api_base} LIKE '%fireworks%' THEN 'fireworks'
            WHEN {lowered_api_base} LIKE '%together%' THEN 'together'
            WHEN {lowered_api_base} LIKE '%deepinfra%' THEN 'deepinfra'
            WHEN {lowered_api_base} LIKE '%perplexity%' THEN 'perplexity'
            WHEN {lowered_api_base} LIKE '%anthropic%' THEN 'anthropic'
            WHEN {lowered_api_base} LIKE '%googleapis.com%' OR {lowered_api_base} LIKE '%generativelanguage.googleapis.com%' THEN 'gemini'
            WHEN {lowered_api_base} LIKE '%bedrock%' THEN 'bedrock'
            WHEN {lowered_api_base} LIKE '%api.openai.com%' OR {lowered_api_base} LIKE '%openai.com%' THEN 'openai'
            WHEN {lowered_api_base} LIKE '%azure%' THEN 'azure_openai'
            ELSE NULL
        END
    """.strip()


def provider_from_model_expr(model_expr: str) -> str:
    lowered_model = f"LOWER(COALESCE({model_expr}, ''))"
    return f"""
        CASE
            WHEN {lowered_model} LIKE 'azure_openai/%' OR {lowered_model} LIKE 'azure/%' THEN 'azure_openai'
            WHEN {lowered_model} LIKE 'anthropic/%' THEN 'anthropic'
            WHEN {lowered_model} LIKE 'openrouter/%' THEN 'openrouter'
            WHEN {lowered_model} LIKE 'groq/%' THEN 'groq'
            WHEN {lowered_model} LIKE 'together/%' THEN 'together'
            WHEN {lowered_model} LIKE 'fireworks/%' THEN 'fireworks'
            WHEN {lowered_model} LIKE 'deepinfra/%' THEN 'deepinfra'
            WHEN {lowered_model} LIKE 'perplexity/%' THEN 'perplexity'
            WHEN {lowered_model} LIKE 'gemini/%' THEN 'gemini'
            WHEN {lowered_model} LIKE 'bedrock/%' THEN 'bedrock'
            WHEN {lowered_model} LIKE 'vllm/%' THEN 'vllm'
            WHEN {lowered_model} LIKE 'lmstudio/%' THEN 'lmstudio'
            WHEN {lowered_model} LIKE 'ollama/%' THEN 'ollama'
            WHEN {lowered_model} LIKE 'openai/%' THEN 'openai'
            ELSE NULL
        END
    """.strip()


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _legacy_cache_provider_override_expr(
    *,
    table_alias: str = "s",
    model_provider_overrides: dict[str, str] | None = None,
) -> str:
    if not model_provider_overrides:
        return "NULL"

    lowered_api_base = f"LOWER(COALESCE({table_alias}.api_base, ''))"
    cases = []
    for model, provider in sorted(model_provider_overrides.items()):
        model_literal = _sql_string_literal(model)
        provider_literal = _sql_string_literal(provider)
        cases.append(
            f"""WHEN (
                LOWER(COALESCE({table_alias}.deployment_model, '')) = {model_literal}
                OR LOWER(COALESCE({table_alias}.model, '')) = {model_literal}
            ) THEN {provider_literal}"""
        )
    case_sql = "\n            ".join(cases)
    return f"""
        CASE
            WHEN {lowered_api_base} <> 'cache' THEN NULL
            {case_sql}
            ELSE NULL
        END
    """.strip()


def canonical_provider_expr(
    *,
    table_alias: str = "s",
    model_provider_overrides: dict[str, str] | None = None,
) -> str:
    provider_column = f"{table_alias}.provider"
    metadata_provider_column = f"{table_alias}.metadata->>'provider'"
    api_base_column = f"{table_alias}.api_base"
    api_base_provider_expr = provider_from_api_base_expr(api_base_column)
    legacy_cache_provider_expr = _legacy_cache_provider_override_expr(
        table_alias=table_alias,
        model_provider_overrides=model_provider_overrides,
    )
    deployment_model_provider_expr = provider_from_model_expr(f"{table_alias}.deployment_model")
    model_provider_expr = provider_from_model_expr(f"{table_alias}.model")
    return f"""
        COALESCE(
            NULLIF(LOWER(TRIM(COALESCE({metadata_provider_column}, ''))), ''),
            NULLIF(LOWER(TRIM({provider_column})), ''),
            {legacy_cache_provider_expr},
            {api_base_provider_expr},
            {deployment_model_provider_expr},
            {model_provider_expr},
            'unknown'
        )
    """.strip()


def legacy_cache_model_provider_overrides(registry: Mapping[str, Any]) -> dict[str, str]:
    provider_candidates: dict[str, set[str]] = {}
    for deployments in registry.values():
        if not isinstance(deployments, list):
            continue
        for deployment in deployments:
            params = deployment.get("deltallm_params") if isinstance(deployment, dict) else None
            if not isinstance(params, dict):
                continue
            deployment_model = str(params.get("model") or "").strip()
            provider = resolve_provider(params)
            if not deployment_model or provider == "unknown":
                continue
            if provider_from_model(deployment_model) == provider:
                continue
            provider_candidates.setdefault(deployment_model.lower(), set()).add(provider)
    return {
        deployment_model: next(iter(providers))
        for deployment_model, providers in provider_candidates.items()
        if len(providers) == 1
    }


def spend_dimension_sql(
    group_by: str,
    source: SpendReadSource,
    *,
    model_provider_overrides: dict[str, str] | None = None,
    user_identity_labels_visible: bool = False,
) -> SpendDimensionSql:
    """Return the allowlisted SQL that groups spend rows (alias ``s``) by one dimension."""
    if group_by == "model":
        return SpendDimensionSql(
            group_expr="s.model",
            display_expr="NULL",
            group_by_exprs=("s.model",),
            search_clause="(COALESCE(s.model, '') ILIKE ${i} OR (s.model IS NULL AND 'unspecified model' ILIKE ${i}))",
        )
    if group_by == "organization":
        organization_label = "NULLIF(TRIM(COALESCE(o.organization_name, '')), '')"
        return SpendDimensionSql(
            joins=(
                "LEFT JOIN deltallm_organizationtable o ON o.organization_id = s.organization_id",
            ),
            group_expr="s.organization_id",
            display_expr=organization_label,
            group_by_exprs=("s.organization_id", organization_label),
            search_clause="(COALESCE(s.organization_id, '') ILIKE ${i} OR COALESCE(o.organization_name, '') ILIKE ${i} OR (s.organization_id IS NULL AND 'unassigned organization' ILIKE ${i}))",
        )
    if group_by == "team":
        team_label = "NULLIF(TRIM(COALESCE(t.team_alias, '')), '')"
        return SpendDimensionSql(
            joins=("LEFT JOIN deltallm_teamtable t ON t.team_id = s.team_id",),
            group_expr="s.team_id",
            display_expr=team_label,
            group_by_exprs=("s.team_id", team_label),
            search_clause="(COALESCE(s.team_id, '') ILIKE ${i} OR COALESCE(t.team_alias, '') ILIKE ${i} OR (s.team_id IS NULL AND 'unassigned team' ILIKE ${i}))",
        )
    if group_by == "user":
        user_column = source.column("user_column", table_alias="s")
        if not user_identity_labels_visible:
            return SpendDimensionSql(
                group_expr=user_column,
                display_expr="NULL",
                group_by_exprs=(user_column,),
                search_clause=f"(COALESCE({user_column}, '') ILIKE ${{i}} OR ({user_column} IS NULL AND 'unassigned user' ILIKE ${{i}}))",
            )
        user_label = "NULLIF(TRIM(COALESCE(u.user_email, '')), '')"
        return SpendDimensionSql(
            joins=(f"LEFT JOIN deltallm_usertable u ON u.user_id = {user_column}",),
            group_expr=user_column,
            display_expr=user_label,
            group_by_exprs=(user_column, user_label),
            search_clause=f"(COALESCE({user_column}, '') ILIKE ${{i}} OR COALESCE(u.user_email, '') ILIKE ${{i}} OR ({user_column} IS NULL AND 'unassigned user' ILIKE ${{i}}))",
        )
    if group_by == "api_key":
        key_label = "NULLIF(TRIM(COALESCE(vt.key_name, '')), '')"
        return SpendDimensionSql(
            joins=("LEFT JOIN deltallm_verificationtoken vt ON vt.token = s.api_key",),
            group_expr="s.api_key",
            display_expr=key_label,
            group_by_exprs=("s.api_key", key_label),
            search_clause="(COALESCE(s.api_key, '') ILIKE ${i} OR COALESCE(vt.key_name, '') ILIKE ${i} OR (s.api_key IS NULL AND 'unassigned api key' ILIKE ${i}))",
        )
    if group_by == "provider":
        provider_expr = canonical_provider_expr(
            table_alias="s",
            model_provider_overrides=model_provider_overrides,
        )
        return SpendDimensionSql(
            group_expr=provider_expr,
            display_expr="NULL",
            group_by_exprs=(provider_expr,),
            search_clause=f"({provider_expr} ILIKE ${{i}} OR COALESCE(s.api_base, '') ILIKE ${{i}})",
        )
    raise ValueError(f"Unsupported spend grouping: {group_by}")
