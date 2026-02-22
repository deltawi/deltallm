from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from src.config import AppConfig
from src.config_runtime.secrets import SecretResolver


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge two dictionaries with `override` taking precedence."""
    merged = deepcopy(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = deep_merge(existing, value)
        else:
            merged[key] = deepcopy(value)
    return merged


def load_yaml_dict(path: str | Path) -> dict[str, Any]:
    cfg_path = Path(path)
    if not cfg_path.exists():
        return {}

    data = yaml.safe_load(cfg_path.read_text())
    if not isinstance(data, dict):
        return {}

    return data


def build_app_config(
    file_config: dict[str, Any],
    db_config: dict[str, Any] | None = None,
    secret_resolver: SecretResolver | None = None,
) -> AppConfig:
    resolver = secret_resolver or SecretResolver()
    merged = deep_merge(file_config, db_config or {})
    resolved = resolver.resolve_tree(merged)
    return AppConfig.model_validate(resolved)
