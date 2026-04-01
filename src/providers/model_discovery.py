from __future__ import annotations

from typing import Any

import httpx

from src.config import ModelMode
from src.providers.model_catalog import canonical_catalog_provider, catalog_model_metadata, catalog_models_for_provider
from src.providers.resolution import PROVIDER_PRESETS, is_openai_compatible_provider


def _normalized_provider(provider: str | None) -> str:
    return canonical_catalog_provider(provider)


def _response_provider(provider: str | None) -> str:
    raw = str(provider or "").strip().lower()
    return raw or _normalized_provider(provider)


def _default_api_base(provider: str, *, default_openai_base_url: str) -> str | None:
    if provider == "openai":
        return str(default_openai_base_url or "").strip() or str(PROVIDER_PRESETS["openai"]["api_base"] or "").strip() or None

    preset = PROVIDER_PRESETS.get(provider)
    if preset is None:
        return None
    api_base = str(preset.get("api_base") or "").strip()
    return api_base or None


def _extract_openai_style_models(payload: Any, *, provider: str) -> list[dict[str, Any]]:
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []

    models: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or item.get("name") or "").strip()
        if not model_id:
            continue
        models.append(
            {
                "id": model_id,
                "label": str(item.get("display_name") or item.get("name") or model_id).strip() or model_id,
                "provider": provider,
                "source": "provider_api",
                "supported_modes": [],
                "known_metadata": catalog_model_metadata(provider, model_id),
            }
        )
    return models


def _extract_anthropic_models(payload: Any, *, provider: str) -> list[dict[str, Any]]:
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []

    models: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        label = str(item.get("display_name") or item.get("name") or model_id).strip() or model_id
        models.append(
            {
                "id": model_id,
                "label": label,
                "provider": provider,
                "source": "provider_api",
                "supported_modes": [],
                "known_metadata": catalog_model_metadata(provider, model_id),
            }
        )
    return models


def _extract_gemini_models(payload: Any, *, provider: str) -> list[dict[str, Any]]:
    items = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []

    models: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_name = str(item.get("name") or "").strip()
        model_id = raw_name.removeprefix("models/") if raw_name else ""
        if not model_id:
            continue
        label = str(item.get("displayName") or model_id).strip() or model_id
        models.append(
            {
                "id": model_id,
                "label": label,
                "provider": provider,
                "source": "provider_api",
                "supported_modes": [],
                "known_metadata": catalog_model_metadata(provider, model_id),
            }
        )
    return models


async def _load_json(
    http_client: httpx.AsyncClient,
    *,
    url: str,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> Any:
    response = await http_client.get(url, headers=headers or None, timeout=timeout)
    if response.status_code >= 400:
        raise httpx.HTTPStatusError(
            f"Provider model discovery returned {response.status_code}",
            request=httpx.Request("GET", url),
            response=response,
        )

    try:
        return response.json()
    except ValueError as exc:
        raise ValueError("Provider model discovery returned invalid JSON") from exc


async def _discover_live_models(
    http_client: httpx.AsyncClient,
    *,
    provider: str,
    response_provider: str,
    api_key: str | None,
    api_base: str | None,
    api_version: str | None,
) -> list[dict[str, Any]]:
    if provider == "bedrock":
        raise NotImplementedError("Live discovery is not yet implemented for AWS Bedrock.")

    if provider == "anthropic":
        if not api_key:
            return []
        payload = await _load_json(
            http_client,
            url=f"{str(api_base or '').rstrip('/')}/models",
            headers={
                "x-api-key": api_key,
                "anthropic-version": str(api_version or "2023-06-01").strip() or "2023-06-01",
            },
        )
        return _extract_anthropic_models(payload, provider=response_provider)

    if provider in {"azure", "azure_openai"}:
        if not api_key or not api_base:
            return []
        payload = await _load_json(
            http_client,
            url=f"{str(api_base).rstrip('/')}/models",
            headers={"api-key": api_key},
        )
        return _extract_openai_style_models(payload, provider=response_provider)

    if provider == "gemini":
        if not api_key:
            return []
        payload = await _load_json(
            http_client,
            url=f"{str(api_base or '').rstrip('/')}/models?key={api_key}",
        )
        return _extract_gemini_models(payload, provider=response_provider)

    if not is_openai_compatible_provider(provider):
        return []
    if not api_key or not api_base:
        return []

    payload = await _load_json(
        http_client,
        url=f"{str(api_base).rstrip('/')}/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    return _extract_openai_style_models(payload, provider=response_provider)


def _merge_model_options(
    catalog_options: list[dict[str, Any]],
    live_options: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}

    for option in catalog_options + live_options:
        provider = str(option.get("provider") or "").strip().lower()
        model_id = str(option.get("id") or "").strip()
        if not provider or not model_id:
            continue

        key = (provider, model_id.lower())
        existing = merged.get(key)
        if existing is None:
            merged[key] = {
                **option,
                "known_metadata": dict(option.get("known_metadata") or {}) or None,
                "supported_modes": list(option.get("supported_modes") or []),
            }
            continue

        existing_metadata = dict(existing.get("known_metadata") or {})
        next_metadata = dict(option.get("known_metadata") or {})
        merged_metadata = existing_metadata or next_metadata or None

        existing_modes = set(existing.get("supported_modes") or [])
        next_modes = set(option.get("supported_modes") or [])
        combined_modes = sorted(existing_modes | next_modes)

        sources = {str(existing.get("source") or "").strip(), str(option.get("source") or "").strip()}
        if sources == {"catalog", "provider_api"}:
            merged_source = "catalog+provider_api"
        else:
            merged_source = str(existing.get("source") or option.get("source") or "catalog").strip()

        merged[key] = {
            "id": model_id,
            "label": str(option.get("label") or existing.get("label") or model_id).strip() or model_id,
            "provider": provider,
            "source": merged_source,
            "supported_modes": combined_modes,
            "known_metadata": merged_metadata,
        }

    def sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
        metadata_rank = 0 if item.get("known_metadata") else 1
        source_rank = 0 if str(item.get("source") or "") == "catalog+provider_api" else 1
        return (metadata_rank, source_rank, str(item.get("label") or item.get("id") or "").lower())

    return sorted(merged.values(), key=sort_key)


async def discover_provider_models(
    http_client: httpx.AsyncClient,
    *,
    provider: str,
    mode: ModelMode | None = None,
    api_key: str | None = None,
    api_base: str | None = None,
    api_version: str | None = None,
    default_openai_base_url: str,
) -> dict[str, Any]:
    normalized_provider = _normalized_provider(provider)
    response_provider = _response_provider(provider)
    catalog_options = catalog_models_for_provider(response_provider, mode=mode)
    warnings: list[str] = []

    resolved_api_base = str(api_base or "").strip() or _default_api_base(normalized_provider, default_openai_base_url=default_openai_base_url)

    try:
        live_options = await _discover_live_models(
            http_client,
            provider=normalized_provider,
            response_provider=response_provider,
            api_key=str(api_key or "").strip() or None,
            api_base=resolved_api_base,
            api_version=str(api_version or "").strip() or None,
        )
    except NotImplementedError as exc:
        live_options = []
        warnings.append(str(exc))
    except (httpx.HTTPError, ValueError) as exc:
        live_options = []
        warnings.append(str(exc))

    return {
        "data": _merge_model_options(catalog_options, live_options),
        "warnings": warnings,
    }
