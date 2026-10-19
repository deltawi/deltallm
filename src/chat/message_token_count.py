"""Input-token counts for `/v1/messages/count_tokens`.

A deployment whose provider has a token-count endpoint (Anthropic) counts the body. When no
deployment of the model can count, or the provider does not answer, DeltaLLM returns a local
estimate. Counting sends no model call, so it is not billed and does not use rate limits.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from src.models.errors import InvalidRequestError
from src.providers.anthropic_messages import NativeMessagesRequest
from src.providers.base import NativeTokenCountUnavailable
from src.providers.chat_upstream import ChatAdapterLookup, resolve_chat_upstream_from_registry
from src.router.router import Deployment

# A provider that does not answer costs one upstream timeout. Stop after this many.
MAX_PROVIDER_COUNT_ATTEMPTS = 2
# Counting is fast. A deployment timeout set for long generations does not apply to it.
MAX_PROVIDER_COUNT_TIMEOUT_SECONDS = 10.0

# Claude and GPT tokenizers give approximately 3.5 to 4 characters for each token on English
# text and code. Three characters keep the estimate high, which is safer for context checks.
_CHARS_PER_TOKEN = 3
# Role and turn markers for each message.
_MESSAGE_OVERHEAD_TOKENS = 4
# A full-size image is approximately 1,600 tokens. DeltaLLM does not decode images or PDFs.
_BINARY_BLOCK_TOKENS = 1600
_IGNORED_FIELDS = frozenset({"data", "signature", "cache_control"})

TokenCountSource = Literal["provider", "estimate"]


@dataclass(frozen=True, slots=True)
class MessageTokenCount:
    input_tokens: int
    source: TokenCountSource


async def count_message_input_tokens(
    *,
    deployments: Sequence[Deployment],
    native: NativeMessagesRequest,
    adapters: ChatAdapterLookup,
    default_openai_base_url: str,
    upstream_timeout: Callable[[float | None], httpx.Timeout],
) -> MessageTokenCount:
    """Count with the first deployment that can, in routing priority order.

    A client error from the provider (for example, an unknown content block) is raised as
    InvalidRequestError. Other provider failures fall back to the local estimate.
    """

    attempts = 0
    for deployment in sorted(deployments, key=lambda item: item.priority):
        if attempts >= MAX_PROVIDER_COUNT_ATTEMPTS:
            break
        params = deployment.deltallm_params
        try:
            upstream = resolve_chat_upstream_from_registry(
                adapters, params, default_openai_base_url=default_openai_base_url
            )
        except InvalidRequestError:
            # No chat adapter or no provider key: this deployment cannot count.
            continue
        try:
            count = await upstream.adapter.count_native_message_tokens(
                upstream=upstream,
                params=params,
                native=native,
                timeout=upstream_timeout(
                    min(
                        upstream.timeout or MAX_PROVIDER_COUNT_TIMEOUT_SECONDS,
                        MAX_PROVIDER_COUNT_TIMEOUT_SECONDS,
                    )
                ),
            )
        except NativeTokenCountUnavailable:
            attempts += 1
            continue
        if count is not None:
            return MessageTokenCount(input_tokens=count, source="provider")
    return MessageTokenCount(input_tokens=estimate_input_tokens(native.body), source="estimate")


def estimate_input_tokens(body: Mapping[str, Any]) -> int:
    """Estimate the input tokens of a Messages body without a provider tokenizer."""

    counter = _Counter()
    counter.content(body.get("system"))
    messages = body.get("messages")
    if isinstance(messages, list):
        for message in messages:
            counter.fixed += _MESSAGE_OVERHEAD_TOKENS
            if isinstance(message, Mapping):
                counter.content(message.get("content"))
    tools = body.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            counter.json_value(tool)
    return max(1, counter.fixed + math.ceil(counter.chars / _CHARS_PER_TOKEN))


class _Counter:
    def __init__(self) -> None:
        self.chars = 0
        self.fixed = 0

    def content(self, value: object) -> None:
        if isinstance(value, str):
            self.chars += len(value)
        elif isinstance(value, list):
            for block in value:
                self.block(block)
        elif isinstance(value, Mapping):
            self.block(value)

    def block(self, block: object) -> None:
        if isinstance(block, str):
            self.chars += len(block)
            return
        if not isinstance(block, Mapping):
            return
        source = block.get("source")
        if isinstance(source, Mapping) and source.get("type") in {"base64", "url"}:
            self.fixed += _BINARY_BLOCK_TOKENS
            return
        block_type = block.get("type")
        if block_type == "text":
            self.content(block.get("text"))
        elif block_type == "thinking":
            self.content(block.get("thinking"))
        elif block_type == "redacted_thinking":
            return
        elif block_type == "tool_use":
            self.content(block.get("name"))
            self.json_value(block.get("input"))
        elif block_type == "tool_result":
            self.content(block.get("content"))
        else:
            # Text documents, search results, and block types that Anthropic adds later.
            self.json_value(block)

    def json_value(self, value: object) -> None:
        if value is not None:
            self.chars += len(json.dumps(_without_ignored(value), ensure_ascii=False, default=str))


def _without_ignored(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            key: _without_ignored(item) for key, item in value.items() if key not in _IGNORED_FIELDS
        }
    if isinstance(value, list):
        return [_without_ignored(item) for item in value]
    return value
