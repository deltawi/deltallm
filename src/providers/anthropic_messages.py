"""Native Anthropic Messages hop for `/v1/messages` on Anthropic deployments.

The text pipeline (auth, policy, routing, accounting) stays canonical. This module only
changes the provider hop: it sends the client's Messages body unchanged, returns the
provider's Messages response unchanged, and gives the pipeline a canonical view of each
response for usage, telemetry and guardrails.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

import httpx

from src.models.errors import InvalidRequestError, ProxyError, RoutingFailureAction
from src.models.requests import ChatCompletionRequest
from src.providers.anthropic import map_anthropic_stream_error
from src.providers.base import (
    NativeTokenCountUnavailable,
    ProviderAdapter,
    ProviderErrorDetails,
    invalid_provider_response_error,
    is_valid_provider_token_count,
    parse_provider_json_response,
    provider_error_details,
)
from src.providers.chat_upstream import ChatUpstream
from src.providers.chat_usage import CompatibleChatResponse
from src.providers.resolution import (
    resolve_provider_required_chat_output_tokens,
    resolve_upstream_model,
)
from src.providers.token_receipt import ProviderTokenReceipt

# The served native response travels with the canonical result of one attempt. The HTTP
# edge removes it before telemetry, so it is never logged or stored with the result.
NATIVE_MESSAGES_RESPONSE_KEY = "__native_messages_response__"

_MAX_HEADER_VALUE_CHARS = 4096
_FORWARDED_CLIENT_ERROR_STATUSES = frozenset({400, 413, 422})
_FORWARDED_CLIENT_ERROR_TYPES = frozenset({"invalid_request_error", "request_too_large"})
# Anthropic sends account state (credit, billing, workspace limits) as invalid_request_error
# too. That text is about the shared provider account and stays out of client errors.
_ACCOUNT_ERROR_MARKERS = ("credit balance", "billing", "workspace", "organization", "api key")
_STOP_TO_FINISH = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "pause_turn": "stop",
    "max_tokens": "length",
    "model_context_window_exceeded": "length",
    "tool_use": "tool_calls",
    "refusal": "content_filter",
}


def _header_value(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value or len(value) > _MAX_HEADER_VALUE_CHARS:
        return None
    if any(not 0x20 <= ord(char) < 0x7F for char in value):
        return None
    return value


@dataclass(frozen=True, slots=True)
class NativeMessagesRequest:
    """The client's Messages body after DeltaLLM policy, ready for an Anthropic hop."""

    body: Mapping[str, Any] = field(repr=False)
    anthropic_beta: str | None = None
    anthropic_version: str | None = None
    # Set when the body has content that the chat translation cannot carry; a
    # non-native deployment then rejects the request instead of dropping content.
    translation_blocker: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "anthropic_beta", _header_value(self.anthropic_beta))
        object.__setattr__(self, "anthropic_version", _header_value(self.anthropic_version))

    def upstream_body(self, *, model: str, max_tokens: int | None, stream: bool) -> dict[str, Any]:
        body = dict(self.body)
        body["model"] = model
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if stream:
            body["stream"] = True
        else:
            body.pop("stream", None)
        return body

    def upstream_headers(self, headers: Mapping[str, str]) -> dict[str, str]:
        merged = dict(headers)
        if self.anthropic_version is not None:
            merged["anthropic-version"] = self.anthropic_version
        if self.anthropic_beta is not None:
            merged["anthropic-beta"] = self.anthropic_beta
        return merged


class NativeSSEFrame(str):
    """A canonical stream line that also carries the provider event to relay unchanged.

    The pipeline reads the canonical text for usage, validation and failover; the
    `/v1/messages` edge writes `native` to the client.
    """

    __slots__ = ("native",)
    native: str

    def __new__(cls, canonical: str, native: str) -> NativeSSEFrame:
        frame = super().__new__(cls, canonical)
        frame.native = native
        return frame


def bind_native_messages_upstream(
    upstream: ChatUpstream, native: NativeMessagesRequest | None
) -> ChatUpstream:
    """Use the native hop when the adapter supports it, else keep the translation hop."""

    if native is None:
        return upstream
    adapter = upstream.adapter.native_messages_adapter(native)
    if adapter is None:
        if native.translation_blocker is not None:
            # Another deployment of the group may be an Anthropic one that can serve it.
            raise InvalidRequestError(
                message=native.translation_blocker,
                affects_deployment_health=False,
                routing_failure_action=RoutingFailureAction.NEXT_DEPLOYMENT,
            )
        return upstream
    return replace(upstream, adapter=adapter, headers=native.upstream_headers(upstream.headers))


