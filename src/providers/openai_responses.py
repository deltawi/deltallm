"""Native OpenAI Responses hop for `/v1/responses` on deployments that serve that API.

The text pipeline (auth, policy, routing, accounting) stays canonical. This module only
changes the provider hop: it sends the client's Responses body unchanged, returns the
provider's response and SSE events unchanged, and gives the pipeline a canonical chat view
of each response for usage, telemetry and guardrails.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import urlsplit

import httpx

from src.models.errors import InvalidRequestError, ProxyError, RoutingFailureAction
from src.models.requests import ChatCompletionRequest
from src.providers.anthropic_messages import (
    NativeSSEFrame,
    forwardable_client_error_message,
)
from src.providers.base import (
    ProviderAdapter,
    ProviderErrorDetails,
    invalid_provider_response_error,
    is_valid_provider_token_count,
    parse_provider_json_response,
    provider_error_details,
)
from src.providers.chat_upstream import ChatUpstream
from src.providers.chat_usage import CompatibleChatResponse
from src.providers.openai_compatible import (
    ProviderFailureClassifier,
    map_openai_compatible_stream_error,
)
from src.providers.request_defaults import provider_request_defaults
from src.providers.resolution import resolve_upstream_model
from src.providers.token_receipt import ProviderTokenReceipt, token_receipt

NATIVE_RESPONSES_ENDPOINT = "/responses"
# Hosts known to serve the OpenAI Responses API for `provider: openai` deployments. Other
# OpenAI-compatible servers often serve chat completions only, so they use translation.
NATIVE_RESPONSES_OPENAI_HOSTS = frozenset({"api.openai.com"})
_AZURE_RESPONSES_PATH_SUFFIX = "/openai/v1"
_TERMINAL_EVENTS = frozenset({"response.completed", "response.incomplete"})
_INCOMPLETE_TO_FINISH = {"max_output_tokens": "length", "content_filter": "content_filter"}


@dataclass(frozen=True, slots=True)
class NativeResponsesRequest:
    """The client's Responses body after DeltaLLM policy, ready for a native hop."""

    body: Mapping[str, Any] = field(repr=False)
    # Set when the body has content that the chat translation cannot carry; a
    # non-native deployment then rejects the request instead of dropping content.
    translation_blocker: str | None = None

    def upstream_body(
        self, *, model: str, max_output_tokens: int | None, stream: bool
    ) -> dict[str, Any]:
        body = dict(self.body)
        body["model"] = model
        if max_output_tokens is not None:
            body["max_output_tokens"] = max_output_tokens
        if stream:
            body["stream"] = True
        else:
            body.pop("stream", None)
        return body


def openai_base_serves_responses(api_base: str) -> bool:
    return (urlsplit(api_base).hostname or "").lower() in NATIVE_RESPONSES_OPENAI_HOSTS


def azure_base_serves_responses(api_base: str) -> bool:
    # Only the Azure OpenAI v1 surface has `/responses` next to `/chat/completions`.
    return urlsplit(api_base).path.rstrip("/").lower().endswith(_AZURE_RESPONSES_PATH_SUFFIX)


def bind_native_responses_upstream(
    upstream: ChatUpstream, native: NativeResponsesRequest
) -> ChatUpstream:
    """Use the native hop when the deployment serves Responses, else keep translation."""

    adapter = upstream.adapter.native_responses_adapter(native, api_base=upstream.api_base)
    if adapter is None:
        if native.translation_blocker is not None:
            # Another deployment of the group may serve the Responses API natively.
            raise InvalidRequestError(
                message=native.translation_blocker,
                affects_deployment_health=False,
                routing_failure_action=RoutingFailureAction.NEXT_DEPLOYMENT,
            )
        return upstream
    return replace(upstream, adapter=adapter, endpoint=NATIVE_RESPONSES_ENDPOINT)


