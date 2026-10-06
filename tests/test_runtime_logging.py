import logging

import pytest

from src.config import AppConfig, GeneralSettings, Settings
from src.lifecycle_settings import LifecycleSettings
from src.runtime_logging import (
    access_log_enabled,
    apply_runtime_log_level,
    effective_startup_log_level,
    normalize_log_level,
)
from src.startup_config import StartupConfig

pytestmark = pytest.mark.hermetic


@pytest.mark.parametrize("file_level,expected", [(None, "WARNING"), ("DEBUG", "DEBUG")])
def test_startup_log_level_keeps_explicit_file_precedence(file_level, expected):
    general = GeneralSettings() if file_level is None else GeneralSettings(log_level=file_level)
    startup = StartupConfig(
        settings=Settings(log_level="WARNING"),
        file_config={},
        app_config=AppConfig(general_settings=general),
        lifecycle=LifecycleSettings(),
    )
    assert effective_startup_log_level(startup) == expected


@pytest.mark.parametrize(
    "level,dependency_level,access",
    [
        ("DEBUG", logging.DEBUG, True),
        ("INFO", logging.WARNING, False),
        ("WARNING", logging.WARNING, False),
        ("ERROR", logging.ERROR, False),
    ],
)
def test_dependency_request_logs_and_access_logs_require_debug(level, dependency_level, access):
    root, dependency = logging.getLogger(), logging.getLogger("httpx")
    original = root.level, dependency.level
    try:
        assert apply_runtime_log_level(level.lower()) == level
        assert root.level == getattr(logging, level)
        assert dependency.level == dependency_level
        assert access_log_enabled(level) is access
    finally:
        root.setLevel(original[0])
        dependency.setLevel(original[1])


@pytest.mark.parametrize("value", ["TRACE", "tenant-controlled-level", None])
def test_unknown_log_levels_are_rejected(value):
    with pytest.raises(ValueError, match="log level"):
        normalize_log_level(value)
