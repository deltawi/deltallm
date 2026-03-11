from __future__ import annotations

import hashlib
import json
from typing import Any

from src.db.mcp import MCPApprovalRequestRecord, MCPRepository, MCPToolPolicyRecord
from src.models.responses import UserAPIKeyAuth

from .auth import build_forwarded_headers
from .metrics import record_mcp_approval_request
from .models import MCPServerConfig


class MCPApprovalService:
    def __init__(self, repository: MCPRepository | None) -> None:
        self.repository = repository

    async def require_approval(
        self,
        *,
        server: MCPServerConfig,
        tool_name: str,
        policy: MCPToolPolicyRecord,
        auth: UserAPIKeyAuth,
        arguments: dict[str, Any] | None,
        request_headers: dict[str, str] | None,
        request_id: str | None,
        correlation_id: str | None,
    ) -> MCPApprovalRequestRecord | None:
        if self.repository is None:
            return None
        fingerprint = self._fingerprint(
            server=server,
            tool_name=tool_name,
            policy=policy,
            auth=auth,
            arguments=arguments,
            request_headers=request_headers,
        )
        pending = await self.repository.find_pending_approval_request(request_fingerprint=fingerprint)
        if pending is not None:
            record_mcp_approval_request(server_key=server.server_key, tool_name=tool_name, created=False)
            return pending
        created = await self.repository.create_approval_request(
            server_id=server.server_id,
            tool_name=tool_name,
            scope_type=policy.scope_type,
            scope_id=policy.scope_id,
            request_fingerprint=fingerprint,
            requested_by_api_key=getattr(auth, "api_key", None),
            requested_by_user=getattr(auth, "user_id", None),
            organization_id=getattr(auth, "organization_id", None),
            request_id=request_id,
            correlation_id=correlation_id,
            arguments_json=arguments or {},
            metadata={
                "server_key": server.server_key,
                "policy_scope_type": policy.scope_type,
                "policy_scope_id": policy.scope_id,
            },
        )
        if created is not None:
            record_mcp_approval_request(server_key=server.server_key, tool_name=tool_name, created=True)
        return created

    @staticmethod
    def _fingerprint(
        *,
        server: MCPServerConfig,
        tool_name: str,
        policy: MCPToolPolicyRecord,
        auth: UserAPIKeyAuth,
        arguments: dict[str, Any] | None,
        request_headers: dict[str, str] | None,
    ) -> str:
        payload = {
            "server_key": server.server_key,
            "tool_name": tool_name,
            "scope_type": policy.scope_type,
            "scope_id": policy.scope_id,
            "api_key": getattr(auth, "api_key", None),
            "forwarded_headers": build_forwarded_headers(
                request_headers=request_headers,
                server_key=server.server_key,
                allowlist=server.forwarded_headers_allowlist,
            ),
            "arguments": arguments or {},
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
