"""Per-user credentials for tool calls on ``oauth2`` MCP servers.

The gateway calls this module only for servers with ``auth_mode == "oauth2"``.
Other servers keep their static headers and cause no extra SQL or Redis calls.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from src.mcp.exceptions import MCPAuthError
from src.mcp.models import MCPServerConfig, MCPToolCallResult
from src.mcp.transport_http import StreamableHTTPMCPClient
from src.models.responses import UserAPIKeyAuth

from .errors import ConnectionRequiredReason, MCPConnectionRequiredError
from .service import MCPOAuthService

CONNECTION_REQUIRED_ERROR_TYPE = "mcp_connection_required"
USER_KEY_REQUIRED_ERROR_TYPE = "mcp_user_key_required"
USER_KEY_REQUIRED_MESSAGE = "This key is not owned by a user. Per-user OAuth tools need a user key."


@dataclass(frozen=True)
class OAuthToolCall:
    config: MCPServerConfig
    account_id: str
    token_version: int


def connection_required_result(
    *,
    server_key: str,
    server_name: str,
    reason: ConnectionRequiredReason,
    connect_url: str | None,
) -> MCPToolCallResult:
    """The tool result for a call that has no usable user connection.

    The tool stays in tools/list. The model (chat) or the MCP client gets an
    error result with a link to the Connected accounts page.
    """
    if reason == "user_key_required":
        error: dict[str, Any] = {
            "type": USER_KEY_REQUIRED_ERROR_TYPE,
            "server": server_key,
            "message": USER_KEY_REQUIRED_MESSAGE,
        }
        text = USER_KEY_REQUIRED_MESSAGE
    else:
        message = (
            f"Connect your {server_name} account again to use this tool."
            if reason == "needs_reconnect"
            else f"Connect your {server_name} account to use this tool."
        )
        error = {"type": CONNECTION_REQUIRED_ERROR_TYPE, "server": server_key, "message": message}
        if reason == "needs_reconnect":
            error["reason"] = "needs_reconnect"
        if connect_url:
            error["connect_url"] = connect_url
        text = f"{message}\nOpen {connect_url}" if connect_url else message
    return MCPToolCallResult(
        content=[{"type": "text", "text": text}],
        structured_content={"error": error},
        is_error=True,
        metadata={"error_type": error["type"]},
    )


class MCPOAuthToolAccess:
    def __init__(self, service: MCPOAuthService | None) -> None:
        self.service = service

    async def resolve(
        self, auth: UserAPIKeyAuth, config: MCPServerConfig
    ) -> OAuthToolCall | MCPToolCallResult:
        """Return the server config with the user's token, or the error result."""
        account_id = str(auth.owner_account_id or "").strip()
        if not account_id:
            return self._required(config, "user_key_required")
        if self.service is None:
            return self._required(config, "not_connected")
        try:
            token = await self.service.access_token_for(
                server_id=config.server_id,
                server_key=config.server_key,
                auth_config=config.auth_config,
                account_id=account_id,
            )
        except MCPConnectionRequiredError as exc:
            return self._required(config, exc.reason)
        return OAuthToolCall(
            config=replace(config, oauth_access_token=token.value),
            account_id=account_id,
            token_version=token.token_version,
        )

    async def call_tool(
        self,
        transport: StreamableHTTPMCPClient,
        call: OAuthToolCall,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        request_headers: dict[str, str] | None,
    ) -> MCPToolCallResult:
        try:
            return await transport.call_tool(
                call.config,
                tool_name=tool_name,
                arguments=arguments,
                request_headers=request_headers,
            )
        except MCPAuthError:
            if self.service is None:
                raise
        # HTTP 401/403 means the server did not run the tool, so one retry with a
        # refreshed token is safe.
        try:
            token = await self.service.access_token_for(
                server_id=call.config.server_id,
                server_key=call.config.server_key,
                auth_config=call.config.auth_config,
                account_id=call.account_id,
                rejected_token_version=call.token_version,
            )
        except MCPConnectionRequiredError as exc:
            return self._required(call.config, exc.reason)
        return await transport.call_tool(
            replace(call.config, oauth_access_token=token.value),
            tool_name=tool_name,
            arguments=arguments,
            request_headers=request_headers,
        )

    def _required(
        self, config: MCPServerConfig, reason: ConnectionRequiredReason
    ) -> MCPToolCallResult:
        return connection_required_result(
            server_key=config.server_key,
            server_name=config.name,
            reason=reason,
            connect_url=self.service.connect_url(config.server_key) if self.service else None,
        )
