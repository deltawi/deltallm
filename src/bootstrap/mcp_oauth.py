"""Construct the per-user MCP OAuth service."""

from __future__ import annotations

import logging
from typing import Any

from src.mcp.oauth.client import MCPOAuthProtocolClient
from src.mcp.oauth.service import OAUTH2_AUTH_MODE, MCPOAuthService
from src.secret_envelope import SecretEnvelopeCipher, decode_envelope_key

logger = logging.getLogger(__name__)


def build_mcp_oauth_service(app: Any, general_settings: Any) -> MCPOAuthService | None:
    """Return the service, or None when the runtime has no OAuth HTTP or repository.

    The encryption key is optional at startup. It becomes required when an
    administrator saves an oauth2 server, and the startup log names it when an
    oauth2 server exists without it.
    """
    http = getattr(app.state, "mcp_oauth_http", None)
    repository = getattr(app.state, "mcp_oauth_repository", None)
    mcp_repository = getattr(app.state, "mcp_repository", None)
    if http is None or repository is None or mcp_repository is None:
        return None
    key = getattr(general_settings, "mcp_oauth_encryption_key", None)
    cipher = (
        SecretEnvelopeCipher(
            decode_envelope_key(key, key_name="mcp_oauth_encryption_key"),
            key_name="mcp_oauth_encryption_key",
        )
        if key is not None
        else None
    )

    def _public_base_url() -> str | None:
        config = getattr(app.state, "app_config", None)
        current = getattr(config, "general_settings", None) or general_settings
        return getattr(current, "email_base_url", None)

    service = MCPOAuthService(
        repository=repository,
        protocol=MCPOAuthProtocolClient(http),
        cipher=cipher,
        public_base_url=_public_base_url,
        server_loader=mcp_repository.get_server,
    )
    governance = getattr(app.state, "mcp_governance_service", None)
    servers = governance.list_enabled_servers() if governance is not None else []
    oauth_server_keys = sorted(
        server.server_key for server in servers if server.auth_mode == OAUTH2_AUTH_MODE
    )
    if oauth_server_keys and cipher is None:
        logger.error(
            "MCP servers use auth_mode oauth2 but general_settings.mcp_oauth_encryption_key "
            "is not set; their tool calls fail until you set the key",
            extra={"server_keys": oauth_server_keys[:20]},
        )
    if oauth_server_keys and service.public_base_url() is None:
        logger.error(
            "MCP servers use auth_mode oauth2 but general_settings.email_base_url is not set; "
            "users cannot connect and tool errors have no connect link",
            extra={"server_keys": oauth_server_keys[:20]},
        )
    return service
