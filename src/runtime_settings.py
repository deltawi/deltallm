"""Resolve explicit general settings before environment settings."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar, cast

if TYPE_CHECKING:
    from src.config import GeneralSettings, Settings

T = TypeVar("T")
_MISSING = object()


def resolve_general_setting(
    general_settings: GeneralSettings | None,
    runtime_settings: Settings | None,
    field_name: str,
    default: T,
) -> T:
    if general_settings is not None:
        fields_set = getattr(general_settings, "model_fields_set", None)
        if fields_set is None or field_name in fields_set:
            value = getattr(general_settings, field_name, _MISSING)
            if value is not _MISSING:
                return cast(T, value)
    return cast(T, getattr(runtime_settings, field_name, default))
