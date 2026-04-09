"""Pure validation/normalization helpers for the admin MCP endpoints."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException, status

from src.api.admin.endpoints.common import optional_int
from src.services.mcp_migration import (
    ORGANIZATION_ROLLOUT_STATES as MCP_MIGRATION_ROLLOUT_STATES,
    ROLLOUT_STATE_ALIASES as MCP_MIGRATION_ROLLOUT_STATE_ALIASES,
)

from src.api.admin.endpoints.mcp.constants import (
    _ALLOWED_AUTH_MODES,
    _ALLOWED_OWNER_SCOPE_TYPES,
    _ALLOWED_SCOPE_TYPES,
    _ALLOWED_TRANSPORTS,
    _MCP_SCOPE_POLICY_MODES,
    _MCP_SCOPE_POLICY_SCOPE_TYPES,
    _SERVER_KEY_ALLOWED,
)


def _normalize_server_key(value: Any) -> str:
    key = str(value or "").strip().lower()
    if not key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="server_key is required")
    if any(ch not in _SERVER_KEY_ALLOWED for ch in key):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="server_key may only contain lowercase letters, numbers, hyphens, and underscores",
        )
    return key


def _normalize_transport(value: Any) -> str:
    transport = str(value or "streamable_http").strip().lower()
    if transport not in _ALLOWED_TRANSPORTS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="transport must be streamable_http")
    return transport


def _normalize_auth_mode(value: Any) -> str:
    mode = str(value or "none").strip().lower()
    if mode not in _ALLOWED_AUTH_MODES:
        allowed = ", ".join(sorted(_ALLOWED_AUTH_MODES))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"auth_mode must be one of: {allowed}")
    return mode


def _normalize_metadata(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="metadata must be an object")
    return dict(value)


def _credentials_present(auth_mode: str, auth_config: dict[str, Any] | None) -> bool:
    config = auth_config or {}
    if auth_mode == "none":
        return False
    if auth_mode == "bearer":
        return bool(str(config.get("token") or "").strip())
    if auth_mode == "basic":
        return bool(str(config.get("username") or "") and str(config.get("password") or ""))
    headers = config.get("headers")
    return isinstance(headers, dict) and any(str(key).strip() and value is not None for key, value in headers.items())


def _sanitize_auth_payload(payload: dict[str, Any] | None, *, auth_mode: str | None = None) -> dict[str, Any] | None:
    if payload is None:
        return None
    sanitized = dict(payload)
    effective_mode = _normalize_auth_mode(auth_mode if auth_mode is not None else sanitized.get("auth_mode"))
    auth_config = sanitized.pop("auth_config", None)
    sanitized["auth_mode"] = effective_mode
    sanitized["auth_config_redacted"] = auth_config is not None
    return sanitized


def _normalize_allowlist(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="forwarded_headers_allowlist must be an array")
    out: list[str] = []
    for item in value:
        header = str(item or "").strip().lower()
        if header and header not in out:
            out.append(header)
    return out


def _validate_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="base_url is required")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="base_url must be a valid http or https URL")
    return raw.rstrip("/")


def _validate_auth_config(auth_mode: str, value: Any) -> dict[str, Any]:
    if value is None:
        config: dict[str, Any] = {}
    elif isinstance(value, dict):
        config = dict(value)
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="auth_config must be an object")

    if auth_mode == "none":
        return {}
    if auth_mode == "bearer":
        token = str(config.get("token") or "").strip()
        if not token:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="auth_config.token is required for bearer auth")
        return {"token": token}
    if auth_mode == "basic":
        username = str(config.get("username") or "")
        password = str(config.get("password") or "")
        if not username or not password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="auth_config.username and auth_config.password are required for basic auth",
            )
        return {"username": username, "password": password}
    headers = config.get("headers")
    if not isinstance(headers, dict) or not headers:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="auth_config.headers is required for header_map auth")
    normalized_headers = {
        str(key).strip(): str(item)
        for key, item in headers.items()
        if str(key).strip() and item is not None
    }
    if not normalized_headers:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="auth_config.headers must include at least one header")
    return {"headers": normalized_headers}


def _validate_scope_type(value: Any) -> str:
    scope_type = str(value or "").strip().lower()
    if scope_type not in _ALLOWED_SCOPE_TYPES:
        allowed = ", ".join(sorted(_ALLOWED_SCOPE_TYPES))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"scope_type must be one of: {allowed}")
    return scope_type


def _validate_mcp_scope_policy_scope_type(value: Any) -> str:
    scope_type = str(value or "").strip().lower()
    if scope_type not in _MCP_SCOPE_POLICY_SCOPE_TYPES:
        allowed = ", ".join(sorted(_MCP_SCOPE_POLICY_SCOPE_TYPES))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"scope_type must be one of: {allowed}")
    return scope_type


def _validate_mcp_scope_policy_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode not in _MCP_SCOPE_POLICY_MODES:
        allowed = ", ".join(sorted(_MCP_SCOPE_POLICY_MODES))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"mode must be one of: {allowed}")
    return mode


def _validate_mcp_migration_rollout_states(values: list[str] | None) -> set[str]:
    if not values:
        return set()
    normalized = {
        MCP_MIGRATION_ROLLOUT_STATE_ALIASES.get(str(value or "").strip().lower(), str(value or "").strip().lower())
        for value in values
        if str(value or "").strip()
    }
    invalid = sorted(value for value in normalized if value not in MCP_MIGRATION_ROLLOUT_STATES)
    if invalid:
        allowed = ", ".join(sorted(MCP_MIGRATION_ROLLOUT_STATES))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"rollout_state must be one of: {allowed}")
    return normalized


def _validate_owner_scope_type(value: Any) -> str:
    owner_scope_type = str(value or "global").strip().lower()
    if owner_scope_type not in _ALLOWED_OWNER_SCOPE_TYPES:
        allowed = ", ".join(sorted(_ALLOWED_OWNER_SCOPE_TYPES))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"owner_scope_type must be one of: {allowed}")
    return owner_scope_type


def _normalize_scope_id(value: Any, *, field_name: str = "scope_id") -> str:
    scope_id = str(value or "").strip()
    if not scope_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} is required")
    return scope_id


def _normalize_tool_name(value: Any) -> str:
    tool_name = str(value or "").strip()
    if not tool_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="tool_name is required")
    return tool_name


def _validate_require_approval(value: Any) -> str | None:
    if value is None:
        return None
    approval = str(value).strip().lower()
    if approval not in {"never", "manual"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='require_approval must be "never" or "manual"')
    return approval


def _normalize_tool_policy_metadata(payload: dict[str, Any]) -> dict[str, Any] | None:
    metadata = _normalize_metadata(payload.get("metadata")) or {}
    timeout_value = payload.get("max_total_execution_time_ms")
    if timeout_value is None:
        timeout_value = payload.get("max_total_mcp_execution_time_ms")
    if timeout_value is not None:
        metadata["max_total_mcp_execution_time_ms"] = optional_int(timeout_value, "max_total_execution_time_ms")
    elif "max_total_mcp_execution_time_ms" in metadata:
        metadata["max_total_mcp_execution_time_ms"] = optional_int(
            metadata.get("max_total_mcp_execution_time_ms"),
            "metadata.max_total_mcp_execution_time_ms",
        )
    return metadata or None


__all__ = [
    "_normalize_server_key",
    "_normalize_transport",
    "_normalize_auth_mode",
    "_normalize_metadata",
    "_credentials_present",
    "_sanitize_auth_payload",
    "_normalize_allowlist",
    "_validate_url",
    "_validate_auth_config",
    "_validate_scope_type",
    "_validate_mcp_scope_policy_scope_type",
    "_validate_mcp_scope_policy_mode",
    "_validate_mcp_migration_rollout_states",
    "_validate_owner_scope_type",
    "_normalize_scope_id",
    "_normalize_tool_name",
    "_validate_require_approval",
    "_normalize_tool_policy_metadata",
]
