from __future__ import annotations

from typing import Literal

from src.mcp.exceptions import MCPError, MCPTransportError

ConnectionRequiredReason = Literal["not_connected", "needs_reconnect", "user_key_required"]


class MCPOAuthError(MCPTransportError):
    """A safe, operator-readable OAuth failure. Messages never hold tokens or upstream bodies."""


class MCPOAuthConfigurationError(MCPOAuthError):
    """The gateway or the server is not configured for per-user OAuth."""


class MCPOAuthDiscoveryError(MCPOAuthError):
    pass


class MCPOAuthTokenError(MCPOAuthError):
    def __init__(self, message: str, *, error_code: str | None = None) -> None:
        self.error_code = error_code
        super().__init__(message)


class MCPConnectionRequiredError(MCPError):
    """The caller has no usable OAuth connection for this server."""

    def __init__(self, *, server_key: str, reason: ConnectionRequiredReason) -> None:
        self.server_key = server_key
        self.reason: ConnectionRequiredReason = reason
        super().__init__(f"MCP server '{server_key}' needs a user connection ({reason})")


# Reason codes the callback may put in the browser redirect. Upstream error text
# is never copied into the URL.
CALLBACK_REASONS = frozenset(
    {
        "sign_in_required",
        "invalid_state",
        "expired_state",
        "wrong_account",
        "server_unavailable",
        "denied",
        "client_changed",
        "token_exchange_failed",
        "configuration",
    }
)


class MCPOAuthCallbackError(MCPError):
    def __init__(self, reason: str, *, server_key: str | None = None) -> None:
        self.reason = reason if reason in CALLBACK_REASONS else "token_exchange_failed"
        self.server_key = server_key
        super().__init__(f"MCP OAuth callback failed: {self.reason}")
