from __future__ import annotations

from datetime import date
import json
from functools import lru_cache
from importlib import resources
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from src.config import ModelMode

CATALOG_PACKAGE = "src.providers.catalogs"
ALLOWED_METADATA_KEYS = {
    "input_cost_per_token",
    "output_cost_per_token",
    "input_cost_per_token_cache_hit",
    "output_cost_per_token_cache_hit",
    "batch_input_cost_per_token",
    "batch_output_cost_per_token",
    "batch_price_multiplier",
    "input_cost_per_character",
    "output_cost_per_character",
    "input_cost_per_second",
    "output_cost_per_second",
    "input_cost_per_image",
    "output_cost_per_image",
    "input_cost_per_audio_token",
    "output_cost_per_audio_token",
    "output_vector_size",
    "max_tokens",
    "max_input_tokens",
    "max_output_tokens",
}


class CatalogSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    url: HttpUrl


class CatalogModelEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str | None = None
    status: Literal["active", "preview", "deprecated", "legacy"] = "active"
    supported_modes: tuple[ModelMode, ...]
    aliases: tuple[str, ...] = ()
    metadata: dict[str, float | int] = Field(default_factory=dict)
    notes: str | None = None

    @field_validator("id")
    @classmethod
    def validate_model_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("model id is required")
        return normalized

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, aliases: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(alias.strip() for alias in aliases if alias.strip())
        if len({alias.lower() for alias in normalized}) != len(normalized):
            raise ValueError("aliases must be unique")
        return normalized

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, metadata: dict[str, float | int]) -> dict[str, float | int]:
        invalid = sorted(set(metadata) - ALLOWED_METADATA_KEYS)
        if invalid:
            raise ValueError(f"unsupported metadata keys: {', '.join(invalid)}")
        return dict(metadata)

    @model_validator(mode="after")
    def validate_aliases_against_id(self) -> "CatalogModelEntry":
        if any(alias.lower() == self.id.lower() for alias in self.aliases):
            raise ValueError("aliases must not repeat the model id")
        return self


class ProviderCatalogDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    last_verified_at: date
    source_type: Literal["official_docs", "official_api", "mixed"]
    sources: tuple[CatalogSource, ...]
    models: tuple[CatalogModelEntry, ...]

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("provider is required")
        return normalized

    @model_validator(mode="after")
    def validate_model_entries(self) -> "ProviderCatalogDocument":
        seen_ids: set[str] = set()
        seen_aliases: set[str] = set()
        for model in self.models:
            model_id = model.id.lower()
            if model_id in seen_ids or model_id in seen_aliases:
                raise ValueError(f"duplicate model id: {model.id}")
            seen_ids.add(model_id)
            for alias in model.aliases:
                lowered = alias.lower()
                if lowered in seen_ids or lowered in seen_aliases:
                    raise ValueError(f"duplicate alias: {alias}")
                seen_aliases.add(lowered)
        return self


def _catalog_resource_names() -> list[str]:
    base = resources.files(CATALOG_PACKAGE)
    return sorted(item.name for item in base.iterdir() if item.name.endswith(".models.json"))


def _load_document(resource_name: str) -> ProviderCatalogDocument:
    raw = resources.files(CATALOG_PACKAGE).joinpath(resource_name).read_text(encoding="utf-8")
    payload = json.loads(raw)
    return ProviderCatalogDocument.model_validate(payload)


@lru_cache(maxsize=1)
def load_provider_catalogs() -> dict[str, ProviderCatalogDocument]:
    documents = [_load_document(resource_name) for resource_name in _catalog_resource_names()]
    return {document.provider: document for document in documents}


def reload_provider_catalogs() -> dict[str, ProviderCatalogDocument]:
    load_provider_catalogs.cache_clear()
    return load_provider_catalogs()


def get_provider_catalog(provider: str) -> ProviderCatalogDocument | None:
    return load_provider_catalogs().get(provider.strip().lower())


def provider_catalog_summary() -> list[dict[str, Any]]:
    return [
        {
            "provider": document.provider,
            "last_verified_at": document.last_verified_at.isoformat(),
            "source_type": document.source_type,
            "model_count": len(document.models),
        }
        for document in load_provider_catalogs().values()
    ]
