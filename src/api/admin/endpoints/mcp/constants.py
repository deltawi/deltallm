"""Static constants for the admin MCP endpoints."""
from __future__ import annotations

_ALLOWED_AUTH_MODES = {"none", "bearer", "basic", "header_map"}
_ALLOWED_SCOPE_TYPES = {"organization", "team", "api_key", "user"}
_ALLOWED_OWNER_SCOPE_TYPES = {"global", "organization"}
_ALLOWED_TRANSPORTS = {"streamable_http"}
_SERVER_KEY_ALLOWED = set("abcdefghijklmnopqrstuvwxyz0123456789-_")
_SCOPE_SPECIFICITY = ("user", "api_key", "team", "organization")
_MCP_SCOPE_POLICY_SCOPE_TYPES = {"team", "api_key", "user"}
_MCP_SCOPE_POLICY_MODES = {"inherit", "restrict"}

__all__ = [
    "_ALLOWED_AUTH_MODES",
    "_ALLOWED_SCOPE_TYPES",
    "_ALLOWED_OWNER_SCOPE_TYPES",
    "_ALLOWED_TRANSPORTS",
    "_SERVER_KEY_ALLOWED",
    "_SCOPE_SPECIFICITY",
    "_MCP_SCOPE_POLICY_SCOPE_TYPES",
    "_MCP_SCOPE_POLICY_MODES",
]
