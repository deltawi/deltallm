from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from src.guardrails.middleware import GuardrailMiddleware
from src.mcp.orchestrator import MCPChatOrchestrator, MCPRequestContext
from src.models.errors import ProxyError, ServiceUnavailableError
from src.models.request_serialization import dump_request_for_preflight
from src.models.requests import ChatCompletionRequest
from src.models.responses import UserAPIKeyAuth
from src.rate_limit_policy import estimate_tokens
from src.router import Deployment, FailoverManager
from src.router.context_policy import RequestTokenDemand, set_request_token_demand


ChatDeploymentCall = Callable[
    [ChatCompletionRequest, Deployment],
    Awaitable[tuple[dict[str, Any], float]],
]
DeploymentAttemptObserver = Callable[[Deployment], None]


@dataclass(frozen=True, slots=True)
class MCPModelRouting:
    primary_deployment: Deployment
    model_group: str
    routing_context: dict[str, Any]
    timeout_seconds: float | None = None
    retry_max_attempts: int | None = None
    retryable_error_classes: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class MCPChatExecutionResult:
    payload: dict[str, Any]
    api_latency_ms: float
    served_deployment: Deployment
    fallback_used: bool


class MCPChatExecutionService:
    """Own the model/tool phase sequence without making tool calls retryable."""

    def __init__(
        self,
        *,
        failover_manager: FailoverManager,
        orchestrator: MCPChatOrchestrator,
        execute_chat_call: ChatDeploymentCall,
    ) -> None:
        self.failover_manager = failover_manager
        self.orchestrator = orchestrator
        self.execute_chat_call = execute_chat_call

    async def execute(
        self,
        *,
        request_context: MCPRequestContext,
        auth: UserAPIKeyAuth,
        payload: ChatCompletionRequest,
        guardrail_middleware: GuardrailMiddleware,
        routing: MCPModelRouting,
        on_attempt: DeploymentAttemptObserver | None = None,
    ) -> MCPChatExecutionResult:
        deadline = self.failover_manager.create_request_deadline(routing.timeout_seconds)
        served_deployment: Deployment | None = None
        phase_primary = routing.primary_deployment
        fallback_used = False

        async def execute_model_phase(
            phase_payload: ChatCompletionRequest,
        ) -> tuple[dict[str, Any], float]:
            nonlocal fallback_used, phase_primary, served_deployment
            set_request_token_demand(
                routing.routing_context,
                RequestTokenDemand(
                    input_tokens=estimate_tokens(dump_request_for_preflight(phase_payload)),
                    requested_output_tokens=phase_payload.max_tokens,
                ),
            )
            attempted_primary = phase_primary
            result, phase_served_deployment = await self.failover_manager.execute_with_failover(
                primary_deployment=attempted_primary,
                model_group=routing.model_group,
                execute=lambda deployment: self.execute_chat_call(phase_payload, deployment),
                return_deployment=True,
                on_attempt=on_attempt,
                timeout_seconds=routing.timeout_seconds,
                retry_max_attempts=routing.retry_max_attempts,
                retryable_error_classes=list(routing.retryable_error_classes)
                if routing.retryable_error_classes is not None
                else None,
                routing_context=routing.routing_context,
                request_deadline=deadline,
            )
            fallback_used = fallback_used or (
                attempted_primary.deployment_id != phase_served_deployment.deployment_id
            )
            served_deployment = phase_served_deployment
            phase_primary = phase_served_deployment
            return result

        try:
            response_payload, api_latency_ms = await deadline.wait_for(
                self.orchestrator.execute(
                    request_context=request_context,
                    auth=auth,
                    payload=payload,
                    execute_chat_call=execute_model_phase,
                    guardrail_middleware=guardrail_middleware,
                )
            )
        except ProxyError:
            raise
        except Exception as exc:
            raise ServiceUnavailableError(message="MCP orchestration failed") from exc
        if served_deployment is None:
            raise ServiceUnavailableError(
                message="MCP orchestration completed without a model result"
            )
        return MCPChatExecutionResult(
            payload=response_payload,
            api_latency_ms=api_latency_ms,
            served_deployment=served_deployment,
            fallback_used=fallback_used,
        )
