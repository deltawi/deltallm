"""Dependency-free error envelopes shared by admission and provider boundaries."""

from starlette.responses import JSONResponse


def _anthropic_error_type(status_code: int) -> str:
    if status_code == 400:
        return "invalid_request_error"
    if status_code == 401:
        return "authentication_error"
    if status_code == 403:
        return "permission_error"
    if status_code == 404:
        return "not_found_error"
    if status_code == 413:
        return "request_too_large"
    if status_code == 429:
        return "rate_limit_error"
    if status_code == 503:
        return "overloaded_error"
    return "api_error"


def anthropic_error_payload(
    *,
    status_code: int,
    message: str,
    error_type: str | None = None,
) -> dict[str, object]:
    return {
        "type": "error",
        "error": {"type": error_type or _anthropic_error_type(status_code), "message": message},
    }


def anthropic_error_response(
    *,
    status_code: int,
    message: str,
    error_type: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=anthropic_error_payload(
            status_code=status_code, message=message, error_type=error_type
        ),
        headers=headers or {},
    )
