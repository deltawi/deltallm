"""HTTP access to the gateway under test.

Credentials stay in request headers or the session cookie jar. Error text keeps
only a short, bounded part of the response body, so a summary never contains a
full upstream body.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import httpx

MAX_ERROR_TEXT = 300


class GatewayError(RuntimeError):
    """A gateway call returned an unexpected status."""

    def __init__(self, action: str, status_code: int, detail: str) -> None:
        super().__init__(f"{action}: HTTP {status_code}: {detail}")
        self.action = action
        self.status_code = status_code
        self.detail = detail


# Errors that a call to a gateway can cause: a refused call, a network error, or a
# response that does not have the expected shape.
CALL_ERRORS: tuple[type[Exception], ...] = (
    GatewayError,
    httpx.HTTPError,
    AttributeError,
    KeyError,
    TypeError,
    ValueError,
)


def describe_error(exc: Exception) -> str:
    if isinstance(exc, GatewayError):
        return str(exc)
    if isinstance(exc, (AttributeError, KeyError, TypeError, ValueError)):
        return f"unexpected response ({type(exc).__name__}: {exc})"
    return f"{type(exc).__name__}: {exc}"


def error_text(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        text = response.text
    else:
        detail: Any = payload
        if isinstance(payload, dict):
            detail = payload.get("detail", payload.get("error", payload))
        if isinstance(detail, dict):
            detail = detail.get("message") or detail.get("code") or detail
        text = str(detail)
    return " ".join(text.split())[:MAX_ERROR_TEXT]


class GatewayClient:
    """One credential against one gateway. Safe to share between threads."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float,
        bearer: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    def clear_cookies(self) -> None:
        self._http.cookies.clear()

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> GatewayClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        return self._http.request(method, path, **kwargs)

    def call(
        self,
        method: str,
        path: str,
        action: str,
        *,
        expect: tuple[int, ...] = (200,),
        **kwargs: Any,
    ) -> httpx.Response:
        response = self.request(method, path, **kwargs)
        if response.status_code not in expect:
            raise GatewayError(action, response.status_code, error_text(response))
        return response

    def json(self, method: str, path: str, action: str, **kwargs: Any) -> Any:
        response = self.call(method, path, action, **kwargs)
        try:
            return response.json()
        except ValueError as exc:
            raise GatewayError(action, response.status_code, "response is not JSON") from exc

    @contextmanager
    def stream(self, path: str, *, json: Mapping[str, Any]) -> Iterator[httpx.Response]:
        with self._http.stream("POST", path, json=dict(json)) as response:
            yield response
