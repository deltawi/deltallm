from __future__ import annotations

from typing import Any

_MISSING = object()


def batch_runtime_setting(app: Any, cfg: Any, field_name: str, *, default: Any) -> Any:
    general_settings = getattr(cfg, "general_settings", None)
    value = _explicit_general_setting(general_settings, field_name)
    if value is not _MISSING:
        return value
    settings = getattr(getattr(app, "state", None), "settings", None)
    return getattr(settings, field_name, default)


def _explicit_general_setting(general_settings: Any, field_name: str) -> Any:
    if general_settings is None:
        return _MISSING
    fields_set = getattr(general_settings, "model_fields_set", None)
    if fields_set is not None and field_name not in fields_set:
        return _MISSING
    return getattr(general_settings, field_name, _MISSING)
