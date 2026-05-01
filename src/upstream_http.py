from __future__ import annotations

from typing import Any

import httpx


DEFAULT_UPSTREAM_HTTP_CONNECT_TIMEOUT_SECONDS = 10.0
DEFAULT_UPSTREAM_HTTP_READ_TIMEOUT_SECONDS = 300.0
DEFAULT_UPSTREAM_HTTP_WRITE_TIMEOUT_SECONDS = 30.0
DEFAULT_UPSTREAM_HTTP_POOL_TIMEOUT_SECONDS = 10.0
DEFAULT_UPSTREAM_HTTP_MAX_CONNECTIONS = 500
DEFAULT_UPSTREAM_HTTP_MAX_KEEPALIVE_CONNECTIONS = 100
DEFAULT_UPSTREAM_HTTP_KEEPALIVE_EXPIRY_SECONDS = 60.0
HEALTH_CHECK_POOL_TIMEOUT_RATIO = 0.8
MIN_HEALTH_CHECK_POOL_TIMEOUT_SECONDS = 0.1

CONTROL_HTTP_TIMEOUT_SECONDS = 20.0
CONTROL_HTTP_CONNECT_TIMEOUT_SECONDS = 5.0
CONTROL_HTTP_READ_TIMEOUT_SECONDS = 20.0
CONTROL_HTTP_WRITE_TIMEOUT_SECONDS = 10.0
CONTROL_HTTP_POOL_TIMEOUT_SECONDS = 5.0
CONTROL_HTTP_MAX_CONNECTIONS = 100
CONTROL_HTTP_MAX_KEEPALIVE_CONNECTIONS = 20
CONTROL_HTTP_KEEPALIVE_EXPIRY_SECONDS = 30.0


def _setting(settings: Any, name: str, default: float | int) -> float | int:
    value = getattr(settings, name, None)
    return default if value is None else value


def build_upstream_http_timeout(general_settings: Any) -> httpx.Timeout:
    return httpx.Timeout(
        connect=float(
            _setting(
                general_settings,
                "upstream_http_connect_timeout_seconds",
                DEFAULT_UPSTREAM_HTTP_CONNECT_TIMEOUT_SECONDS,
            )
        ),
        read=float(
            _setting(
                general_settings,
                "upstream_http_read_timeout_seconds",
                DEFAULT_UPSTREAM_HTTP_READ_TIMEOUT_SECONDS,
            )
        ),
        write=float(
            _setting(
                general_settings,
                "upstream_http_write_timeout_seconds",
                DEFAULT_UPSTREAM_HTTP_WRITE_TIMEOUT_SECONDS,
            )
        ),
        pool=float(
            _setting(
                general_settings,
                "upstream_http_pool_timeout_seconds",
                DEFAULT_UPSTREAM_HTTP_POOL_TIMEOUT_SECONDS,
            )
        ),
    )


def build_upstream_http_limits(general_settings: Any) -> httpx.Limits:
    return httpx.Limits(
        max_connections=int(
            _setting(
                general_settings,
                "upstream_http_max_connections",
                DEFAULT_UPSTREAM_HTTP_MAX_CONNECTIONS,
            )
        ),
        max_keepalive_connections=int(
            _setting(
                general_settings,
                "upstream_http_max_keepalive_connections",
                DEFAULT_UPSTREAM_HTTP_MAX_KEEPALIVE_CONNECTIONS,
            )
        ),
        keepalive_expiry=float(
            _setting(
                general_settings,
                "upstream_http_keepalive_expiry_seconds",
                DEFAULT_UPSTREAM_HTTP_KEEPALIVE_EXPIRY_SECONDS,
            )
        ),
    )


def build_upstream_http_client(general_settings: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=build_upstream_http_timeout(general_settings),
        limits=build_upstream_http_limits(general_settings),
    )


