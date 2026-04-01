from __future__ import annotations

from typing import Any

from src.config import ModelMode
from src.providers.model_catalog_loader import CatalogModelEntry, get_provider_catalog


PROVIDER_CATALOG_ALIASES: dict[str, str] = {
    "azure": "azure_openai",
}


def canonical_catalog_provider(provider: str | None) -> str:
    normalized = str(provider or "").strip().lower()
    return PROVIDER_CATALOG_ALIASES.get(normalized, normalized)


def _catalog_document_for_provider(provider: str | None):
    canonical_provider = canonical_catalog_provider(provider)
    document = get_provider_catalog(canonical_provider)
    if document is not None:
        return document
    if canonical_provider == "azure_openai":
        return get_provider_catalog("openai")
    return None


def _catalog_option(
    entry: CatalogModelEntry,
    *,
    provider: str,
    source: str = "catalog",
) -> dict[str, Any]:
    return {
        "id": entry.id,
        "label": entry.label or entry.id,
        "provider": provider,
        "source": source,
        "supported_modes": list(entry.supported_modes),
        "known_metadata": dict(entry.metadata) if entry.metadata else None,
    }


def catalog_models_for_provider(provider: str | None, *, mode: ModelMode | None = None) -> list[dict[str, Any]]:
    canonical_provider = canonical_catalog_provider(provider)
    response_provider = str(provider or canonical_provider).strip().lower() or canonical_provider
    document = _catalog_document_for_provider(canonical_provider)
    if document is None:
        return []

    models: list[dict[str, Any]] = []
    for entry in document.models:
        if mode is not None and mode not in entry.supported_modes:
            continue
        models.append(_catalog_option(entry, provider=response_provider))
    return models


def catalog_model_metadata(provider: str | None, model_id: str | None) -> dict[str, Any] | None:
    normalized_id = str(model_id or "").strip().lower()
    if not normalized_id:
        return None

    document = _catalog_document_for_provider(provider)
    if document is None:
        return None

    for entry in document.models:
        if entry.id.lower() == normalized_id:
            return dict(entry.metadata)
        if any(alias.lower() == normalized_id for alias in entry.aliases):
            return dict(entry.metadata)
    return None


def catalog_provider_sources(provider: str | None) -> list[dict[str, str]]:
    document = _catalog_document_for_provider(provider)
    if document is None:
        return []
    return [{"label": source.label, "url": str(source.url)} for source in document.sources]