def native_responses_usage(usage: object) -> dict[str, int]:
    """Map Responses usage to the ledger vocabulary, as chat usage for the same provider.

    Chat completions on OpenAI and Azure bill cached input at the full input price today,
    so the cached count is validated but not reported as `prompt_tokens_cached`. The
    cached-input price for these providers is a separate billing decision.
    """

    if not isinstance(usage, Mapping):
        raise invalid_provider_response_error()
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if not is_valid_provider_token_count(input_tokens) or not is_valid_provider_token_count(
        output_tokens
    ):
        raise invalid_provider_response_error()
    total = usage.get("total_tokens", input_tokens + output_tokens)
    if total != input_tokens + output_tokens:
        raise invalid_provider_response_error()
    if _detail_count(usage, "input_tokens_details", "cached_tokens") > input_tokens:
        raise invalid_provider_response_error()
    return {
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": total,
    }


def _detail_count(usage: Mapping[str, Any], details_key: str, count_key: str) -> int:
    details = usage.get(details_key)
    if details is None:
        return 0
    if not isinstance(details, Mapping):
        raise invalid_provider_response_error()
    value = details.get(count_key)
    if value is None:
        return 0
    if not is_valid_provider_token_count(value):
        raise invalid_provider_response_error()
    return value


def _finish_reason(response: Mapping[str, Any], *, has_tool_calls: bool) -> str:
    if response.get("status") == "incomplete":
        details = response.get("incomplete_details")
        reason = details.get("reason") if isinstance(details, Mapping) else None
        return _INCOMPLETE_TO_FINISH.get(str(reason or ""), "length")
    return "tool_calls" if has_tool_calls else "stop"


def canonical_tool_call(item: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the chat tool call for a client-executed Responses call item."""

    item_type = item.get("type")
    if item_type == "function_call":
        arguments = item.get("arguments")
        arguments = arguments if isinstance(arguments, str) else ""
    elif item_type == "custom_tool_call":
        arguments = json.dumps({"input": str(item.get("input") or "")})
    else:
        return None
    return {
        "id": str(item.get("call_id") or item.get("id") or ""),
        "type": "function",
        "function": {"name": str(item.get("name") or ""), "arguments": arguments},
    }


def _item_texts(item: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Return (answer text, reasoning text) of one output item."""

    item_type = item.get("type")
    if item_type == "message":
        content = item.get("content")
        parts = content if isinstance(content, list) else []
        return [
            str(part.get("text") if part.get("type") == "output_text" else part.get("refusal"))
            for part in parts
            if isinstance(part, Mapping) and part.get("type") in {"output_text", "refusal"}
        ], []
    if item_type == "reasoning":
        texts = [
            str(part.get("text") or "")
            for key in ("summary", "content")
            for part in (item.get(key) if isinstance(item.get(key), list) else [])
            if isinstance(part, Mapping)
        ]
        return [], texts
    return [], []


def _is_valid_native_response(payload: Mapping[str, Any]) -> bool:
    output = payload.get("output")
    return (
        payload.get("object") == "response"
        and payload.get("status") in {"completed", "incomplete"}
        and isinstance(output, list)
        and all(isinstance(item, Mapping) for item in output)
        and isinstance(payload.get("usage"), Mapping)
    )


def canonical_from_native_response(
    payload: Mapping[str, Any], model_name: str
) -> CompatibleChatResponse:
    if not _is_valid_native_response(payload):
        raise invalid_provider_response_error()
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for item in payload["output"]:
        texts, reasoning = _item_texts(item)
        text_parts.extend(texts)
        reasoning_parts.extend(reasoning)
        call = canonical_tool_call(item)
        if call is not None:
            tool_calls.append(call)
    message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts)}
    if reasoning_parts:
        message["reasoning_content"] = "\n".join(reasoning_parts)
    if tool_calls:
        message["tool_calls"] = tool_calls
    return CompatibleChatResponse.model_validate(
        {
            "id": str(payload.get("id") or f"resp-{int(time.time())}"),
            "object": "chat.completion",
            "created": int(payload.get("created_at") or time.time()),
            "model": str(payload.get("model") or model_name),
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": _finish_reason(payload, has_tool_calls=bool(tool_calls)),
                }
            ],
            "usage": native_responses_usage(payload["usage"]),
        }
    )


def _native_output_count(payload: object) -> int | None:
    if not isinstance(payload, Mapping) or payload.get("error") is not None:
        return None
    usage = payload.get("usage")
    count = usage.get("output_tokens") if isinstance(usage, Mapping) else None
    return count if is_valid_provider_token_count(count) else None


