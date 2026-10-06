"""One log-level owner for the managed runtime and admin settings."""

from __future__ import annotations

import logging

from src.startup_config import StartupConfig

_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


def effective_startup_log_level(startup: StartupConfig) -> str:
    general = startup.app_config.general_settings
    value = (
        general.log_level if "log_level" in general.model_fields_set else startup.settings.log_level
    )
    return normalize_log_level(value)


def normalize_log_level(value: object) -> str:
    level = str(value).upper()
    if level not in _LEVELS:
        raise ValueError("log level must be DEBUG, INFO, WARNING, or ERROR")
    return level


def apply_runtime_log_level(value: object) -> str:
    level = normalize_log_level(value)
    numeric = _LEVELS[level]
    logging.basicConfig(level=numeric)
    logging.getLogger().setLevel(numeric)
    dependency_level = logging.DEBUG if level == "DEBUG" else max(logging.WARNING, numeric)
    logging.getLogger("httpx").setLevel(dependency_level)
    return level


def access_log_enabled(level: object) -> bool:
    return normalize_log_level(level) == "DEBUG"
