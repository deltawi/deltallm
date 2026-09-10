from __future__ import annotations

import asyncio
from contextlib import contextmanager
import logging
from threading import Lock
from typing import Iterator

import httpx

_HTTPCORE_TRACE_LOGGERS = tuple(
    logging.getLogger(name)
    for name in (
        "httpcore",
        "httpcore.connection",
        "httpcore.http11",
        "httpcore.http2",
        "httpcore.proxy",
        "httpcore.socks",
    )
)
_HTTPCORE_LOG_GUARD_LOCK = Lock()
_httpcore_log_guard_count = 0
_httpcore_log_guard_previous_levels: tuple[int, ...] | None = None
_RESPONSE_CLOSE_GRACE_SECONDS = 1.0


@contextmanager
def suppress_httpcore_debug_traces() -> Iterator[None]:
    """Prevent dependency traces from logging customer delivery material.

    httpcore's DEBUG traces include TLS SNI hostnames and complete response
    headers. The guard is process-wide because Python logger levels are global,
    and reference-counted so concurrent outbound operations cannot restore DEBUG
    while another attempt is still active.
    """

    global _httpcore_log_guard_count, _httpcore_log_guard_previous_levels

    with _HTTPCORE_LOG_GUARD_LOCK:
        if _httpcore_log_guard_count == 0:
            _httpcore_log_guard_previous_levels = tuple(
                logger.level for logger in _HTTPCORE_TRACE_LOGGERS
            )
            for logger in _HTTPCORE_TRACE_LOGGERS:
                logger.setLevel(max(logging.INFO, logger.getEffectiveLevel()))
        _httpcore_log_guard_count += 1
    try:
        yield
    finally:
        with _HTTPCORE_LOG_GUARD_LOCK:
            _httpcore_log_guard_count -= 1
            if _httpcore_log_guard_count == 0:
                assert _httpcore_log_guard_previous_levels is not None
                for logger, previous_level in zip(
                    _HTTPCORE_TRACE_LOGGERS,
                    _httpcore_log_guard_previous_levels,
                    strict=True,
                ):
                    logger.setLevel(previous_level)
                _httpcore_log_guard_previous_levels = None


async def close_response_bounded(response: httpx.Response, *, deadline: float) -> None:
    remaining = deadline - asyncio.get_running_loop().time()
    try:
        async with asyncio.timeout(max(0.001, min(_RESPONSE_CLOSE_GRACE_SECONDS, remaining))):
            await response.aclose()
    except Exception:
        # Cleanup cannot expose upstream material or cause a duplicate operation.
        pass
