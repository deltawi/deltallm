"""Generate the complete GeneralSettings reference from Pydantic metadata."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts.docs.generated_artifact import write_or_check
from src.config import GeneralSettings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = PROJECT_ROOT / "docs" / "configuration" / "general-settings-reference.md"

GROUP_PREFIXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Authentication, identity, and sessions",
        (
            "master_key",
            "salt_key",
            "deltallm_key_",
            "enable_jwt_",
            "jwt_",
            "custom_auth",
            "enable_sso",
            "sso_",
            "session_",
            "platform_",
            "mfa_",
            "self_registration",
        ),
    ),
    ("Database and telemetry ingestion", ("database_", "telemetry_", "spend_ingestion_")),
    ("Redis and caching", ("redis_", "cache_", "prompt_cache_", "prompt_negative_")),
    ("Gateway and upstream capacity", ("gateway_", "preflight_", "upstream_http_")),
    ("Spend, budgets, and reporting", ("spend_", "budget_")),
    ("Organizations and governance", ("organization_", "governance_", "callable_target_")),
    (
        "Notifications and email",
        ("key_lifecycle_", "slack_", "email_", "smtp_", "resend_", "sendgrid_"),
    ),
    ("Model deployments and routing", ("model_deployment_", "failover_", "routing_")),
    ("Batch execution", ("embeddings_batch_", "batch_")),
    ("Tier policy and capacity", ("tier_",)),
    ("Audit", ("audit_",)),
    ("UI and branding", ("ui_",)),
)

CONSTRAINT_KEYS = {
    "exclusiveMaximum": "<",
    "exclusiveMinimum": ">",
    "maxItems": "max items",
    "maxLength": "max length",
    "maximum": "≤",
    "minItems": "min items",
    "minLength": "min length",
    "minimum": "≥",
    "multipleOf": "multiple of",
    "pattern": "pattern",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the artifact is stale")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _group_for(name: str) -> str:
    for title, prefixes in GROUP_PREFIXES:
        if name.startswith(prefixes):
            return title
    return "Core runtime settings"


def _schema_type(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        return str(schema["$ref"]).rsplit("/", 1)[-1]
    if "enum" in schema:
        return " or ".join(json.dumps(value) for value in schema["enum"])
    if "const" in schema:
        return json.dumps(schema["const"])
    if "anyOf" in schema:
        rendered = dict.fromkeys(_schema_type(item) for item in schema["anyOf"])
        return " or ".join(item for item in rendered if item)
    schema_type = schema.get("type")
    if schema_type == "array":
        items = schema.get("items")
        item_type = _schema_type(items) if isinstance(items, dict) else "value"
        return f"array of {item_type}"
    if schema_type == "object":
        values = schema.get("additionalProperties")
        value_type = _schema_type(values) if isinstance(values, dict) else "value"
        return f"object of {value_type}"
    return str(schema_type or "value")


def _constraints(schema: dict[str, Any]) -> str:
    found: list[str] = []

    def visit(node: object) -> None:
        if isinstance(node, dict):
            for key, label in CONSTRAINT_KEYS.items():
                if key in node:
                    found.append(f"{label} {node[key]}")
            for key in ("anyOf", "allOf", "items"):
                value = node.get(key)
                if isinstance(value, list):
                    for item in value:
                        visit(item)
                elif isinstance(value, dict):
                    visit(value)

    visit(schema)
    return "; ".join(dict.fromkeys(found)) or "—"


def _default(schema: dict[str, Any], *, sensitive: bool, required: bool) -> str:
    if required:
        return "Required"
    if "default" not in schema:
        return "Factory default"
    value = schema["default"]
    if sensitive and value is not None:
        return "`<redacted>`"
    return f"`{json.dumps(value, ensure_ascii=False, default=str)}`"


def _handling(name: str, annotation: object) -> str:
    annotation_text = str(annotation)
    secret_suffixes = (
        "_api_key",
        "_client_secret",
        "_encryption_key",
        "_password",
        "_signing_secret",
        "_webhook_url",
    )
    if "SecretStr" in annotation_text or name in {"master_key", "salt_key"}:
        return "Secret"
    if name.endswith(secret_suffixes):
        return "Secret"
    if name in {"database_url", "redis_url"}:
        return "May contain credentials"
    return "Ordinary"


def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def generate_reference() -> tuple[str, int]:
    schema = GeneralSettings.model_json_schema()
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("GeneralSettings JSON schema has no properties")

    grouped: dict[str, list[str]] = {}
    for name in GeneralSettings.model_fields:
        grouped.setdefault(_group_for(name), []).append(name)

    lines = [
        "<!-- Generated by scripts/docs/generate_config_reference.py; do not edit manually. -->",
        "# Complete General Settings Index",
        "",
        (
            "This reference is generated from `GeneralSettings` and covers all "
            f"**{len(properties)}** declared fields. Use `general_settings` in `config.yaml`; "
            "values can reference an environment variable with the `os.environ/NAME` form."
        ),
        "",
        (
            "The generated schema is the field-coverage source of truth. The "
            "[General settings guide](general.md) explains operational choices, precedence, "
            "and safe examples. A default shown here is not automatically a production "
            "recommendation."
        ),
        "",
    ]

    for group_title in [title for title, _ in GROUP_PREFIXES] + ["Core runtime settings"]:
        names = grouped.get(group_title, [])
        if not names:
            continue
        lines.extend(
            [
                f"## {group_title}",
                "",
                "| Setting | Type | Default | Constraints | Handling |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for name in sorted(names):
            property_schema = properties.get(name)
            if not isinstance(property_schema, dict):
                raise ValueError(f"GeneralSettings field is missing schema metadata: {name}")
            field = GeneralSettings.model_fields[name]
            handling = _handling(name, field.annotation)
            values = (
                f"`{name}`",
                _schema_type(property_schema),
                _default(
                    property_schema,
                    sensitive=handling == "Secret",
                    required=field.is_required(),
                ),
                _constraints(property_schema),
                handling,
            )
            lines.append("| " + " | ".join(_escape_cell(value) for value in values) + " |")
        lines.append("")

    rendered_names = [name for names in grouped.values() for name in names]
    if set(rendered_names) != set(properties) or len(rendered_names) != len(properties):
        raise ValueError("generated settings coverage does not match GeneralSettings fields")
    return "\n".join(lines), len(properties)


def main() -> int:
    args = _parse_args()
    try:
        content, field_count = generate_reference()
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1
    if not write_or_check(args.output.resolve(), content, check=args.check):
        return 1
    print(f"General settings reference covers {field_count} fields.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
