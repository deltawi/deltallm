from __future__ import annotations

import json
import re
from typing import Any

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("groq_api_key", re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b")),
    ("bearer_token", re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}", re.IGNORECASE)),
)

_SUPPORTED_JSON_TYPES: set[str] = {"string", "number", "integer", "boolean", "object", "array"}


def detect_secret_like_content(value: Any) -> list[str]:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    findings: list[str] = []
    for name, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            findings.append(name)
    return findings


def validate_variables_schema(schema: dict[str, Any] | None, variables: dict[str, Any]) -> list[str]:
    if schema is None:
        return []
    if not isinstance(schema, dict):
        return ["variables_schema must be an object"]

    errors: list[str] = []
    schema_type = schema.get("type")
    if schema_type not in (None, "object"):
        return ["variables_schema.type must be 'object'"]

    required = schema.get("required")
    if isinstance(required, list):
        for field in required:
            if isinstance(field, str) and field not in variables:
                errors.append(f"variables.{field} is required")

    properties = schema.get("properties")
    if isinstance(properties, dict):
        for field, raw_field_schema in properties.items():
            if not isinstance(field, str) or field not in variables:
                continue
            if not isinstance(raw_field_schema, dict):
                continue
            value = variables[field]
            expected_type = raw_field_schema.get("type")
            if isinstance(expected_type, str):
                if expected_type not in _SUPPORTED_JSON_TYPES:
                    errors.append(f"variables_schema.properties.{field}.type is not supported")
                elif not _matches_json_type(value, expected_type):
                    errors.append(f"variables.{field} must be {expected_type}")
            allowed = raw_field_schema.get("enum")
            if isinstance(allowed, list) and value not in allowed:
                errors.append(f"variables.{field} must be one of {allowed}")

    if schema.get("additionalProperties") is False and isinstance(properties, dict):
        allowed_fields = {name for name in properties.keys() if isinstance(name, str)}
        for field in variables.keys():
            if field not in allowed_fields:
                errors.append(f"variables.{field} is not allowed")

    return errors


def render_template_body(template_body: Any, variables: dict[str, Any]) -> Any:
    return _render_value(template_body, variables)


def _render_value(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return _render_string(value, variables)
    if isinstance(value, list):
        return [_render_value(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: _render_value(item, variables) for key, item in value.items()}
    return value


def _render_string(template: str, variables: dict[str, Any]) -> str:
    class _StrictMap(dict[str, Any]):
        def __missing__(self, key: str) -> Any:
            raise KeyError(key)

    try:
        return template.format_map(_StrictMap(variables))
    except KeyError as exc:
        missing = str(exc).strip("'")
        raise ValueError(f"variables.{missing} is required by template") from exc


def _matches_json_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    return False