def build_control_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(
            timeout=CONTROL_HTTP_TIMEOUT_SECONDS,
            connect=CONTROL_HTTP_CONNECT_TIMEOUT_SECONDS,
            read=CONTROL_HTTP_READ_TIMEOUT_SECONDS,
            write=CONTROL_HTTP_WRITE_TIMEOUT_SECONDS,
            pool=CONTROL_HTTP_POOL_TIMEOUT_SECONDS,
        ),
        limits=httpx.Limits(
            max_connections=CONTROL_HTTP_MAX_CONNECTIONS,
            max_keepalive_connections=CONTROL_HTTP_MAX_KEEPALIVE_CONNECTIONS,
            keepalive_expiry=CONTROL_HTTP_KEEPALIVE_EXPIRY_SECONDS,
        ),
    )


def build_control_request_timeout(timeout_seconds: float | int | None = None) -> httpx.Timeout:
    return httpx.Timeout(
        connect=CONTROL_HTTP_CONNECT_TIMEOUT_SECONDS,
        read=float(timeout_seconds or CONTROL_HTTP_READ_TIMEOUT_SECONDS),
        write=CONTROL_HTTP_WRITE_TIMEOUT_SECONDS,
        pool=CONTROL_HTTP_POOL_TIMEOUT_SECONDS,
    )


def configured_timeout_seconds(value: Any) -> float | None:
    if value is None or value == "":
        return None
    parsed = float(value)
    return parsed if parsed > 0 else None


def build_upstream_request_timeout(
    general_settings: Any,
    timeout_seconds: float | int | None,
    *,
    pool_timeout_seconds: float | int | None = None,
) -> httpx.Timeout:
    read_timeout = float(
        timeout_seconds
        if timeout_seconds is not None
        else _setting(
            general_settings,
            "upstream_http_read_timeout_seconds",
            DEFAULT_UPSTREAM_HTTP_READ_TIMEOUT_SECONDS,
        )
    )
    return httpx.Timeout(
        connect=float(
            _setting(
                general_settings,
                "upstream_http_connect_timeout_seconds",
                DEFAULT_UPSTREAM_HTTP_CONNECT_TIMEOUT_SECONDS,
            )
        ),
        read=read_timeout,
        write=float(
            _setting(
                general_settings,
                "upstream_http_write_timeout_seconds",
                DEFAULT_UPSTREAM_HTTP_WRITE_TIMEOUT_SECONDS,
            )
        ),
        pool=float(
            pool_timeout_seconds
            if pool_timeout_seconds is not None
            else _setting(
                general_settings,
                "upstream_http_pool_timeout_seconds",
                DEFAULT_UPSTREAM_HTTP_POOL_TIMEOUT_SECONDS,
            )
        ),
    )


def build_health_check_request_timeout(
    general_settings: Any,
    *,
    read_timeout_seconds: float | int,
    health_check_timeout_seconds: float | int | None,
) -> httpx.Timeout:
    configured_pool_timeout = float(
        _setting(
            general_settings,
            "upstream_http_pool_timeout_seconds",
            DEFAULT_UPSTREAM_HTTP_POOL_TIMEOUT_SECONDS,
        )
    )
    wrapper_timeout = configured_timeout_seconds(health_check_timeout_seconds)
    pool_timeout = configured_pool_timeout
    if wrapper_timeout is not None:
        pool_timeout = min(
            configured_pool_timeout,
            max(MIN_HEALTH_CHECK_POOL_TIMEOUT_SECONDS, wrapper_timeout * HEALTH_CHECK_POOL_TIMEOUT_RATIO),
        )
    return build_upstream_request_timeout(
        general_settings,
        read_timeout_seconds,
        pool_timeout_seconds=pool_timeout,
    )


def get_upstream_http_settings_from_request(request: Any) -> Any:
    app_state = getattr(getattr(request, "app", None), "state", None)
    startup_settings = getattr(app_state, "upstream_http_settings", None)
    if startup_settings is not None:
        return startup_settings
    app_config = getattr(app_state, "app_config", None)
    return getattr(app_config, "general_settings", None)


def build_upstream_request_timeout_for_request(
    request: Any,
    timeout_seconds: float | int | None,
) -> httpx.Timeout:
    return build_upstream_request_timeout(get_upstream_http_settings_from_request(request), timeout_seconds)
