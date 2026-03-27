from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.guardrails.lakera import LakeraGuardrail
from src.guardrails.presidio import (
    PresidioGuardrail,
    PRESIDIO_FALLBACK_SUPPORTED_ENTITIES,
    presidio_engine_mode,
)


SUPPORTED_GUARDRAIL_MODES = ("pre_call", "post_call")
SUPPORTED_GUARDRAIL_ACTIONS = ("block", "log")

PRESIDIO_CLASS_PATH = f"{PresidioGuardrail.__module__}.{PresidioGuardrail.__name__}"
LAKERA_CLASS_PATH = f"{LakeraGuardrail.__module__}.{LakeraGuardrail.__name__}"


_BUILTIN_GUARDRAIL_PRESETS: tuple[dict[str, Any], ...] = (
    {
        "preset_id": "presidio_pii",
        "label": "PII Detection",
        "description": "Detect or anonymize sensitive personal data in prompts and outputs.",
        "type_label": "PII Detection (Presidio)",
        "class_path": PRESIDIO_CLASS_PATH,
        "supported_modes": ["pre_call", "post_call"],
        "supported_actions": ["block", "log"],
        "fields": [
            {
                "key": "anonymize",
                "label": "Anonymize detected data",
                "input": "boolean",
                "default_value": True,
                "help_text": "Replace detected PII instead of only blocking the request.",
            },
            {
                "key": "threshold",
                "label": "Detection threshold",
                "input": "number",
                "default_value": 0.5,
                "min": 0,
                "max": 1,
                "step": 0.1,
            },
            {
                "key": "language",
                "label": "Language",
                "input": "text",
                "default_value": "en",
                "placeholder": "en",
            },
            {
                "key": "entities",
                "label": "Entities to inspect",
                "input": "multiselect",
                "default_value": list(PresidioGuardrail.SUPPORTED_ENTITIES),
                "options": [
                    {"value": entity, "label": entity.replace("_", " ").title()}
                    for entity in PresidioGuardrail.SUPPORTED_ENTITIES
                ],
            },
        ],
    },
    {
        "preset_id": "lakera_prompt_injection",
        "label": "Prompt Injection Detection",
        "description": "Call Lakera Guard to detect prompt injection and jailbreak-style attacks.",
        "type_label": "Prompt Injection (Lakera)",
        "class_path": LAKERA_CLASS_PATH,
        "supported_modes": ["pre_call"],
        "supported_actions": ["block", "log"],
        "fields": [
            {
                "key": "api_key",
                "label": "Lakera API key",
                "input": "secret",
                "default_value": "",
                "placeholder": "os.environ/LAKERA_API_KEY",
                "help_text": "Use a secret or environment reference when possible.",
            },
            {
                "key": "threshold",
                "label": "Risk threshold",
                "input": "number",
                "default_value": 0.5,
                "min": 0,
                "max": 1,
                "step": 0.1,
            },
            {
                "key": "fail_open",
                "label": "Fail open on provider outage",
                "input": "boolean",
                "default_value": False,
                "help_text": "Allow traffic through if Lakera is unavailable.",
            },
            {
                "key": "timeout",
                "label": "Timeout (seconds)",
                "input": "number",
                "default_value": 10.0,
                "min": 1,
                "max": 60,
                "step": 0.5,
            },
            {
                "key": "api_url",
                "label": "Lakera API URL",
                "input": "text",
                "default_value": LakeraGuardrail.LAKERA_API_URL,
                "advanced": True,
            },
        ],
    },
)


_PRESET_BY_CLASS_PATH = {preset["class_path"]: preset for preset in _BUILTIN_GUARDRAIL_PRESETS}
_PRESET_BY_ID = {preset["preset_id"]: preset for preset in _BUILTIN_GUARDRAIL_PRESETS}


def list_guardrail_presets() -> list[dict[str, Any]]:
    presets = deepcopy(list(_BUILTIN_GUARDRAIL_PRESETS))
    if presidio_engine_mode() != "full":
        fallback_entities = set(PRESIDIO_FALLBACK_SUPPORTED_ENTITIES)
        for preset in presets:
            if preset["preset_id"] != "presidio_pii":
                continue
            for field in preset["fields"]:
                if field["key"] != "entities":
                    continue
                field["default_value"] = list(PRESIDIO_FALLBACK_SUPPORTED_ENTITIES)
                field["help_text"] = (
                    "Full Presidio packages are not installed. "
                    "Only regex-based entities are available in fallback mode."
                )
                options = field.get("options") or []
                for option in options:
                    if option["value"] not in fallback_entities:
                        option["disabled"] = True
                        option["description"] = "Requires the full Presidio engine."
    return presets