def native_usage(usage: object) -> dict[str, int]:
    """Map Messages usage to the ledger vocabulary.

    Anthropic reports uncached input, cache writes and cache reads separately; all three are
    prompt tokens. Cache reads use the cached-input price when the model has one.
    """

    if not isinstance(usage, Mapping):
        raise invalid_provider_response_error()
    counts = {
        key: usage.get(key, 0) if usage.get(key) is not None else 0
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
    }
    if not all(is_valid_provider_token_count(value) for value in counts.values()):
        raise invalid_provider_response_error()
    prompt = (
        counts["input_tokens"]
        + counts["cache_creation_input_tokens"]
        + counts["cache_read_input_tokens"]
    )
    return {
        "prompt_tokens": prompt,
        "completion_tokens": counts["output_tokens"],
        "total_tokens": prompt + counts["output_tokens"],
        "prompt_tokens_cached": counts["cache_read_input_tokens"],
        "cache_creation_input_tokens": counts["cache_creation_input_tokens"],
    }


def _is_valid_native_message(payload: Mapping[str, Any]) -> bool:
    content = payload.get("content")
    stop_reason = payload.get("stop_reason")
    return (
        payload.get("type") == "message"
        and payload.get("role") == "assistant"
        and isinstance(content, list)
        and all(isinstance(block, Mapping) for block in content)
        and isinstance(stop_reason, str)
        and bool(stop_reason.strip())
        and isinstance(payload.get("usage"), Mapping)
    )


def canonical_from_native_message(
    payload: Mapping[str, Any], model_name: str
) -> CompatibleChatResponse:
    if not _is_valid_native_message(payload):
        raise invalid_provider_response_error()
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in payload["content"]:
        block_type = block.get("type")
        if block_type == "text" and isinstance(block.get("text"), str):
            text_parts.append(block["text"])
        elif block_type == "thinking" and isinstance(block.get("thinking"), str):
            reasoning_parts.append(block["thinking"])
        elif block_type == "tool_use":
            tool_calls.append(
                {
                    "id": str(block.get("id") or ""),
                    "type": "function",
                    "function": {
                        "name": str(block.get("name") or ""),
                        "arguments": json.dumps(block.get("input") or {}),
                    },
                }
            )
    message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts)}
    if reasoning_parts:
        message["reasoning_content"] = "\n".join(reasoning_parts)
    if tool_calls:
        message["tool_calls"] = tool_calls
    return CompatibleChatResponse.model_validate(
        {
            "id": str(payload.get("id") or f"msg-{int(time.time())}"),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": str(payload.get("model") or model_name),
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": _STOP_TO_FINISH.get(payload["stop_reason"], "stop"),
                }
            ],
            "usage": native_usage(payload["usage"]),
        }
    )


class AnthropicNativeMessagesAdapter(ProviderAdapter):
    """Per-attempt adapter: forwards one native Messages request to one Anthropic deployment."""

    provider_name = "anthropic"

    def __init__(self, inner: ProviderAdapter, native: NativeMessagesRequest) -> None:
        self._inner = inner
        self._native = native
        self.native_response: dict[str, Any] | None = None

    async def translate_request(
        self, canonical_request: ChatCompletionRequest, provider_config: dict[str, Any]
    ) -> dict[str, Any]:
        requested = (
            canonical_request.max_completion_tokens
            if canonical_request.max_completion_tokens is not None
            else canonical_request.max_tokens
        )
        return self._native.upstream_body(
            model=resolve_upstream_model(provider_config) or canonical_request.model,
            max_tokens=resolve_provider_required_chat_output_tokens(provider_config, requested),
            stream=bool(canonical_request.stream),
        )

    async def translate_response(
        self, provider_response: Any, model_name: str
    ) -> CompatibleChatResponse:
        data = provider_response if isinstance(provider_response, Mapping) else None
        if data is None:
            raise invalid_provider_response_error()
        return canonical_from_native_message(data, model_name)

    async def translate_success_response(
        self,
        response: httpx.Response,
        model_name: str,
        *,
        output_observer: Callable[[int | None], None] | None = None,
    ) -> CompatibleChatResponse:
        payload = parse_provider_json_response(response)
        if output_observer is not None:
            output_observer(self._inner.complete_output_count(payload))
        try:
            canonical = await self.translate_response(payload, model_name)
        except ProxyError:
            raise
        except Exception as exc:
            raise invalid_provider_response_error() from exc
        self.native_response = payload
        return canonical

    def complete_output_count(self, payload: object) -> int | None:
        return self._inner.complete_output_count(payload)

    def reported_token_receipt(self, payload: object) -> ProviderTokenReceipt | None:
        return self._inner.reported_token_receipt(payload)

    async def translate_stream(
        self,
        provider_stream: AsyncIterator[str],
        *,
        model_name: str | None = None,
        output_observer: Callable[[int | None], None] | None = None,
    ) -> AsyncIterator[str]:
        relay = _NativeStreamRelay(model_name or "anthropic", output_observer)
        event_name: str | None = None
        async for line in provider_stream:
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip() or None
                continue
            if not line.startswith("data:"):
                continue
            raw = line[len("data:") :].strip()
            if not raw:
                continue
            frame = relay.frame(event_name, raw)
            event_name = None
            if frame is not None:
                yield frame
            if relay.finished:
                return
        relay.abort()
        raise invalid_provider_response_error()

    def map_error(
        self,
        provider_error: Exception,
        *,
        details: ProviderErrorDetails | None = None,
    ) -> ProxyError:
        details = details or provider_error_details(provider_error)
        mapped = self._inner.map_error(provider_error, details=details)
        if not isinstance(mapped, InvalidRequestError):
            return mapped
        message = _forwardable_client_error_message(details)
        if message is None:
            return mapped
        # Anthropic clients recover from capability rejections by matching this text,
        # and the text only describes the client's own request.
        return InvalidRequestError(
            message=message,
            affects_deployment_health=mapped.affects_deployment_health,
            failure_classification=mapped.failure_classification,
            routing_failure_action=mapped.routing_failure_action,
        )

    async def health_check(self, provider_config: dict[str, Any]) -> bool:
        return await self._inner.health_check(provider_config)


