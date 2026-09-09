from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
import json
import math

import httpx
from pydantic import ValidationError

from src.models.requests import ChatCompletionRequest, ResponseFormat
from src.models.responses import ChatCompletionResponse
from src.providers.chat_hop import BoundedChatResponse, ChatHopError, execute_chat_hop
from src.providers.chat_upstream import (
    ChatAdapterLookup,
    ChatGenerationProfile,
    OptionalGenerationControl,
    resolve_chat_upstream_from_registry,
)
from src.providers.resolution import resolve_upstream_model
from src.providers.token_receipt import ProviderTokenReceipt
from src.router.selection.contracts import (
    SELECTOR_OUTPUT_BYTES,
    SELECTOR_OUTPUT_TOKENS,
    ReportedSelectorUsage,
    SelectorCause,
    SelectorHopFailure,
    SelectorHopOutcome,
    SelectorHopSuccess,
    SelectorInvariantError,
    SelectorPrompt,
    SelectorUsage,
    UnknownSelectorUsage,
    UnattemptedSelectorUsage,
)

MAX_SELECTOR_REQUEST_BYTES = 262_144
_TARGET_KEYS = (
    "model",
    "provider",
    "api_base",
    "api_key",
    "api_version",
    "timeout",
    "auth_header_name",
    "auth_header_format",
    "region",
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
)
TargetValue = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class ConcreteSelectorTarget:
    deployment_id: str
    parameters: tuple[tuple[str, TargetValue], ...] = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.deployment_id, str) or not 1 <= len(self.deployment_id) <= 256:
            raise SelectorInvariantError()
        if type(self.parameters) is not tuple or len(self.parameters) > len(_TARGET_KEYS):
            raise SelectorInvariantError()
        names: set[str] = set()
        size = 0
        for pair in self.parameters:
            if type(pair) is not tuple or len(pair) != 2:
                raise SelectorInvariantError()
            key, value = pair
            if key not in _TARGET_KEYS or key in names:
                raise SelectorInvariantError()
            names.add(key)
            if value is not None and type(value) not in (str, int, float, bool):
                raise SelectorInvariantError()
            if isinstance(value, float) and not math.isfinite(value):
                raise SelectorInvariantError()
            size += len(value) if isinstance(value, str) else 8
        if size > MAX_SELECTOR_REQUEST_BYTES:
            raise SelectorInvariantError()

    @classmethod
    def from_config(
        cls, deployment_id: str, params: Mapping[str, object]
    ) -> ConcreteSelectorTarget:
        values: list[tuple[str, TargetValue]] = []
        for key in _TARGET_KEYS:
            if key in params:
                value = params[key]
                if value is not None and not isinstance(value, (str, int, float, bool)):
                    raise SelectorInvariantError()
                values.append((key, value))
        return cls(deployment_id=deployment_id, parameters=tuple(values))


