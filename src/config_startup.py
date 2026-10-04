"""Resolve explicit configuration before environment defaults for owned runtimes."""

from dataclasses import fields
from typing import Any


def startup_field_values(
    defaults: Any, general: object, environment: object, *, prefix: str
) -> dict[str, Any]:
    explicit = getattr(general, "model_fields_set", None)
    values: dict[str, Any] = {}
    for field in fields(defaults):
        name = prefix + field.name
        values[field.name] = (
            getattr(general, name)
            if hasattr(general, name) and (explicit is None or name in explicit)
            else getattr(environment, name, getattr(defaults, field.name))
        )
    return values