class OpenAINativeResponsesAdapter(ProviderAdapter):
    """Per-attempt adapter: forwards one native Responses request to one deployment."""

    def __init__(
        self,
        inner: ProviderAdapter,
        native: NativeResponsesRequest,
        *,
        classify_failure: ProviderFailureClassifier,
    ) -> None:
        self.provider_name = inner.provider_name
        self._inner = inner
        self._native = native
        self._classify_failure = classify_failure
        self.native_response: dict[str, Any] | None = None

    async def translate_request(
        self, canonical_request: ChatCompletionRequest, provider_config: dict[str, Any]
    ) -> dict[str, Any]:
        return self._native.upstream_body(
            model=resolve_upstream_model(provider_config) or canonical_request.model,
            max_output_tokens=(
                canonical_request.max_completion_tokens
                if canonical_request.max_completion_tokens is not None
                else canonical_request.max_tokens
            ),
            stream=bool(canonical_request.stream),
        )

    def apply_default_params(self, payload: dict[str, Any], model_info: Mapping[str, Any]) -> None:
        """Apply configured model defaults under their Responses names.

        Defaults are configured with chat names. Only the ones with a Responses field are
        sent; the API rejects chat-only names such as `presence_penalty`.
        """

        defaults = provider_request_defaults(model_info)
        for chat_name in ("max_completion_tokens", "max_tokens"):
            if chat_name in defaults and "max_output_tokens" not in payload:
                payload["max_output_tokens"] = defaults[chat_name]
        for name in ("temperature", "top_p"):
            if name in defaults and name not in payload:
                payload[name] = defaults[name]
        effort = defaults.get("reasoning_effort")
        reasoning = payload.get("reasoning")
        if effort is not None and (reasoning is None or isinstance(reasoning, Mapping)):
            if not (reasoning or {}).get("effort"):
                payload["reasoning"] = {**(reasoning or {}), "effort": effort}

    async def translate_response(
        self, provider_response: Any, model_name: str
    ) -> CompatibleChatResponse:
        if not isinstance(provider_response, Mapping):
            raise invalid_provider_response_error()
        return canonical_from_native_response(provider_response, model_name)

    async def translate_success_response(
        self,
        response: httpx.Response,
        model_name: str,
        *,
        output_observer: Callable[[int | None], None] | None = None,
    ) -> CompatibleChatResponse:
        payload = parse_provider_json_response(response)
        if output_observer is not None:
            output_observer(_native_output_count(payload))
        try:
            canonical = await self.translate_response(payload, model_name)
        except ProxyError:
            raise
        except Exception as exc:
            raise invalid_provider_response_error() from exc
        self.native_response = payload
        return canonical

    def complete_output_count(self, payload: object) -> int | None:
        return _native_output_count(payload)

    def reported_token_receipt(self, payload: object) -> ProviderTokenReceipt | None:
        usage = payload.get("usage") if isinstance(payload, Mapping) else None
        if not isinstance(usage, Mapping):
            return None
        details = usage.get("input_tokens_details")
        return token_receipt(
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            total_tokens=usage.get("total_tokens"),
            cached_input_tokens=(
                details.get("cached_tokens") if isinstance(details, Mapping) else None
            ),
        )

    async def translate_stream(
        self,
        provider_stream: AsyncIterator[str],
        *,
        model_name: str | None = None,
        output_observer: Callable[[int | None], None] | None = None,
    ) -> AsyncIterator[str]:
        relay = _ResponsesStreamRelay(
            model_name or "openai", output_observer, self._classify_failure
        )
        event_name: str | None = None
        async for line in provider_stream:
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip() or None
                continue
            if not line.startswith("data:"):
                continue
            raw = line[len("data:") :].strip()
            if not raw or raw == "[DONE]":
                continue
            for frame in relay.frames(event_name, raw):
                yield frame
            event_name = None
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
        message = forwardable_client_error_message(details)
        if message is None:
            return mapped
        # Coding agents recover from request errors (for example a context overflow) by
        # reading this text, and the text only describes the client's own request.
        return InvalidRequestError(
            message=message,
            affects_deployment_health=mapped.affects_deployment_health,
            failure_classification=mapped.failure_classification,
            routing_failure_action=mapped.routing_failure_action,
        )

    async def health_check(self, provider_config: dict[str, Any]) -> bool:
        return await self._inner.health_check(provider_config)


