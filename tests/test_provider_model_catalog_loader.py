from __future__ import annotations

import pytest

from src.providers.model_catalog import (
    canonical_catalog_provider,
    catalog_model_metadata,
    catalog_models_for_provider,
    catalog_provider_sources,
)
from src.providers.model_catalog_loader import (
    ProviderCatalogDocument,
    get_provider_catalog,
    provider_catalog_summary,
    reload_provider_catalogs,
)


def test_provider_catalog_loader_returns_expected_providers() -> None:
    catalogs = reload_provider_catalogs()

    assert {"openai", "anthropic", "groq", "gemini"}.issubset(catalogs.keys())
    assert get_provider_catalog("openai") is not None


def test_provider_catalog_summary_includes_provenance() -> None:
    summary = {item["provider"]: item for item in provider_catalog_summary()}

    assert summary["openai"]["last_verified_at"] == "2026-04-01"
    assert summary["openai"]["model_count"] >= 10


def test_catalog_model_metadata_supports_aliases() -> None:
    metadata = catalog_model_metadata("anthropic", "claude-haiku-4-5-20251001")

    assert metadata is not None
    assert metadata["max_tokens"] == 200000


def test_catalog_models_for_provider_filters_by_mode() -> None:
    models = {item["id"] for item in catalog_models_for_provider("gemini", mode="embedding")}

    assert models == {"gemini-embedding-001"}


def test_catalog_provider_sources_returns_official_urls() -> None:
    sources = catalog_provider_sources("openai")

    assert sources
    assert any(source["url"].startswith("https://developers.openai.com/") for source in sources)


def test_canonical_catalog_provider_maps_azure_alias() -> None:
    assert canonical_catalog_provider("azure") == "azure_openai"


def test_catalog_models_for_provider_reuses_openai_catalog_for_azure() -> None:
    models = {item["id"] for item in catalog_models_for_provider("azure", mode="chat")}

    assert {"gpt-5.4", "gpt-4o"}.issubset(models)


def test_catalog_validation_rejects_model_id_that_collides_with_alias() -> None:
    with pytest.raises(ValueError, match="duplicate model id"):
        ProviderCatalogDocument.model_validate(
            {
                "provider": "test",
                "last_verified_at": "2026-04-01",
                "source_type": "official_docs",
                "sources": [{"label": "Test", "url": "https://example.com"}],
                "models": [
                    {
                        "id": "alpha",
                        "supported_modes": ["chat"],
                        "aliases": ["beta"],
                    },
                    {
                        "id": "beta",
                        "supported_modes": ["chat"],
                    },
                ],
            }
        )


def test_catalog_validation_rejects_duplicate_aliases_across_models() -> None:
    with pytest.raises(ValueError, match="duplicate alias"):
        ProviderCatalogDocument.model_validate(
            {
                "provider": "test",
                "last_verified_at": "2026-04-01",
                "source_type": "official_docs",
                "sources": [{"label": "Test", "url": "https://example.com"}],
                "models": [
                    {
                        "id": "alpha",
                        "supported_modes": ["chat"],
                        "aliases": ["shared"],
                    },
                    {
                        "id": "beta",
                        "supported_modes": ["chat"],
                        "aliases": ["shared"],
                    },
                ],
            }
        )
