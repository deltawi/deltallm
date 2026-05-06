from __future__ import annotations

import base64
from typing import Any

PROTECTED_MCP_REQUEST_HEADERS = frozenset(
    {
        "accept",
        "content-type",
        "mcp-protocol-version",
        "mcp-session-id",
    }
)


def _normalize_header_name(value: str) -> str:
    return "-".join(part for part in value.strip().lower().split("-") if part)


def build_server_headers(
    *, auth_mode: str, auth_config: dict[str, Any] | None = None
) -> dict[str, str]:
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
            return {
                str(key): str(value)
                for key, value in headers.items()
                if str(key).strip() and value is not None
            }
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


def filter_protected_mcp_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in headers.items()
        if key.strip().lower() not in PROTECTED_MCP_REQUEST_HEADERS
    }


def build_effective_mcp_forwarded_headers(
    *,
    request_headers: dict[str, str] | None,
    server_key: str,
    allowlist: list[str] | None,
) -> dict[str, str]:
    return filter_protected_mcp_headers(
        build_forwarded_headers(
            request_headers=request_headers,
            server_key=server_key,
            allowlist=allowlist,
        )
    )


def build_effective_mcp_upstream_headers(
    *,
    auth_mode: str,
    auth_config: dict[str, Any] | None,
    request_headers: dict[str, str] | None,
    server_key: str,
    allowlist: list[str] | None,
) -> dict[str, str]:
    return filter_protected_mcp_headers(
        {
            **build_server_headers(auth_mode=auth_mode, auth_config=auth_config),
            **build_forwarded_headers(
                request_headers=request_headers,
                server_key=server_key,
                allowlist=allowlist,
            ),
        }
    )
