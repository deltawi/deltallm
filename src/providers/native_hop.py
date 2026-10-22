"""Selects the native provider hop for edge formats that a provider serves directly.

`/v1/messages` (Anthropic Messages) and `/v1/responses` (OpenAI Responses) keep the
canonical chat pipeline for policy, routing and accounting. Per attempt, a deployment that
serves the client's format natively receives the client's body unchanged.
"""

from __future__ import annotations

from typing import Any, TypeAlias

from src.providers.anthropic_messages import (
    AnthropicNativeMessagesAdapter,
    NativeMessagesRequest,
    bind_native_messages_upstream,
)
from src.providers.base import ProviderAdapter
from src.providers.chat_upstream import ChatUpstream
from src.providers.openai_responses import (
    NativeResponsesRequest,
    OpenAINativeResponsesAdapter,
    bind_native_responses_upstream,
)

NativeRequest: TypeAlias = NativeMessagesRequest | NativeResponsesRequest

# The served native response travels with the canonical result of one attempt. The HTTP
# edge removes it before telemetry, so it is never logged or stored with the result.
NATIVE_RESPONSE_KEY = "__native_response__"


def bind_native_upstream(upstream: ChatUpstream, native: NativeRequest | None) -> ChatUpstream:
    if native is None:
        return upstream
    if isinstance(native, NativeResponsesRequest):
        return bind_native_responses_upstream(upstream, native)
    return bind_native_messages_upstream(upstream, native)


def served_native_response(adapter: ProviderAdapter) -> dict[str, Any] | None:
    if isinstance(adapter, (AnthropicNativeMessagesAdapter, OpenAINativeResponsesAdapter)):
        return adapter.native_response
    return None


def sends_native_body(adapter: ProviderAdapter) -> bool:
    """True when the attempt sends the client's body, not a chat completions body."""
    return isinstance(adapter, (AnthropicNativeMessagesAdapter, OpenAINativeResponsesAdapter))
