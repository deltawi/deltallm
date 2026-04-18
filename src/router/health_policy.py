from __future__ import annotations

import httpx

from src.models.errors import InvalidRequestError, ProxyError, TimeoutError


def exception_status_code(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    raw_status = getattr(exc, "status_code", None)
    if raw_status is None and response is not None:
        raw_status = getattr(response, "status_code", None)
    try:
        return int(raw_status) if raw_status is not None else None
    except (TypeError, ValueError):
        return None


def is_request_side_client_error(exc: Exception) -> bool:
    explicit = getattr(exc, "affects_deployment_health", None)
    if explicit is False:
        return True

    if isinstance(exc, InvalidRequestError):
        return True

    status_code = exception_status_code(exc)
    return status_code is not None and 400 <= status_code < 500 and status_code != 429


def affects_deployment_health(exc: Exception) -> bool:
    explicit = getattr(exc, "affects_deployment_health", None)
    if explicit is not None:
        return bool(explicit)

    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return True

    if isinstance(exc, httpx.HTTPStatusError):
        return not is_request_side_client_error(exc)

    if isinstance(exc, httpx.TransportError):
        return True

    if isinstance(exc, ProxyError):
        return False

    status_code = exception_status_code(exc)
    if status_code is not None:
        return status_code == 429 or status_code >= 500

    return True
