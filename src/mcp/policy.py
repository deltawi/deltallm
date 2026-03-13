from __future__ import annotations

from dataclasses import dataclass

from src.db.mcp import MCPToolPolicyRecord
from src.models.errors import RateLimitError
from src.services.limit_counter import LimitCounter

from .exceptions import MCPRateLimitError


@dataclass(frozen=True)
class MCPToolPolicyLease:
    scope: str
    entity_id: str


class MCPToolPolicyEnforcer:
    def __init__(self, rate_limiter: LimitCounter | None) -> None:
        self.rate_limiter = rate_limiter

    async def acquire(
        self,
        *,
        server_key: str,
        tool_name: str,
        policy: MCPToolPolicyRecord | None,
    ) -> MCPToolPolicyLease | None:
        if policy is None or self.rate_limiter is None:
            return None

        entity_id = self._entity_id(
            scope_type=policy.scope_type,
            scope_id=policy.scope_id,
            server_key=server_key,
            tool_name=tool_name,
        )
        try:
            if policy.max_rpm is not None and policy.max_rpm > 0:
                await self.rate_limiter.check_rate_limit("mcp_tool_rpm", entity_id, policy.max_rpm, 1)
            if policy.max_concurrency is not None and policy.max_concurrency > 0:
                await self.rate_limiter.acquire_parallel("mcp_tool", entity_id, policy.max_concurrency)
                return MCPToolPolicyLease(scope="mcp_tool", entity_id=entity_id)
        except RateLimitError as exc:
            raise MCPRateLimitError(exc.message, retry_after=exc.retry_after) from exc
        return None

    async def release(self, lease: MCPToolPolicyLease | None) -> None:
        if lease is None or self.rate_limiter is None:
            return
        await self.rate_limiter.release_parallel(lease.scope, lease.entity_id)

    @staticmethod
    def _entity_id(*, scope_type: str, scope_id: str, server_key: str, tool_name: str) -> str:
        return f"{scope_type}:{scope_id}:{server_key}:{tool_name}"


def resolve_policy_timeout_ms(policy: MCPToolPolicyRecord | None) -> int | None:
    if policy is None or not isinstance(policy.metadata, dict):
        return None
    raw_value = policy.metadata.get("max_total_mcp_execution_time_ms")
    if raw_value is None:
        return None
    try:
        timeout_ms = int(raw_value)
    except (TypeError, ValueError):
        return None
    return timeout_ms if timeout_ms > 0 else None
