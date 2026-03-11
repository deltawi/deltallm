from __future__ import annotations

import base64
from typing import Any


def _normalize_header_name(value: str) -> str:
    return "-".join(part for part in value.strip().lower().split("-") if part)


def build_server_headers(*, auth_mode: str, auth_config: dict[str, Any] | None = None) -> dict[str, str]:
    config = auth_config or {}
    mode = str(auth_mode or "none").strip().lower()
    if mode in {"", "none"}:
        return {}
    if mode == "bearer":
        token = str(config.get("token") or "").strip()
        return {"Authorization": f"Bearer {token}"} if token else {}
    if mode == "basic":
        username = str(config.get("username") or "")
        password = str(config.get("password") or "")
        encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}
    if mode == "header_map":
        headers = config.get("headers")
        if isinstance(headers, dict):
            return {str(key): str(value) for key, value in headers.items() if str(key).strip() and value is not None}
    return {}


def build_forwarded_headers(
    *,
    request_headers: dict[str, str] | None,
    server_key: str,
    allowlist: list[str] | None,
    prefix: str = "x-deltallm-mcp-",
) -> dict[str, str]:
    headers = request_headers or {}
    allowed = {_normalize_header_name(item) for item in (allowlist or []) if str(item).strip()}
    if not allowed:
        return {}
    normalized_prefix = f"{prefix}{server_key.strip().lower()}-"
    out: dict[str, str] = {}
    for key, value in headers.items():
        normalized_key = key.strip().lower()
        if not normalized_key.startswith(normalized_prefix):
            continue
        forwarded_name = _normalize_header_name(normalized_key[len(normalized_prefix) :])
        if forwarded_name in allowed:
            out[forwarded_name] = str(value)
    return out
