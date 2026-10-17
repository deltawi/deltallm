"""Streamed chat and responses requests with gateway-run MCP tools.

Each model hop is its own upstream stream with first-chunk failover. Text goes
to the client as it arrives. When a hop ends with MCP tool calls, the gateway
runs them outside any provider retry and opens the next hop on the same
downstream response. Bytes already sent are never replaced.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import Request

from src.chat.durable_execution import open_durable_stream
from src.chat.executor import OpenedStream
from src.chat.mcp_execution import MCPModelRouting, refresh_hop_routing_context
from src.chat.mcp_stream_buffer import MCPStreamHopBuffer
from src.chat.stream_response import (
    DeadlineStreamingResponse,
    ManagedStreamLifecycle,
    close_stream_resources,
    record_stream_health_outcome,
)
from src.chat.stream_usage import StreamUsage, StreamUsageTracker
from src.chat.telemetry import emit_stream_failure, emit_stream_success
from src.mcp.hop_usage import add_hop_usage
from src.mcp.orchestrator import MCPChatOrchestrator, MCPRequestContext, ResolvedMCPTool
from src.middleware.errors import serialize_proxy_error
from src.models.errors import InvalidRequestError, ProxyError, ServiceUnavailableError
from src.models.requests import ChatCompletionRequest
from src.models.responses import UserAPIKeyAuth
from src.router import Deployment, FailoverManager
from src.router.execution import ManagedFailoverResult, RequestDeadline
from src.router.usage import record_router_usage

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MCPStreamTelemetry:
    """Request facts that the spend, audit and callback emitters need."""

    request: Request
    auth: UserAPIKeyAuth
    payload: ChatCompletionRequest
    request_data: dict[str, Any]
    callback_manager: Any
    guardrail_middleware: Any
    callback_start: datetime
    request_start: float
    request_id: str | None
    cache_hit: bool
    cache_key: str | None
    audit_action: str

    def emitter_kwargs(self) -> dict[str, Any]:
        return {
            "request": self.request,
            "auth": self.auth,
            "payload": self.payload,
            "request_data": self.request_data,
            "callback_manager": self.callback_manager,
            "guardrail_middleware": self.guardrail_middleware,
            "callback_start": self.callback_start,
            "request_start": self.request_start,
            "request_id": self.request_id,
            "cache_hit": self.cache_hit,
            "cache_key": self.cache_key,
            "audit_action": self.audit_action,
        }


@dataclass(frozen=True, slots=True)
class MCPStreamFraming:
    response_object: str
    line_transform: Callable[[str], str | None] | None = None
    error_transform: Callable[[ProxyError], str] | None = None


@dataclass(slots=True)
class _Hop:
    payload: ChatCompletionRequest
    managed: ManagedFailoverResult[OpenedStream]
    lifecycle: ManagedStreamLifecycle

    @property
    def opened(self) -> OpenedStream:
        return self.managed.value


@dataclass(slots=True)
class _HopResult:
    buffer: MCPStreamHopBuffer
    usage: StreamUsage | None = None
    terminal_line: str | None = None


@dataclass(slots=True)
class _StreamTotals:
    usage: dict[str, Any] | None = None
    estimated: bool = False
    estimate_incomplete: bool = False
    completion_id: str | None = None

    def add(self, usage: StreamUsage) -> None:
        self.usage = add_hop_usage(self.usage, usage.usage)
        self.estimated = self.estimated or usage.estimated
        self.estimate_incomplete = self.estimate_incomplete or usage.estimate_incomplete

    def metadata(self) -> dict[str, Any]:
        return {
            "usage_source": "estimated" if self.estimated else "provider",
            "usage_estimated": self.estimated,
            "usage_estimate_incomplete": self.estimate_incomplete,
        }


class MCPStreamSession:
    def __init__(
        self,
        *,
        telemetry: MCPStreamTelemetry,
        framing: MCPStreamFraming,
        failover_manager: FailoverManager,
        cooldown_manager: Any,
        router_state_backend: Any,
        orchestrator: MCPChatOrchestrator,
        request_context: MCPRequestContext,
        routing: MCPModelRouting,
        on_attempt: Callable[[Deployment], None] | None = None,
    ) -> None:
        self.telemetry = telemetry
        self.framing = framing
        self.failover_manager = failover_manager
        self.cooldown_manager = cooldown_manager
        self.router_state_backend = router_state_backend
        self.orchestrator = orchestrator
        self.request_context = request_context
        self.routing = routing
        self.on_attempt = on_attempt
        self.deadline: RequestDeadline = (
            routing.request_deadline
            or failover_manager.create_request_deadline(routing.timeout_seconds)
        )
        self._hop_primary = routing.primary_deployment
        self._current: _Hop | None = None

    async def open(
        self,
        *,
        on_first_hop: Callable[[Deployment], None],
        headers: Callable[[], dict[str, str]],
    ) -> DeadlineStreamingResponse:
        """Open the first hop before the response starts, so its errors stay HTTP errors."""
        payload = self.telemetry.payload
        if (payload.n or 1) > 1:
            raise InvalidRequestError(message="MCP tools on streaming requests support only n=1")
        translated, resolved_tools = await self.orchestrator.prepare_payload(
            self.telemetry.auth, payload
        )
        hop = await self._open_hop(translated)
        try:
            on_first_hop(hop.managed.deployment)
            return DeadlineStreamingResponse(
                self._events(hop, resolved_tools),
                deadline=self.deadline,
                close=self._close_current,
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "x-deltallm-cache-hit": "false",
                    **headers(),
                },
            )
        except BaseException as exc:
            await close_stream_resources(lambda failure=exc: hop.lifecycle.close(failure))
            raise

    async def _open_hop(self, hop_payload: ChatCompletionRequest) -> _Hop:
        routing = self.routing
        request = self.telemetry.request
        refresh_hop_routing_context(routing.routing_context, hop_payload)
        managed = await self.failover_manager.execute_managed_with_failover(
            primary_deployment=self._hop_primary,
            model_group=routing.model_group,
            execute=lambda deployment: open_durable_stream(request, hop_payload, deployment),
            on_attempt=self.on_attempt,
            timeout_seconds=routing.timeout_seconds,
            retry_max_attempts=routing.retry_max_attempts,
            retryable_error_classes=list(routing.retryable_error_classes)
            if routing.retryable_error_classes is not None
            else None,
            routing_context=routing.routing_context,
            request_deadline=self.deadline,
        )
        self._hop_primary = managed.deployment
        hop = _Hop(
            payload=hop_payload,
            managed=managed,
            lifecycle=ManagedStreamLifecycle(managed.value, managed),
        )
        self._current = hop
        return hop

    async def _close_current(self, exc: BaseException | None = None) -> None:
        if self._current is not None:
            await self._current.lifecycle.close(exc)

    async def _events(
        self, hop: _Hop, resolved_tools: dict[str, ResolvedMCPTool]
    ) -> AsyncIterator[str]:
        totals = _StreamTotals()
        result: _HopResult | None = None
        stream_error: ProxyError | None = None
        failure: BaseException | None = None
        hop_count = 1
        try:
            while True:
                result = _HopResult(buffer=MCPStreamHopBuffer(completion_id=totals.completion_id))
                async with aclosing(self._relay_hop(hop, result)) as lines:
                    async for line in lines:
                        yield line
                totals.completion_id = totals.completion_id or result.buffer.completion_id
                if result.usage is not None:
                    totals.add(result.usage)
                    await record_router_usage(
                        self.router_state_backend,
                        hop.managed.deployment.deployment_id,
                        mode="chat",
                        usage=result.usage.usage,
                    )
                tool_calls = result.buffer.tool_calls()
                if not self.orchestrator.gateway_owns_tool_calls(tool_calls, resolved_tools):
                    break
                if hop_count > self.orchestrator.max_mcp_tool_hops:
                    raise InvalidRequestError(
                        message="MCP tool execution exceeded the configured hop limit"
                    )
                # Tools can run for a long time. Do not hold provider capacity meanwhile.
                await close_stream_resources(hop.lifecycle.close)
                for tool_call in tool_calls:
                    # An SSE comment keeps idle proxies from closing the response.
                    yield f": mcp_tool_call {tool_call['function']['name']}\n\n"
                next_messages = list(hop.payload.messages)
                next_messages.append(
                    self.orchestrator.assistant_message_from_response(
                        result.buffer.assistant_response()
                    )
                )
                next_messages.extend(
                    await self.orchestrator.run_tool_calls(
                        request_context=self.request_context,
                        auth=self.telemetry.auth,
                        guardrail_middleware=self.telemetry.guardrail_middleware,
                        resolved_tools=resolved_tools,
                        tool_calls=tool_calls,
                    )
                )
                hop = await self._open_hop(
                    hop.payload.model_copy(update={"messages": next_messages})
                )
                hop_count += 1
        except asyncio.CancelledError as exc:
            failure = exc
            raise
        except ProxyError as exc:
            stream_error = failure = exc
        except Exception as exc:
            logger.warning("MCP stream orchestration failed error_type=%s", type(exc).__name__)
            stream_error = failure = ServiceUnavailableError(message="MCP orchestration failed")
        finally:
            if failure is not None:
                await close_stream_resources(lambda: self._close_current(failure))

        if stream_error is not None or result is None:
            stream_error = stream_error or ServiceUnavailableError(
                message="MCP orchestration failed"
            )
            try:
                await emit_stream_failure(
                    **self.telemetry.emitter_kwargs(),
                    primary_deployment=self.routing.primary_deployment,
                    api_base=hop.opened.api_base,
                    params=hop.opened.params,
                    exc=stream_error,
                )
            except Exception as reporting_error:
                logger.warning(
                    "post-stream MCP failure reporting failed deployment_id=%s error_type=%s",
                    hop.managed.deployment.deployment_id,
                    type(reporting_error).__name__,
                )
            yield f"{self._error_line(stream_error)}\n\n"
            return

        for line in result.buffer.client_lines():
            out_line = self._transform(line)
            if out_line is not None:
                yield f"{out_line}\n\n"
        if hop.opened.client_stream_usage_requested and totals.usage is not None:
            out_line = self._transform(result.buffer.usage_line(totals.usage))
            if out_line is not None:
                yield f"{out_line}\n\n"
        async with emit_stream_success(
            **self.telemetry.emitter_kwargs(),
            stream_response_object=self.framing.response_object,
            served_deployment=hop.managed.deployment,
            api_base=hop.opened.api_base,
            params=hop.opened.params,
            usage=totals.usage,
            usage_metadata=totals.metadata(),
        ):
            try:
                if result.terminal_line is not None:
                    out_line = self._transform(result.terminal_line)
                    if out_line is not None:
                        yield f"{out_line}\n\n"
            finally:
                await close_stream_resources(hop.lifecycle.close)

    async def _relay_hop(self, hop: _Hop, result: _HopResult) -> AsyncIterator[str]:
        opened = hop.opened
        tracker = StreamUsageTracker()
        failure: BaseException | None = None
        health_error: Exception | None = None
        try:
            line: str | None = opened.first_line
            iterator = opened.translated_stream.__aiter__()
            while True:
                if line is None:
                    try:
                        line = await anext(iterator)
                    except StopAsyncIteration:
                        break
                    except ProxyError as exc:
                        health_error = exc
                        raise
                    except Exception as exc:
                        health_error = opened.adapter.map_error(exc)
                        raise health_error from exc
                if not line:
                    line = None
                    continue
                try:
                    info = tracker.add_line(line)
                except Exception as exc:
                    health_error = exc
                    raise
                if info.is_terminal:
                    result.terminal_line = line
                    break
                if not info.is_usage_only_chunk:
                    # The client gets one summed usage chunk at the end, not one per hop.
                    forwarded = result.buffer.filter(info.without_usage or line)
                    out_line = self._transform(forwarded) if forwarded is not None else None
                    if out_line is not None:
                        yield f"{out_line}\n\n"
                line = None
            if opened.output_context is not None:
                await opened.output_context.finish()
            result.usage = tracker.resolve(hop.payload)
        except BaseException as exc:
            failure = exc
            raise
        finally:
            if opened.output_context is not None:
                await opened.output_context.finish()
            try:
                await record_stream_health_outcome(
                    cooldown_manager=self.cooldown_manager,
                    health_ref=hop.managed.deployment.health_ref,
                    health_error=health_error,
                    succeeded=failure is None,
                    recovery_token=hop.managed.recovery_token,
                )
            finally:
                await close_stream_resources(lambda: hop.lifecycle.close_upstream(failure))

    def _transform(self, line: str) -> str | None:
        transform = self.framing.line_transform
        return transform(line) if transform is not None else line

    def _error_line(self, exc: ProxyError) -> str:
        if self.framing.error_transform is not None:
            return self.framing.error_transform(exc)
        return "data: " + json.dumps(serialize_proxy_error(exc), separators=(",", ":"))
