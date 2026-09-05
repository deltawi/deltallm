from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.docs import export_openapi
from scripts.docs.generate_config_reference import generate_reference as generate_config
from scripts.docs.generate_provider_reference import generate_reference as generate_providers
from scripts.docs.generated_artifact import write_or_check
from src.config import GeneralSettings
from src.providers.model_catalog_loader import load_provider_catalogs
from src.providers.resolution import PROVIDER_PRESETS

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_generated_openapi_excludes_runtime_spa_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _App:
        def openapi(self) -> dict[str, object]:
            return {
                "paths": {
                    "/v1/models": {"get": {"operationId": "list_models"}},
                    "/{full_path}": {"get": {"operationId": "serve_spa__full_path__get"}},
                }
            }

    monkeypatch.setattr(export_openapi, "create_app", _App)
    content, path_count, operation_count = export_openapi.generate_openapi()

    schema = json.loads(content)
    assert "/v1/models" in schema["paths"]
    assert "/{full_path}" not in schema["paths"]
    assert path_count == 1
    assert operation_count == 1


def test_generated_config_reference_covers_every_general_setting() -> None:
    content, field_count = generate_config()

    assert field_count == len(GeneralSettings.model_fields)
    for field_name in GeneralSettings.model_fields:
        assert f"`{field_name}`" in content


def test_generated_provider_reference_covers_runtime_presets_and_catalogs() -> None:
    content, provider_count, model_count = generate_providers()

    assert provider_count == len(PROVIDER_PRESETS)
    assert model_count == sum(
        len(document.models) for document in load_provider_catalogs().values()
    )
    normalized_content = "".join(character for character in content.lower() if character.isalnum())
    for preset in PROVIDER_PRESETS.values():
        normalized_provider = "".join(
            character for character in str(preset["provider"]).lower() if character.isalnum()
        )
        assert normalized_provider in normalized_content


def test_committed_openapi_operation_ids_are_unique() -> None:
    schema = json.loads((PROJECT_ROOT / "docs" / "api" / "openapi.json").read_text())
    operation_ids = [
        operation["operationId"]
        for path_item in schema["paths"].values()
        for method, operation in path_item.items()
        if method in {"delete", "get", "head", "options", "patch", "post", "put", "trace"}
    ]

    assert len(operation_ids) == len(set(operation_ids))
    assert "get_ui_branding_asset" in operation_ids


def test_committed_openapi_chat_messages_are_role_aware() -> None:
    schema = json.loads((PROJECT_ROOT / "docs" / "api" / "openapi.json").read_text())
    schemas = schema["components"]["schemas"]
    message_items = schemas["ChatCompletionRequest"]["properties"]["messages"]["items"]

    assert message_items["discriminator"] == {
        "propertyName": "role",
        "mapping": {
            "assistant": "#/components/schemas/AssistantChatMessage",
            "system": "#/components/schemas/SystemChatMessage",
            "tool": "#/components/schemas/ToolChatMessage",
            "user": "#/components/schemas/UserChatMessage",
        },
    }
    assert {item["$ref"] for item in message_items["oneOf"]} == {
        "#/components/schemas/AssistantChatMessage",
        "#/components/schemas/SystemChatMessage",
        "#/components/schemas/ToolChatMessage",
        "#/components/schemas/UserChatMessage",
    }

    assistant = schemas["AssistantChatMessage"]
    assert "content" not in assistant["required"]
    assert {variant.get("type") for variant in assistant["properties"]["content"]["anyOf"]} >= {
        "string",
        "array",
        "null",
    }
    for name in ("SystemChatMessage", "ToolChatMessage", "UserChatMessage"):
        assert "content" in schemas[name]["required"]
        assert all(
            variant.get("type") != "null"
            for variant in schemas[name]["properties"]["content"]["anyOf"]
        )


def test_generated_artifact_check_detects_drift(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.md"
    artifact.write_text("old\n", encoding="utf-8")

    assert write_or_check(artifact, "old", check=True)
    assert not write_or_check(artifact, "new", check=True)
    assert artifact.read_text(encoding="utf-8") == "old\n"
