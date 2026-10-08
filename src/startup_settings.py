from __future__ import annotations

from typing import TypeVar, cast

T = TypeVar("T")


def startup_setting(general: object, settings: object, field_name: str, default: T) -> T:
    """Explicit YAML takes precedence over environment settings and defaults."""
    fields_set = getattr(general, "model_fields_set", None)
    if fields_set is None or field_name in fields_set:
        value = getattr(general, field_name, None)
        if value is not None:
            return cast(T, value)
    return cast(T, getattr(settings, field_name, default))
