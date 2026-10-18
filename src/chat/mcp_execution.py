from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from src.chat.quality_recording import AttemptQualityRecorder
from src.chat.response_validation import ResponseValidationScope
from src.guardrails.middleware import GuardrailMiddleware
from src.mcp.orchestrator import MCPChatOrchestrator, MCPRequestContext
from src.models.errors import ProxyError, ServiceUnavailableError
from src.models.request_serialization import dump_request_for_preflight
from src.models.requests import ChatCompletionRequest
from src.models.responses import UserAPIKeyAuth
from src.route_group_config import ResponseValidationSettings
from src.rate_limit_policy import estimate_tokens
from src.router import Deployment, FailoverManager
from src.router.context_policy import RequestTokenDemand, set_request_token_demand
from src.router.execution import RequestDeadline
from src.router.selection.operation import refresh_selector_payload


ChatDeploymentCall = Callable[
    [ChatCompletionRequest, Deployment],
    Awaitable[tuple[dict[str, Any], float]],
]
DeploymentAttemptObserver = Callable[[Deployment], None]
HopObserver = Callable[[], None]


@dataclass(frozen=True, slots=True)
class MCPModelRouting:
    primary_deployment: Deployment
    model_group: str
    routing_context: dict[str, Any]
    timeout_seconds: float | None = None
    retry_max_attempts: int | None = None
    retryable_error_classes: tuple[str, ...] | None = None
    request_deadline: RequestDeadline | None = None


@dataclass(frozen=True, slots=True)
class MCPChatExecutionResult:
    payload: dict[str, Any]
    api_latency_ms: float
    served_deployment: Deployment
    fallback_used: bool
    response_validation_headers: dict[str, str] = field(default_factory=dict)


def refresh_hop_routing_context(
    routing_context: dict[str, Any], hop_payload: ChatCompletionRequest
) -> None:
    """Route each model hop on its own prompt size; tool results make it grow."""
    token_estimate = estimate_tokens(dump_request_for_preflight(hop_payload))
    refresh_selector_payload(routing_context, hop_payload, token_estimate)
    set_request_token_demand(
        routing_context,
        RequestTokenDemand(
            input_tokens=token_estimate,
            requested_output_tokens=(
                hop_payload.max_completion_tokens
                if hop_payload.max_completion_tokens is not None
                else hop_payload.max_tokens
            ),
        ),
    )


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
        response_validation: ResponseValidationSettings | None = None,
        quality: AttemptQualityRecorder | None = None,
        on_next_hop: HopObserver | None = None,
    ) -> MCPChatExecutionResult:
        deadline = routing.request_deadline or self.failover_manager.create_request_deadline(
            routing.timeout_seconds
        )
        served_deployment: Deployment | None = None
        phase_primary = routing.primary_deployment
        fallback_used = False
        validation_settings = response_validation or ResponseValidationSettings(enabled=False)
        validation_headers: dict[str, str] = {}
        recorder = quality or AttemptQualityRecorder(None)
        hops_started = 0

        async def execute_model_phase(
            phase_payload: ChatCompletionRequest,
        ) -> tuple[dict[str, Any], float]:
            nonlocal fallback_used, hops_started, phase_primary, served_deployment
            if hops_started and on_next_hop is not None:
                # The earlier hop completed; its usage is part of the final charge.
                on_next_hop()
            hops_started += 1
            refresh_hop_routing_context(routing.routing_context, phase_payload)
            attempted_primary = phase_primary
            phase_validation = ResponseValidationScope(phase_payload, validation_settings)
            result, phase_served_deployment = await phase_validation.run(
                recorder.run(
                    self.failover_manager.execute_with_failover(
                        primary_deployment=attempted_primary,
                        model_group=routing.model_group,
                        execute=lambda deployment: recorder.attempt(
                            deployment,
                            phase_validation.checked(
                                deployment, self.execute_chat_call(phase_payload, deployment)
                            ),
                        ),
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
                )
            )
            # The client sees the final answer; earlier phases only feed tool execution.
            validation_headers.clear()
            validation_headers.update(phase_validation.response_headers())
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
            response_validation_headers=dict(validation_headers),
        )