def _forwardable_client_error_message(details: ProviderErrorDetails) -> str | None:
    if details.status_code not in _FORWARDED_CLIENT_ERROR_STATUSES or not details.message:
        return None
    if not details.identifiers & _FORWARDED_CLIENT_ERROR_TYPES:
        return None
    lowered = details.message.lower()
    if any(marker in lowered for marker in _ACCOUNT_ERROR_MARKERS):
        # This text is about the deployment's Anthropic account, not the client request.
        return None
    return "".join(char if char.isprintable() else " " for char in details.message)


async def count_native_message_tokens(
    *,
    client: httpx.AsyncClient,
    upstream: ChatUpstream,
    params: Mapping[str, object],
    native: NativeMessagesRequest,
    timeout: httpx.Timeout,
) -> int:
    """Ask an Anthropic deployment's `count_tokens` endpoint for the body's input tokens.

    A client error that only describes the request is raised as InvalidRequestError, as on
    `/v1/messages`. Any other failure raises NativeTokenCountUnavailable.
    """

    url = f"{upstream.api_base}{upstream.endpoint}/count_tokens"
    body = dict(native.body)
    body["model"] = resolve_upstream_model(params) or body.get("model")
    try:
        response = await client.post(
            url, headers=native.upstream_headers(upstream.headers), json=body, timeout=timeout
        )
    except httpx.HTTPError as exc:
        raise NativeTokenCountUnavailable from exc
    if response.status_code >= 400:
        error = httpx.HTTPStatusError(
            f"Upstream count_tokens call failed with status {response.status_code}",
            request=httpx.Request("POST", url),
            response=response,
        )
        message = _forwardable_client_error_message(provider_error_details(error))
        if message is not None:
            raise InvalidRequestError(message=message)
        raise NativeTokenCountUnavailable
    try:
        payload = response.json()
    except ValueError as exc:
        raise NativeTokenCountUnavailable from exc
    count = payload.get("input_tokens") if isinstance(payload, Mapping) else None
    if not is_valid_provider_token_count(count):
        raise NativeTokenCountUnavailable
    return count