def get_guardrail_catalog_capabilities() -> dict[str, Any]:
    return {
        "presidio": {
            "engine_mode": presidio_engine_mode(),
            "fallback_supported_entities": list(PRESIDIO_FALLBACK_SUPPORTED_ENTITIES),
        }
    }


def normalize_guardrail_class_path(class_path: str | None) -> str:
    normalized = str(class_path or "").strip()
    if normalized.startswith("deltallm."):
        candidate = normalized.replace("deltallm.", "src.", 1)
        if candidate in _PRESET_BY_CLASS_PATH:
            return candidate
    return normalized


def get_guardrail_preset_by_class_path(class_path: str | None) -> dict[str, Any] | None:
    normalized = normalize_guardrail_class_path(class_path)
    if not normalized:
        return None
    preset = _PRESET_BY_CLASS_PATH.get(normalized)
    return deepcopy(preset) if preset is not None else None


def get_guardrail_preset(preset_id: str | None) -> dict[str, Any] | None:
    if not preset_id:
        return None
    preset = _PRESET_BY_ID.get(str(preset_id))
    return deepcopy(preset) if preset is not None else None


def guardrail_type_from_class_path(class_path: str) -> str:
    preset = _PRESET_BY_CLASS_PATH.get(normalize_guardrail_class_path(class_path))
    if preset is not None:
        return str(preset["type_label"])
    return "Custom Guardrail"


def guardrail_threshold_from_params(deltallm_params: dict[str, Any]) -> float:
    threshold = deltallm_params.get("threshold")
    if threshold is None:
        threshold = deltallm_params.get("score_threshold")
    if threshold is None:
        threshold = deltallm_params.get("confidence_threshold")
    if threshold is None:
        return 0.5
    try:
        return float(threshold)
    except (TypeError, ValueError):
        return 0.5


def serialize_guardrail_editor_config(deltallm_params: dict[str, Any]) -> dict[str, Any]:
    raw_class_path = str(deltallm_params.get("guardrail") or "")
    class_path = normalize_guardrail_class_path(raw_class_path)
    preset = _PRESET_BY_CLASS_PATH.get(class_path)
    mode = str(deltallm_params.get("mode") or "pre_call")
    default_action = str(deltallm_params.get("default_action") or "block")
    default_on = bool(deltallm_params.get("default_on", True))

    common_keys = {"guardrail", "mode", "default_action", "default_on"}
    field_values: dict[str, Any] = {}

    if preset is not None:
        for field in preset["fields"]:
            key = str(field["key"])
            if key == "threshold":
                field_values[key] = guardrail_threshold_from_params(deltallm_params)
                common_keys.update({"threshold", "score_threshold", "confidence_threshold"})
                continue
            if key == "entities":
                raw_entities = deltallm_params.get(key)
                field_values[key] = list(raw_entities) if isinstance(raw_entities, list) else list(field["default_value"])
                common_keys.add(key)
                continue
            field_values[key] = deltallm_params.get(key, field.get("default_value"))
            common_keys.add(key)

    additional_params = {
        key: value
        for key, value in deltallm_params.items()
        if key not in common_keys and value is not None
    }

    return {
        "preset_id": preset["preset_id"] if preset is not None else None,
        "is_custom": preset is None,
        "class_path": class_path,
        "mode": mode,
        "default_action": default_action,
        "default_on": default_on,
        "field_values": field_values,
        "additional_params": additional_params,
    }


def validate_guardrail_runtime_requirements(deltallm_params: dict[str, Any]) -> None:
    class_path = normalize_guardrail_class_path(str(deltallm_params.get("guardrail") or ""))
    if class_path == LAKERA_CLASS_PATH:
        api_key = str(deltallm_params.get("api_key") or "").strip()
        if not api_key:
            raise ValueError("Lakera API key is required")
        return

    if class_path != PRESIDIO_CLASS_PATH or presidio_engine_mode() == "full":
        return

    raw_entities = deltallm_params.get("entities")
    if isinstance(raw_entities, list):
        entities = [str(entity) for entity in raw_entities]
    else:
        entities = list(PRESIDIO_FALLBACK_SUPPORTED_ENTITIES)

    unsupported = sorted(
        entity for entity in entities if entity not in PRESIDIO_FALLBACK_SUPPORTED_ENTITIES
    )
    if unsupported:
        raise ValueError(
            "Full Presidio engine is not installed. "
            f"Unsupported entities in fallback mode: {', '.join(unsupported)}"
        )
