"""The privacy guard must not clear logger caches for unchanged levels."""

import logging

import pytest

from src.outbound.http import _HTTPCORE_TRACE_LOGGERS, suppress_httpcore_debug_traces
from src.runtime_logging import apply_runtime_log_level

pytestmark = pytest.mark.hermetic


def test_guard_does_not_write_an_unchanged_logger_level(monkeypatch):
    originals = tuple(logger.level for logger in _HTTPCORE_TRACE_LOGGERS)
    try:
        for logger in _HTTPCORE_TRACE_LOGGERS:
            logger.setLevel(logging.INFO)
        writes = []
        original = logging.Logger.setLevel

        def record(logger, level):
            writes.append(logger.name)
            original(logger, level)

        monkeypatch.setattr(logging.Logger, "setLevel", record)
        with suppress_httpcore_debug_traces(), suppress_httpcore_debug_traces():
            assert all(not logger.isEnabledFor(logging.DEBUG) for logger in _HTTPCORE_TRACE_LOGGERS)
        assert writes == []
    finally:
        for logger, level in zip(_HTTPCORE_TRACE_LOGGERS, originals, strict=True):
            logger.setLevel(level)


@pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARNING", "ERROR"])
def test_startup_owns_safe_trace_levels(level):
    loggers = (logging.getLogger(), logging.getLogger("httpx"), *_HTTPCORE_TRACE_LOGGERS)
    originals = tuple(logger.level for logger in loggers)
    try:
        for logger in _HTTPCORE_TRACE_LOGGERS:
            logger.setLevel(logging.NOTSET)
        apply_runtime_log_level(level)
        expected = max(logging.INFO, getattr(logging, level))
        assert all(logger.level == expected for logger in _HTTPCORE_TRACE_LOGGERS)
    finally:
        for logger, old in zip(loggers, originals, strict=True):
            logger.setLevel(old)