class SelectorProviderHop:
    """Bridge to the canonical direct hop; it never owns clients or routing."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        adapters: ChatAdapterLookup,
        target: ConcreteSelectorTarget,
        default_openai_base_url: str,
        profile: ChatGenerationProfile = ChatGenerationProfile(),
    ) -> None:
        self._client = client
        self._adapters = adapters
        self._target = target
        self._default_base_url = default_openai_base_url
        self._profile = profile

    async def invoke(
        self,
        *,
        deployment_id: str,
        prompt: SelectorPrompt,
        expires_at: float,
    ) -> SelectorHopOutcome:
        if (
            deployment_id != self._target.deployment_id
            or type(expires_at) not in (int, float)
            or not math.isfinite(expires_at)
        ):
            raise SelectorInvariantError()
        if expires_at <= asyncio.get_running_loop().time():
            raise TimeoutError()
        async with asyncio.timeout_at(expires_at):
            return await self._invoke(prompt=prompt, expires_at=expires_at)

    async def _invoke(self, *, prompt: SelectorPrompt, expires_at: float) -> SelectorHopOutcome:
        params = dict(self._target.parameters)
        model = resolve_upstream_model(params)
        if not model:
            raise SelectorInvariantError()
        upstream = resolve_chat_upstream_from_registry(
            self._adapters,
            params,
            default_openai_base_url=self._default_base_url,
        )
        request = _classifier_request(prompt, model=model, profile=self._profile)
        payload = await upstream.adapter.translate_request(request, params)
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_SELECTOR_REQUEST_BYTES:
            return SelectorHopFailure(
                cause=SelectorCause.INPUT_BUDGET_INSUFFICIENT, usage=UnattemptedSelectorUsage()
            )
        remaining = expires_at - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError()
        timeout = _bounded_timeout(self._client.timeout, upstream.timeout, remaining)
        observed: SelectorUsage = UnknownSelectorUsage()

        def observe(receipt: ProviderTokenReceipt | None) -> None:
            nonlocal observed
            if receipt is not None:
                observed = ReportedSelectorUsage(
                    prompt_tokens=receipt.input_tokens,
                    completion_tokens=receipt.output_tokens,
                    total_tokens=receipt.total_tokens,
                    cached_input_tokens=receipt.cached_input_tokens,
                )

        try:
            canonical = await execute_chat_hop(
                client=self._client,
                upstream=upstream,
                params=params,
                payload=payload,
                model_name=model,
                timeout=timeout,
                bounded=BoundedChatResponse(),
                receipt_observer=observe,
            )
        except ChatHopError as exc:
            return SelectorHopFailure(cause=SelectorCause(exc.cause.value), usage=observed)
        return _selector_result(canonical, receipt=observed)


def _bounded_timeout(
    configured: httpx.Timeout, deployment: float | None, remaining: float
) -> httpx.Timeout:
    def clamp(value: float | None) -> float:
        return min(value, remaining) if value is not None else remaining

    return httpx.Timeout(
        connect=clamp(configured.connect),
        read=min(clamp(deployment), clamp(configured.read)),
        write=clamp(configured.write),
        pool=clamp(configured.pool),
    )


def _classifier_request(
    prompt: SelectorPrompt, *, model: str, profile: ChatGenerationProfile
) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model=model,
        messages=[
            {"role": "system", "content": prompt.system},
            {"role": "user", "content": prompt.user},
        ],
        n=1,
        stream=False,
        stream_options=None,
        max_tokens=SELECTOR_OUTPUT_TOKENS,
        temperature=0.0
        if profile.zero_temperature is OptionalGenerationControl.SUPPORTED
        else None,
        top_p=None,
        presence_penalty=None,
        frequency_penalty=None,
        stop=None,
        tools=None,
        tool_choice="none",
        user=None,
        metadata=None,
        response_format=ResponseFormat(type="json_object")
        if profile.json_object is OptionalGenerationControl.SUPPORTED
        else None,
    )


def _selector_result(
    response: ChatCompletionResponse, *, receipt: SelectorUsage | None = None
) -> SelectorHopOutcome:
    try:
        usage = (
            receipt
            if receipt is not None
            else ReportedSelectorUsage(
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens,
            )
        )
    except ValidationError:
        return SelectorHopFailure(
            cause=SelectorCause.INVALID_RESPONSE, usage=UnknownSelectorUsage()
        )
    if len(response.choices) != 1:
        return SelectorHopFailure(cause=SelectorCause.INVALID_RESPONSE, usage=usage)
    choice = response.choices[0]
    text = choice.message.content
    if (
        choice.finish_reason != "stop"
        or choice.message.tool_calls
        or not isinstance(text, str)
        or not text
    ):
        return SelectorHopFailure(cause=SelectorCause.INVALID_RESPONSE, usage=usage)
    if len(text) > SELECTOR_OUTPUT_BYTES:
        return SelectorHopFailure(cause=SelectorCause.OUTPUT_TOO_LARGE, usage=usage)
    try:
        if len(text.encode("utf-8")) > SELECTOR_OUTPUT_BYTES:
            return SelectorHopFailure(cause=SelectorCause.OUTPUT_TOO_LARGE, usage=usage)
    except UnicodeError:
        return SelectorHopFailure(cause=SelectorCause.INVALID_RESPONSE, usage=usage)
    return SelectorHopSuccess(text=text, usage=usage)