class _ResponsesStreamRelay:
    """Turns Responses SSE events into frames; owns the stream's ordering checks."""

    def __init__(
        self,
        model: str,
        output_observer: Callable[[int | None], None] | None,
        classify_failure: ProviderFailureClassifier,
    ) -> None:
        self._id = f"resp-{int(time.time() * 1000)}"
        self._model = model
        self._created = int(time.time())
        self._observer = output_observer
        self._classify_failure = classify_failure
        self._started = False
        self._tool_calls = 0
        self._reported_output = False
        self.finished = False

    def abort(self) -> None:
        """Withdraw a reported output count; only the terminal event proves it."""
        if self._reported_output and self._observer is not None:
            self._observer(None)
        self._reported_output = False

    def frames(self, event_name: str | None, raw: str) -> list[NativeSSEFrame]:
        try:
            event = json.loads(raw)
        except (RecursionError, TypeError, ValueError) as exc:
            raise invalid_provider_response_error() from exc
        if not isinstance(event, dict):
            raise invalid_provider_response_error()
        event_type = str(event.get("type") or event_name or "")
        native = f"event: {event_type}\ndata: {raw}"
        if event_type == "error":
            raise map_openai_compatible_stream_error({"error": event}, self._classify_failure)
        if event_type == "response.failed":
            response = event.get("response")
            error = response.get("error") if isinstance(response, Mapping) else None
            raise map_openai_compatible_stream_error(
                {"error": error if isinstance(error, Mapping) else {}}, self._classify_failure
            )
        if event_type == "response.created":
            return [self._start(event, native)]
        if not self._started:
            raise invalid_provider_response_error()
        if event_type in _TERMINAL_EVENTS:
            return self._finish(event, native)
        return [self._chunk(self._delta(event_type, event), native)]

    def _start(self, event: Mapping[str, Any], native: str) -> NativeSSEFrame:
        response = event.get("response")
        if self._started or not isinstance(response, Mapping):
            raise invalid_provider_response_error()
        self._started = True
        self._id = str(response.get("id") or self._id)
        self._model = str(response.get("model") or self._model)
        return self._chunk({"role": "assistant"}, native)

    def _delta(self, event_type: str, event: Mapping[str, Any]) -> dict[str, Any] | None:
        delta = event.get("delta")
        if event_type in {"response.output_text.delta", "response.refusal.delta"}:
            return {"content": delta} if isinstance(delta, str) else None
        if event_type in {"response.reasoning_summary_text.delta", "response.reasoning_text.delta"}:
            return {"reasoning_content": delta} if isinstance(delta, str) else None
        if event_type == "response.output_item.done":
            item = event.get("item")
            call = canonical_tool_call(item) if isinstance(item, Mapping) else None
            if call is None:
                return None
            # The canonical view gets each call once, complete, when its item is done.
            index = self._tool_calls
            self._tool_calls += 1
            return {"tool_calls": [{"index": index, **call}]}
        return None

    def _finish(self, event: Mapping[str, Any], native: str) -> list[NativeSSEFrame]:
        response = event.get("response")
        if not isinstance(response, Mapping):
            raise invalid_provider_response_error()
        usage = native_responses_usage(response.get("usage"))
        finish_reason = _finish_reason(response, has_tool_calls=self._tool_calls > 0)
        if self._observer is not None:
            self._observer(usage["completion_tokens"])
            self._reported_output = True
        self.finished = True
        # The terminal event goes to the client only after accounting, with `[DONE]`.
        return [
            self._chunk({}, "", finish_reason=finish_reason, usage=usage),
            NativeSSEFrame("data: [DONE]", native),
        ]

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


def served_native_responses_response(adapter: ProviderAdapter) -> dict[str, Any] | None:
    if isinstance(adapter, OpenAINativeResponsesAdapter):
        return adapter.native_response
    return None