class _NativeStreamRelay:
    """Turns Anthropic SSE events into frames; owns the stream's ordering checks."""

    def __init__(self, model: str, output_observer: Callable[[int | None], None] | None) -> None:
        self._id = f"msg-{int(time.time() * 1000)}"
        self._model = model
        self._created = int(time.time())
        self._observer = output_observer
        self._started = False
        self._input_usage: Mapping[str, Any] = {}
        self._finish_reason: str | None = None
        self._tool_indexes: dict[int, int] = {}
        self._reported_output = False
        self.finished = False

    def abort(self) -> None:
        """Withdraw a reported output count; only the terminal delta proves it."""
        if self._reported_output and self._observer is not None:
            self._observer(None)
        self._reported_output = False

    def frame(self, event_name: str | None, raw: str) -> NativeSSEFrame | None:
        try:
            event = json.loads(raw)
        except (RecursionError, TypeError, ValueError) as exc:
            self.abort()
            raise invalid_provider_response_error() from exc
        if not isinstance(event, dict):
            self.abort()
            raise invalid_provider_response_error()
        event_type = str(event.get("type") or event_name or "")
        if event_type not in {"ping", "message_stop"}:
            self.abort()
        if event_type == "error":
            raise map_anthropic_stream_error(event)
        native = f"event: {event_type}\ndata: {raw}"
        if event_type == "message_start":
            return self._message_start(event, native)
        if not self._started:
            if event_type == "ping":
                return None
            raise invalid_provider_response_error()
        if event_type == "message_delta":
            return self._message_delta(event, native)
        if event_type == "message_stop":
            if self._finish_reason is None:
                raise invalid_provider_response_error()
            self.finished = True
            return NativeSSEFrame("data: [DONE]", native)
        if event_type == "content_block_start":
            return self._chunk(self._block_start_delta(event), native)
        if event_type == "content_block_delta":
            return self._chunk(self._block_delta(event), native)
        # ping, content_block_stop and event types added later are relayed as is.
        return self._chunk(None, native)

    def _chunk(
        self,
        delta: dict[str, Any] | None,
        native: str,
        *,
        finish_reason: str | None = None,
        usage: dict[str, int] | None = None,
    ) -> NativeSSEFrame:
        chunk: dict[str, Any] = {
            "id": self._id,
            "object": "chat.completion.chunk",
            "created": self._created,
            "model": self._model,
            "choices": (
                [{"index": 0, "delta": delta or {}, "finish_reason": finish_reason}]
                if delta is not None or finish_reason is not None
                else []
            ),
        }
        if usage is not None:
            chunk["usage"] = usage
        return NativeSSEFrame(f"data: {json.dumps(chunk, separators=(',', ':'))}", native)

    def _message_start(self, event: Mapping[str, Any], native: str) -> NativeSSEFrame:
        message = event.get("message")
        if self._started or not isinstance(message, Mapping):
            raise invalid_provider_response_error()
        self._started = True
        self._id = str(message.get("id") or self._id)
        self._model = str(message.get("model") or self._model)
        usage = message.get("usage")
        self._input_usage = usage if isinstance(usage, Mapping) else {}
        return self._chunk({"role": "assistant"}, native)

    def _message_delta(self, event: Mapping[str, Any], native: str) -> NativeSSEFrame:
        delta = event.get("delta")
        if not isinstance(delta, Mapping):
            raise invalid_provider_response_error()
        stop_reason = delta.get("stop_reason")
        delta_usage = event.get("usage")
        usage = None
        if isinstance(delta_usage, Mapping):
            # Later deltas repeat or update the input counts; message_start has the rest.
            usage = native_usage({**self._input_usage, **delta_usage})
        if not isinstance(stop_reason, str) or not stop_reason:
            return self._chunk(None, native, usage=usage)
        self._finish_reason = _STOP_TO_FINISH.get(stop_reason, "stop")
        if self._observer is not None and usage is not None:
            self._observer(usage["completion_tokens"])
            self._reported_output = True
        return self._chunk({}, native, finish_reason=self._finish_reason, usage=usage)

    def _block_start_delta(self, event: Mapping[str, Any]) -> dict[str, Any] | None:
        block = event.get("content_block")
        if not isinstance(block, Mapping):
            raise invalid_provider_response_error()
        if block.get("type") != "tool_use":
            return None
        index = _block_index(event)
        tool_index = len(self._tool_indexes)
        self._tool_indexes[index] = tool_index
        return {
            "tool_calls": [
                {
                    "index": tool_index,
                    "id": str(block.get("id") or ""),
                    "type": "function",
                    "function": {"name": str(block.get("name") or ""), "arguments": ""},
                }
            ]
        }

    def _block_delta(self, event: Mapping[str, Any]) -> dict[str, Any] | None:
        delta = event.get("delta")
        if not isinstance(delta, Mapping):
            raise invalid_provider_response_error()
        delta_type = delta.get("type")
        if delta_type == "text_delta" and isinstance(delta.get("text"), str):
            return {"content": delta["text"]}
        if delta_type == "thinking_delta" and isinstance(delta.get("thinking"), str):
            return {"reasoning_content": delta["thinking"]}
        if delta_type == "input_json_delta" and isinstance(delta.get("partial_json"), str):
            tool_index = self._tool_indexes.get(_block_index(event))
            if tool_index is None:
                return None
            return {
                "tool_calls": [
                    {"index": tool_index, "function": {"arguments": delta["partial_json"]}}
                ]
            }
        return None


def _block_index(event: Mapping[str, Any]) -> int:
    value = event.get("index")
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise invalid_provider_response_error()
    return value


def served_native_response(adapter: ProviderAdapter) -> dict[str, Any] | None:
    if isinstance(adapter, AnthropicNativeMessagesAdapter):
        return adapter.native_response
    return None
