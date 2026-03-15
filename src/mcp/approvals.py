from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from src.db.mcp import MCPApprovalRequestRecord, MCPRepository, MCPToolPolicyRecord
from src.models.responses import UserAPIKeyAuth

from .auth import build_forwarded_headers
from .metrics import record_mcp_approval_request
from .models import MCPServerConfig


@dataclass(frozen=True)
class MCPApprovalAuthorization:
    status: str
    approval_request: MCPApprovalRequestRecord


class MCPApprovalService:
    def __init__(
        self,
        repository: MCPRepository | None,
        *,
        pending_ttl: timedelta = timedelta(hours=24),
        approved_ttl: timedelta = timedelta(minutes=15),
        rejected_ttl: timedelta = timedelta(minutes=15),
    ) -> None:
        self.repository = repository
        self.pending_ttl = pending_ttl
        self.approved_ttl = approved_ttl
        self.rejected_ttl = rejected_ttl

    async def authorize_execution(
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
    ) -> MCPApprovalAuthorization | None:
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
        await self.repository.expire_stale_approval_requests(request_fingerprint=fingerprint)
        active = await self.repository.find_active_approval_request(request_fingerprint=fingerprint)
        if active is not None:
            if active.status == "pending":
                record_mcp_approval_request(server_key=server.server_key, tool_name=tool_name, created=False)
            return MCPApprovalAuthorization(status=active.status, approval_request=active)
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
            expires_at=datetime.now(tz=UTC) + self.pending_ttl,
            metadata={
                "server_key": server.server_key,
                "policy_scope_type": policy.scope_type,
                "policy_scope_id": policy.scope_id,
            },
        )
        if created is not None:
            record_mcp_approval_request(server_key=server.server_key, tool_name=tool_name, created=True)
            return MCPApprovalAuthorization(status=created.status, approval_request=created)
        return None

    def decision_expiry_for_status(self, status: str) -> datetime | None:
        now = datetime.now(tz=UTC)
        if status == "approved":
            return now + self.approved_ttl
        if status == "rejected":
            return now + self.rejected_ttl
        return None

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
            "server_id": server.server_id,
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
