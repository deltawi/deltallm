from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.api.admin.endpoints.common import serialize_guardrail
from src.config import GuardrailConfig
from src.guardrails.catalog import get_guardrail_preset, list_guardrail_presets


def _prepare_guardrail_config(test_app) -> None:  # noqa: ANN001
    setattr(test_app.state.settings, "master_key", "mk-test")
    setattr(test_app.state.app_config.general_settings, "master_key", "mk-test")
    setattr(
        test_app.state.app_config,
        "deltallm_settings",
        type("DeltaCfg", (), {"guardrails": []})(),
    )


def test_guardrail_config_rejects_unsupported_runtime_options() -> None:
    with pytest.raises(ValidationError):
        GuardrailConfig(
            guardrail_name="invalid-mode",
            deltallm_params={
                "guardrail": "src.guardrails.presidio.PresidioGuardrail",
                "mode": "during_call",
            },
        )

    with pytest.raises(ValidationError):
        GuardrailConfig(
            guardrail_name="invalid-action",
            deltallm_params={
                "guardrail": "src.guardrails.presidio.PresidioGuardrail",
                "default_action": "warn",
            },
        )


def test_serialize_guardrail_marks_builtin_preset() -> None:
    preset = get_guardrail_preset("presidio_pii")
    assert preset is not None

    payload = serialize_guardrail(
        {
            "guardrail_name": "presidio-pii",
            "deltallm_params": {
                "guardrail": preset["class_path"],
                "mode": "post_call",
                "default_action": "log",
                "default_on": False,
                "threshold": 0.7,
                "anonymize": False,
                "entities": ["EMAIL_ADDRESS"],
            },
        }
    )

    assert payload["preset_id"] == "presidio_pii"
    assert payload["type"] == "PII Detection (Presidio)"
    assert payload["editor"]["field_values"]["threshold"] == pytest.approx(0.7)
    assert payload["editor"]["field_values"]["entities"] == ["EMAIL_ADDRESS"]
    assert payload["editor"]["default_on"] is False


def test_serialize_guardrail_recognizes_builtin_alias_class_path() -> None:
    preset = get_guardrail_preset("presidio_pii")
    assert preset is not None

    aliased_class_path = str(preset["class_path"]).replace("src.", "deltallm.", 1)
    payload = serialize_guardrail(
        {
            "guardrail_name": "presidio-pii",
            "deltallm_params": {
                "guardrail": aliased_class_path,
            },
        }
    )

    assert payload["preset_id"] == "presidio_pii"
    assert payload["type"] == "PII Detection (Presidio)"
    assert payload["editor"]["class_path"] == preset["class_path"]


@pytest.mark.asyncio
async def test_guardrail_catalog_returns_builtin_presets(client, test_app) -> None:
    _prepare_guardrail_config(test_app)

    response = await client.get("/ui/api/guardrails/catalog", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["supported_modes"] == ["pre_call", "post_call"]
    assert payload["supported_actions"] == ["block", "log"]
    assert [item["preset_id"] for item in payload["presets"]] == [
        "presidio_pii",
        "lakera_prompt_injection",
    ]
    assert payload["capabilities"]["presidio"]["engine_mode"] in {"full", "regex_fallback"}
    assert payload["capabilities"]["presidio"]["fallback_supported_entities"] == [
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        "US_SSN",
        "CREDIT_CARD",
        "IP_ADDRESS",
    ]


def test_guardrail_catalog_disables_full_engine_presidio_entities_in_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.guardrails.catalog.presidio_engine_mode", lambda: "regex_fallback")

    presets = list_guardrail_presets()
    preset = next(item for item in presets if item["preset_id"] == "presidio_pii")
    entities_field = next(field for field in preset["fields"] if field["key"] == "entities")

    assert entities_field["default_value"] == [
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        "US_SSN",
        "CREDIT_CARD",
        "IP_ADDRESS",
    ]
    disabled_values = {
        option["value"]
        for option in entities_field["options"]
        if option.get("disabled") is True
    }
    assert disabled_values == {"PERSON", "US_PASSPORT", "IBAN", "LOCATION"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("deltallm_params", "expected_detail"),
    [
        (
            {
                "guardrail": "src.guardrails.presidio.PresidioGuardrail",
                "mode": "during_call",
            },
            "mode must be pre_call or post_call",
        ),
        (
            {
                "guardrail": "src.guardrails.presidio.PresidioGuardrail",
                "default_action": "warn",
            },
            "default_action must be block or log",
        ),
    ],
)
async def test_update_guardrails_rejects_unsupported_runtime_options(
    client,
    test_app,
    deltallm_params: dict[str, object],
    expected_detail: str,
) -> None:
    _prepare_guardrail_config(test_app)

    response = await client.put(
        "/ui/api/guardrails",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "guardrails": [
                {
                    "guardrail_name": "bad-guardrail",
                    "deltallm_params": deltallm_params,
                }
            ]
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == expected_detail


@pytest.mark.asyncio
async def test_update_guardrails_rejects_blank_lakera_api_key(client, test_app) -> None:
    _prepare_guardrail_config(test_app)

    response = await client.put(
        "/ui/api/guardrails",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "guardrails": [
                {
                    "guardrail_name": "lakera",
                    "deltallm_params": {
                        "guardrail": "src.guardrails.lakera.LakeraGuardrail",
                        "api_key": "",
                    },
                }
            ]
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Lakera API key is required"


@pytest.mark.asyncio
async def test_update_guardrails_rejects_unsupported_presidio_entities_in_fallback(
    client,
    test_app,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_guardrail_config(test_app)
    monkeypatch.setattr("src.guardrails.catalog.presidio_engine_mode", lambda: "regex_fallback")

    response = await client.put(
        "/ui/api/guardrails",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "guardrails": [
                {
                    "guardrail_name": "presidio",
                    "deltallm_params": {
                        "guardrail": "src.guardrails.presidio.PresidioGuardrail",
                        "entities": ["EMAIL_ADDRESS", "PERSON"],
                    },
                }
            ]
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Full Presidio engine is not installed. Unsupported entities in fallback mode: PERSON"
    )
