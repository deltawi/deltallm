from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import SecretStr

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 5.0
_shared_client: httpx.AsyncClient | None = None


@dataclass(frozen=True)
class WebhookResult:
    ok: bool
    status_code: int | None = None
    error: str | None = None


def _get_shared_client(timeout_seconds: float) -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _shared_client


async def close_shared_client() -> None:
    """Close the module-global webhook client on app shutdown."""
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
    _shared_client = None


async def post_webhook(
    *,
    url: SecretStr,
    json_body: dict[str, Any],
    client: httpx.AsyncClient | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> WebhookResult:
    """POST a JSON body to a webhook, with one retry. Never raises.

    The secret URL is dereferenced only for the request and is never placed in
    logs, so it stays redacted.
    """
    http = client or _get_shared_client(timeout_seconds)
    target = url.get_secret_value()
    last_error: str | None = None
    for attempt in range(2):
        try:
            response = await http.post(target, json=json_body)
            if response.status_code >= 500:
                last_error = f"http_{response.status_code}"
                continue
            ok = response.status_code < 400
            return WebhookResult(ok=ok, status_code=response.status_code, error=None if ok else f"http_{response.status_code}")
        except httpx.HTTPError as exc:
            last_error = type(exc).__name__
            logger.warning("webhook post failed", extra={"attempt": attempt, "error": last_error})
    return WebhookResult(ok=False, status_code=None, error=last_error)
