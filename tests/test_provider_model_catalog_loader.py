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
    load_provider_catalogs,
    provider_catalog_summary,
    reload_provider_catalogs,
)


def test_provider_catalog_loader_returns_expected_providers() -> None:
    catalogs = reload_provider_catalogs()

    assert {"openai", "anthropic", "groq", "gemini", "elevenlabs"}.issubset(catalogs.keys())
    assert get_provider_catalog("openai") is not None


def test_provider_catalog_summary_includes_provenance() -> None:
    summary = {item["provider"]: item for item in provider_catalog_summary()}

    assert summary["openai"]["last_verified_at"] == "2026-11-03"
    assert summary["openai"]["model_count"] >= 10
    assert summary["elevenlabs"]["last_verified_at"] == "2026-06-13"
    assert summary["elevenlabs"]["model_count"] == 5


def test_catalog_model_metadata_supports_aliases() -> None:
    metadata = catalog_model_metadata("anthropic", "claude-haiku-4-5-20251001")

    assert metadata is not None
    assert metadata["max_tokens"] == 200000


def test_catalog_models_for_provider_filters_by_mode() -> None:
    models = {item["id"] for item in catalog_models_for_provider("gemini", mode="embedding")}

    assert models == {"gemini-embedding-001"}


def test_catalog_models_for_elevenlabs_filters_audio_modes() -> None:
    speech_models = {
        item["id"] for item in catalog_models_for_provider("elevenlabs", mode="audio_speech")
    }
    transcription_models = {
        item["id"] for item in catalog_models_for_provider("elevenlabs", mode="audio_transcription")
    }

    assert speech_models == {"eleven_v3", "eleven_multilingual_v2", "eleven_flash_v2_5"}
    assert transcription_models == {"scribe_v2", "scribe_v1"}


def test_catalog_provider_sources_returns_official_urls() -> None:
    sources = catalog_provider_sources("openai")

    assert sources
    assert any(source["url"].startswith("https://developers.openai.com/") for source in sources)


def test_elevenlabs_catalog_exposes_official_sources_and_metadata() -> None:
    catalog = get_provider_catalog("elevenlabs")
    assert catalog is not None

    sources = catalog_provider_sources("elevenlabs")
    speech_metadata = catalog_model_metadata("elevenlabs", "eleven_flash_v2_5")
    transcription_metadata = catalog_model_metadata("elevenlabs", "scribe_v2")
    model_statuses = {model.id: model.status for model in catalog.models}

    assert any(source["url"].startswith("https://elevenlabs.io/docs/") for source in sources)
    assert any(source["url"].startswith("https://elevenlabs.io/pricing/") for source in sources)
    assert model_statuses["scribe_v1"] == "deprecated"
    assert speech_metadata is not None
    assert speech_metadata["input_cost_per_character"] == 0.00005
    assert transcription_metadata is not None
    assert transcription_metadata["input_cost_per_second"] == 0.0000611111111111


def test_catalog_validation_accepts_request_pricing_metadata() -> None:
    catalog = ProviderCatalogDocument.model_validate(
        {
            "provider": "test",
            "last_verified_at": "2026-04-01",
            "source_type": "official_docs",
            "sources": [{"label": "Test", "url": "https://example.com"}],
            "models": [
                {
                    "id": "flat-priced-image-model",
                    "supported_modes": ["image_generation"],
                    "metadata": {"cost_per_request": 0.75},
                }
            ],
        }
    )

    assert catalog.models[0].metadata["cost_per_request"] == 0.75


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


def test_catalog_never_prefills_batch_prices() -> None:
    # Batch prices are what DeltaLLM bills for its own Batch API, which calls the
    # provider's regular API. Pre-filling a provider's batch discount would bill
    # batch items below what the provider charges.
    batch_keys = {
        "batch_input_cost_per_token",
        "batch_output_cost_per_token",
        "batch_price_multiplier",
    }
    for document in load_provider_catalogs().values():
        for model in document.models:
            assert not batch_keys.intersection(model.metadata), (document.provider, model.id)


def test_current_chat_models_have_prices_and_limits() -> None:
    expected = {
        "openai": ["gpt-6.1-sol", "gpt-5.5", "gpt-5.4", "gpt-4o"],
        "anthropic": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"],
        "deepseek": ["deepseek-flash", "deepseek-v4-pro"],
    }
    for provider, model_ids in expected.items():
        for model_id in model_ids:
            metadata = catalog_model_metadata(provider, model_id)
            assert metadata is not None, (provider, model_id)
            assert metadata["input_cost_per_token"] > 0
            assert metadata["output_cost_per_token"] > 0
            assert metadata["input_cost_per_token_cache_hit"] > 0
            assert metadata["max_tokens"] > 0
            assert metadata["max_output_tokens"] > 0
    for model_id in ["gemini-3.1-pro-preview", "gemini-2.5-flash"]:
        metadata = catalog_model_metadata("gemini", model_id)
        assert metadata is not None
        assert metadata["input_cost_per_token"] > 0
        assert metadata["input_cost_per_token_cache_hit"] > 0
