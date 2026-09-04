from __future__ import annotations

import gzip
import json
import struct
from binascii import crc32
from types import SimpleNamespace

import httpx
import pytest

from src.models.errors import (
    FailureClassification,
    InvalidRequestError,
    RateLimitError,
    RoutingFailureAction,
    ServiceUnavailableError,
    TimeoutError,
)
from src.models.requests import ChatCompletionRequest
from src.providers.anthropic import AnthropicAdapter
from src.providers.azure import AzureOpenAIAdapter
from src.providers.bedrock import BedrockAdapter
from src.providers.base import (
    INVALID_PROVIDER_RESPONSE_MESSAGE,
    MAX_PROVIDER_ERROR_BODY_BYTES,
    PROVIDER_ERROR_BODY_TRUNCATED_EXTENSION,
    bound_provider_error_response_body,
    map_standard_provider_status_error,
    parse_provider_json_response,
    read_streaming_provider_error_details,
)
from src.providers.error_body import PROVIDER_ERROR_BODY_OPAQUE_EXTENSION
from src.providers.gemini import GeminiAdapter
from src.providers.openai import OpenAIAdapter
from src.providers.registry import ProviderErrorMapperRegistry
from src.upstream_http import build_upstream_http_client

_OMITTED = object()


def _assistant_tool_call_message(content: object = _OMITTED) -> dict[str, object]:
    message: dict[str, object] = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "toolu_1",
                "type": "function",
                "function": {
                    "name": "docs.search",
                    "arguments": json.dumps({"query": "delta"}),
                },
            }
        ],
    }
    if content is not _OMITTED:
        message["content"] = content
    return message


async def _line_stream(lines: list[str]):
    for line in lines:
        yield line


async def _byte_stream(chunks: list[bytes]):
    for chunk in chunks:
        yield chunk


class _AsyncBytes(httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        return None


def _provider_error_mapper_registry(client: httpx.AsyncClient) -> ProviderErrorMapperRegistry:
    return ProviderErrorMapperRegistry(
        openai=OpenAIAdapter(client),
        azure_openai=AzureOpenAIAdapter(client),
        anthropic=AnthropicAdapter(client),
        gemini=GeminiAdapter(client),
        bedrock=BedrockAdapter(client),
    )


def _encode_eventstream_message(headers: dict[str, str], payload: dict) -> bytes:
    """Encodes a single AWS binary event-stream frame, matching the format
    botocore.eventstream.EventStreamBuffer expects (used by Bedrock's ConverseStream)."""
    header_bytes = b""
    for name, value in headers.items():
        name_bytes = name.encode("utf-8")
        value_bytes = value.encode("utf-8")
        header_bytes += struct.pack("!B", len(name_bytes)) + name_bytes
        header_bytes += struct.pack("!B", 7) + struct.pack("!H", len(value_bytes)) + value_bytes

    payload_bytes = json.dumps(payload).encode("utf-8")
    total_length = 12 + len(header_bytes) + len(payload_bytes) + 4
    prelude_no_crc = struct.pack("!II", total_length, len(header_bytes))
    prelude_crc = crc32(prelude_no_crc) & 0xFFFFFFFF
    prelude_crc_bytes = struct.pack("!I", prelude_crc)
    message_crc = crc32(prelude_crc_bytes + header_bytes + payload_bytes, prelude_crc) & 0xFFFFFFFF
    return (
        prelude_no_crc
        + prelude_crc_bytes
        + header_bytes
        + payload_bytes
        + struct.pack("!I", message_crc)
    )


def _stream_json_payloads(lines: list[str]) -> list[dict]:
    payloads: list[dict] = []
    for line in lines:
        if not line.startswith("data: "):
            continue
        payload = line[len("data: ") :]
        if payload == "[DONE]":
            continue
        payloads.append(json.loads(payload))
    return payloads


@pytest.mark.asyncio
async def test_openai_adapter_omits_tool_choice_without_tools() -> None:
    adapter = OpenAIAdapter(httpx.AsyncClient())
    try:
        req = ChatCompletionRequest(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "hi"}],
            tool_choice="auto",
        )
        payload = await adapter.translate_request(req, {"model": "openai/gpt-4o-mini"})
        assert "tool_choice" not in payload
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_openai_adapter_keeps_tool_choice_with_tools() -> None:
    adapter = OpenAIAdapter(httpx.AsyncClient())
    try:
        req = ChatCompletionRequest(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "hi"}],
            tools=[
                {"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}
            ],
            tool_choice="auto",
        )
        payload = await adapter.translate_request(req, {"model": "openai/gpt-4o-mini"})
        assert payload.get("tool_choice") == "auto"
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_type", [OpenAIAdapter, AzureOpenAIAdapter])
@pytest.mark.parametrize(
    ("assistant_content", "expected_present"),
    [pytest.param(_OMITTED, False, id="omitted"), pytest.param(None, True, id="null")],
)
async def test_openai_compatible_adapter_preserves_assistant_content_presence(
    adapter_type,
    assistant_content: object,
    expected_present: bool,
) -> None:
    adapter = adapter_type(httpx.AsyncClient())
    try:
        req = ChatCompletionRequest.model_validate(
            {
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "user", "content": "search docs"},
                    _assistant_tool_call_message(assistant_content),
                    {"role": "tool", "tool_call_id": "toolu_1", "content": "result"},
                ],
            }
        )
        payload = await adapter.translate_request(
            req,
            {
                "provider": adapter.provider_name,
                "model": f"{adapter.provider_name}/gpt-4o-mini",
            },
        )

        assistant = payload["messages"][1]
        assert ("content" in assistant) is expected_present
        if expected_present:
            assert assistant["content"] is None
        assert assistant["tool_calls"] == _assistant_tool_call_message()["tool_calls"]
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_openai_adapter_preserves_slash_prefixed_model_for_groq() -> None:
    adapter = OpenAIAdapter(httpx.AsyncClient())
    try:
        req = ChatCompletionRequest(
            model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": "hi"}],
        )
        payload = await adapter.translate_request(
            req,
            {"provider": "groq", "model": "openai/gpt-oss-120b"},
        )
        assert payload["model"] == "openai/gpt-oss-120b"
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_openai_adapter_maps_max_tokens_to_max_completion_tokens_for_gpt5() -> None:
    adapter = OpenAIAdapter(httpx.AsyncClient())
    try:
        req = ChatCompletionRequest(
            model="gpt-5-mini",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=64,
        )
        payload = await adapter.translate_request(
            req,
            {"provider": "openai", "model": "openai/gpt-5-mini"},
        )
        assert "max_tokens" not in payload
        assert payload["max_completion_tokens"] == 64
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_openai_adapter_keeps_max_tokens_for_non_gpt5_models() -> None:
    adapter = OpenAIAdapter(httpx.AsyncClient())
    try:
        req = ChatCompletionRequest(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=64,
        )
        payload = await adapter.translate_request(
            req,
            {"provider": "openai", "model": "openai/gpt-4o-mini"},
        )
        assert payload["max_tokens"] == 64
        assert "max_completion_tokens" not in payload
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_openai_adapter_allows_tool_call_messages_without_content() -> None:
    adapter = OpenAIAdapter(httpx.AsyncClient())
    try:
        canonical = await adapter.translate_response(
            {
                "id": "chatcmpl-tool-1",
                "object": "chat.completion",
                "created": 1700000000,
                "model": "openai/gpt-oss-120b",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_docs_search",
                                    "type": "function",
                                    "function": {
                                        "name": "docs.search",
                                        "arguments": '{"query":"DeltaLLM"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            },
            model_name="openai/gpt-oss-120b",
        )
        payload = canonical.model_dump(mode="json")
        assert payload["choices"][0]["message"]["content"] == ""
        assert (
            payload["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "docs.search"
        )
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_openai_adapter_sanitizes_unclassified_provider_error_message() -> None:
    adapter = OpenAIAdapter(httpx.AsyncClient())
    try:
        response = httpx.Response(
            400,
            json={"error": {"message": "tool_choice is not supported for this model"}},
            request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
        )
        exc = httpx.HTTPStatusError("bad request", request=response.request, response=response)
        mapped = adapter.map_error(exc)
        assert str(mapped) == "Provider rejected request"
        assert mapped.failure_classification is FailureClassification.GENERIC
        assert "tool_choice" not in str(mapped)
    finally:
        await adapter.http_client.aclose()


@pytest.mark.parametrize("status_code", [401, 403, 404])
def test_provider_configuration_statuses_are_retryable_deployment_failures(
    status_code: int,
) -> None:
    mapped = map_standard_provider_status_error(status_code)

    assert isinstance(mapped, ServiceUnavailableError)
    assert str(mapped) == "Provider unavailable"
    assert mapped.affects_deployment_health is True
    assert mapped.failure_classification is FailureClassification.GENERIC


def test_provider_request_timeout_is_retryable_deployment_failure() -> None:
    mapped = map_standard_provider_status_error(408)

    assert isinstance(mapped, TimeoutError)
    assert mapped.affects_deployment_health is True
    assert mapped.failure_classification is FailureClassification.TIMEOUT


def test_classified_provider_403_remains_a_terminal_request_failure() -> None:
    mapped = map_standard_provider_status_error(
        403,
        failure_classification=FailureClassification.CONTENT_POLICY,
    )

    assert isinstance(mapped, InvalidRequestError)
    assert mapped.affects_deployment_health is False
    assert mapped.failure_classification is FailureClassification.CONTENT_POLICY


@pytest.mark.asyncio
async def test_nonstream_provider_error_body_is_bounded_before_buffering() -> None:
    sensitive = b"sk-upstream-secret"
    response = httpx.Response(
        500,
        stream=_AsyncBytes(b'{"error":{"message":"' + sensitive + b'"}}' + b"x" * 70_000),
        request=httpx.Request("POST", "https://internal.provider.example/v1/chat"),
    )

    await bound_provider_error_response_body(response)
    body = await response.aread()

    assert len(body) == MAX_PROVIDER_ERROR_BODY_BYTES
    assert response.extensions[PROVIDER_ERROR_BODY_TRUNCATED_EXTENSION] is True
    error = httpx.HTTPStatusError("provider failed", request=response.request, response=response)
    async with httpx.AsyncClient() as client:
        mapped = OpenAIAdapter(client).map_error(error)
    assert isinstance(mapped, ServiceUnavailableError)
    assert str(mapped) == "Provider unavailable"
    assert sensitive.decode() not in str(mapped)


@pytest.mark.asyncio
async def test_shared_upstream_client_never_decompresses_encoded_error_body() -> None:
    decoded_body = b"x" * (8 * 1024 * 1024)
    encoded_body = gzip.compress(decoded_body)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            headers={"content-encoding": "gzip"},
            stream=_AsyncBytes(encoded_body),
            request=request,
        )

    configured_client = build_upstream_http_client(SimpleNamespace())
    event_hooks = configured_client.event_hooks
    await configured_client.aclose()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        event_hooks=event_hooks,
    ) as client:
        response = await client.get("https://provider.example/v1/chat")

    assert len(encoded_body) < MAX_PROVIDER_ERROR_BODY_BYTES
    assert response.content == encoded_body
    assert len(response.content) <= MAX_PROVIDER_ERROR_BODY_BYTES
    assert response.headers.get("content-encoding") is None
    assert response.extensions[PROVIDER_ERROR_BODY_OPAQUE_EXTENSION] is True
    error = httpx.HTTPStatusError("provider failed", request=response.request, response=response)
    async with httpx.AsyncClient() as adapter_client:
        mapped = OpenAIAdapter(adapter_client).map_error(error)
    assert isinstance(mapped, ServiceUnavailableError)
    assert str(mapped) == "Provider unavailable"


@pytest.mark.asyncio
async def test_shared_upstream_client_still_decodes_success_body() -> None:
    decoded_body = b'{"ok":true}'
    encoded_body = gzip.compress(decoded_body)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-encoding": "gzip"},
            stream=_AsyncBytes(encoded_body),
            request=request,
        )

    configured_client = build_upstream_http_client(SimpleNamespace())
    event_hooks = configured_client.event_hooks
    await configured_client.aclose()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        event_hooks=event_hooks,
    ) as client:
        response = await client.get("https://provider.example/v1/chat")

    assert response.content == decoded_body
    assert PROVIDER_ERROR_BODY_OPAQUE_EXTENSION not in response.extensions


def test_provider_success_json_parser_rejects_non_object_without_exposing_body() -> None:
    response = httpx.Response(
        200,
        content=b'["sk-upstream"]',
        request=httpx.Request("POST", "https://internal.provider.example/v1/chat"),
    )

    with pytest.raises(ServiceUnavailableError) as error_info:
        parse_provider_json_response(response)

    error = error_info.value
    assert str(error) == INVALID_PROVIDER_RESPONSE_MESSAGE
    assert error.affects_deployment_health is True
    assert error.failure_classification is FailureClassification.GENERIC
    assert "sk-upstream" not in str(error)
    assert "internal.provider.example" not in str(error)


@pytest.mark.asyncio
async def test_openai_adapter_sanitizes_malformed_success_schema() -> None:
    adapter = OpenAIAdapter(httpx.AsyncClient())
    try:
        response = httpx.Response(
            200,
            json={"secret": "sk-upstream", "messages": ["private-output"]},
            request=httpx.Request("POST", "https://internal.provider.example/v1/chat"),
        )

        with pytest.raises(ServiceUnavailableError) as error_info:
            await adapter.translate_success_response(response, "provider-model")

        error = error_info.value
        assert str(error) == INVALID_PROVIDER_RESPONSE_MESSAGE
        assert error.affects_deployment_health is True
        assert error.failure_classification is FailureClassification.GENERIC
        assert "sk-upstream" not in str(error)
        assert "private-output" not in str(error)
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_type", [OpenAIAdapter, AzureOpenAIAdapter])
async def test_openai_compatible_adapter_rejects_empty_success_choices(adapter_type) -> None:
    adapter = adapter_type(httpx.AsyncClient())
    try:
        response = httpx.Response(
            200,
            json={
                "id": "chatcmpl-empty",
                "object": "chat.completion",
                "created": 1700000000,
                "model": "provider-model",
                "choices": [],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            },
            request=httpx.Request("POST", "https://internal.provider.example/v1/chat"),
        )

        with pytest.raises(ServiceUnavailableError) as error_info:
            await adapter.translate_success_response(response, "provider-model")

        assert str(error_info.value) == INVALID_PROVIDER_RESPONSE_MESSAGE
        assert error_info.value.affects_deployment_health is True
        assert error_info.value.failure_classification is FailureClassification.GENERIC
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_type", [OpenAIAdapter, AzureOpenAIAdapter])
async def test_openai_compatible_stream_classifies_filter_before_output(adapter_type) -> None:
    adapter = adapter_type(httpx.AsyncClient())
    try:
        lines = [
            'data: {"id":"chatcmpl-filtered","choices":[{"index":0,'
            '"delta":{"role":"assistant"},"finish_reason":null}]}',
            'data: {"id":"chatcmpl-filtered","choices":[{"index":0,'
            '"delta":{},"finish_reason":"content_filter"}]}',
            "data: [DONE]",
        ]

        with pytest.raises(InvalidRequestError, match="Provider rejected request") as error_info:
            async for _ in adapter.translate_stream(_line_stream(lines)):
                pass

        assert error_info.value.failure_classification is FailureClassification.CONTENT_POLICY
        assert error_info.value.affects_deployment_health is False
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_type", [OpenAIAdapter, AzureOpenAIAdapter])
async def test_openai_compatible_stream_preserves_filter_after_output(adapter_type) -> None:
    adapter = adapter_type(httpx.AsyncClient())
    try:
        lines = [
            'data: {"id":"chatcmpl-filtered","choices":[{"index":0,'
            '"delta":{"role":"assistant"},"finish_reason":null}]}',
            'data: {"id":"chatcmpl-filtered","choices":[{"index":0,'
            '"delta":{"content":"partial"},"finish_reason":null}]}',
            'data: {"id":"chatcmpl-filtered","choices":[{"index":0,'
            '"delta":{},"finish_reason":"content_filter"}]}',
            "data: [DONE]",
        ]

        out = [line async for line in adapter.translate_stream(_line_stream(lines))]

        assert out == lines
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_type", [OpenAIAdapter, AzureOpenAIAdapter])
@pytest.mark.parametrize(
    ("reasoning_field", "reasoning_value"),
    [
        ("reasoning", "step"),
        ("reasoning_content", "step"),
        ("reasoning_details", [{"type": "reasoning.text", "text": "step"}]),
    ],
)
async def test_openai_compatible_stream_treats_reasoning_as_output(
    adapter_type,
    reasoning_field: str,
    reasoning_value: object,
) -> None:
    adapter = adapter_type(httpx.AsyncClient())
    try:
        lines = [
            'data: {"id":"chatcmpl-reasoning","choices":[{"index":0,'
            '"delta":{"role":"assistant"},"finish_reason":null}]}',
            *[
                "data: "
                + json.dumps(
                    {
                        "id": "chatcmpl-reasoning",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    reasoning_field: reasoning_value,
                                    "provider_extension": {"sequence": index},
                                },
                                "finish_reason": None,
                            }
                        ],
                    },
                    separators=(",", ":"),
                )
                for index in range(33)
            ],
            'data: {"id":"chatcmpl-reasoning","choices":[{"index":0,'
            '"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function",'
            '"function":{"name":"search","arguments":"{}"}}]},"finish_reason":null}]}',
            'data: {"id":"chatcmpl-reasoning","choices":[{"index":0,'
            '"delta":{"content":"answer"},"finish_reason":null}]}',
            'data: {"id":"chatcmpl-reasoning","choices":[{"index":0,'
            '"delta":{},"finish_reason":"stop"}]}',
            "data: [DONE]",
        ]

        out = [line async for line in adapter.translate_stream(_line_stream(lines))]

        assert out == lines
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_type", [OpenAIAdapter, AzureOpenAIAdapter])
async def test_openai_compatible_stream_marks_unknown_output_limit_for_next_deployment(
    adapter_type,
    monkeypatch,
) -> None:
    validation_reasons: list[str] = []

    def record_validation_failure(*, reason) -> None:  # noqa: ANN001
        validation_reasons.append(reason.value)

    monkeypatch.setattr(
        "src.providers.openai_compatible.increment_provider_stream_validation_failure",
        record_validation_failure,
    )
    adapter = adapter_type(httpx.AsyncClient())
    try:
        lines = [
            "data: "
            + json.dumps(
                {
                    "id": "chatcmpl-future-output",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"future_reasoning_field": f"step-{index}"},
                            "finish_reason": None,
                        }
                    ],
                },
                separators=(",", ":"),
            )
            for index in range(33)
        ]

        with pytest.raises(ServiceUnavailableError) as error_info:
            async for _ in adapter.translate_stream(_line_stream(lines)):
                pass

        assert str(error_info.value) == INVALID_PROVIDER_RESPONSE_MESSAGE
        assert error_info.value.affects_deployment_health is False
        assert error_info.value.failure_classification is FailureClassification.GENERIC
        assert error_info.value.routing_failure_action is RoutingFailureAction.NEXT_DEPLOYMENT
        assert validation_reasons == ["precommit_unknown_output_limit"]
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_type", [OpenAIAdapter, AzureOpenAIAdapter])
async def test_openai_compatible_stream_marks_unknown_output_terminal_for_next_deployment(
    adapter_type,
    monkeypatch,
) -> None:
    validation_reasons: list[str] = []

    def record_validation_failure(*, reason) -> None:  # noqa: ANN001
        validation_reasons.append(reason.value)

    monkeypatch.setattr(
        "src.providers.openai_compatible.increment_provider_stream_validation_failure",
        record_validation_failure,
    )
    adapter = adapter_type(httpx.AsyncClient())
    try:
        lines = [
            'data: {"id":"chatcmpl-future-output","choices":[{"index":0,'
            '"delta":{"future_reasoning_field":"step"},"finish_reason":null}]}',
            "data: [DONE]",
        ]

        with pytest.raises(ServiceUnavailableError) as error_info:
            async for _ in adapter.translate_stream(_line_stream(lines)):
                pass

        assert str(error_info.value) == INVALID_PROVIDER_RESPONSE_MESSAGE
        assert error_info.value.affects_deployment_health is False
        assert error_info.value.failure_classification is FailureClassification.GENERIC
        assert error_info.value.routing_failure_action is RoutingFailureAction.NEXT_DEPLOYMENT
        assert validation_reasons == ["precommit_unknown_output_terminal"]
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_type", [OpenAIAdapter, AzureOpenAIAdapter])
async def test_openai_compatible_stream_marks_role_only_limit_as_provider_failure(
    adapter_type,
    monkeypatch,
) -> None:
    validation_reasons: list[str] = []

    def record_validation_failure(*, reason) -> None:  # noqa: ANN001
        validation_reasons.append(reason.value)

    monkeypatch.setattr(
        "src.providers.openai_compatible.increment_provider_stream_validation_failure",
        record_validation_failure,
    )
    adapter = adapter_type(httpx.AsyncClient())
    try:
        lines = [
            "data: "
            + json.dumps(
                {
                    "id": "chatcmpl-role-only",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant"},
                            "finish_reason": None,
                        }
                    ],
                },
                separators=(",", ":"),
            )
            for _ in range(33)
        ]

        with pytest.raises(ServiceUnavailableError) as error_info:
            async for _ in adapter.translate_stream(_line_stream(lines)):
                pass

        assert error_info.value.affects_deployment_health is True
        assert error_info.value.routing_failure_action is None
        assert validation_reasons == ["precommit_no_output_limit"]
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_type", [OpenAIAdapter, AzureOpenAIAdapter])
@pytest.mark.parametrize(
    "lines",
    [
        ["data: [DONE]"],
        [
            'data: {"id":"chatcmpl-truncated","choices":[{"index":0,'
            '"delta":{"role":"assistant"},"finish_reason":null}]}'
        ],
        [
            'data: {"id":"chatcmpl-empty-reasoning","choices":[{"index":0,'
            '"delta":{"reasoning_content":"","reasoning_details":[]},'
            '"finish_reason":null}]}',
            "data: [DONE]",
        ],
    ],
)
async def test_openai_compatible_stream_rejects_terminal_only_or_truncated_input(
    adapter_type,
    lines: list[str],
) -> None:
    adapter = adapter_type(httpx.AsyncClient())
    try:
        with pytest.raises(ServiceUnavailableError) as error_info:
            async for _ in adapter.translate_stream(_line_stream(lines)):
                pass

        assert str(error_info.value) == INVALID_PROVIDER_RESPONSE_MESSAGE
        assert error_info.value.affects_deployment_health is True
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_type", [AnthropicAdapter, GeminiAdapter, BedrockAdapter])
async def test_native_adapter_sanitizes_empty_success_schema(adapter_type) -> None:  # noqa: ANN001
    adapter = adapter_type(httpx.AsyncClient())
    try:
        response = httpx.Response(
            200,
            json={"secret": "sk-upstream"},
            request=httpx.Request("POST", "https://internal.provider.example/native"),
        )

        with pytest.raises(ServiceUnavailableError) as error_info:
            await adapter.translate_success_response(response, "provider-model")

        error = error_info.value
        assert str(error) == INVALID_PROVIDER_RESPONSE_MESSAGE
        assert error.affects_deployment_health is True
        assert error.failure_classification is FailureClassification.GENERIC
        assert "sk-upstream" not in str(error)
        assert "internal.provider.example" not in str(error)
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_provider_5xx_status_preserves_specialized_envelope_classification() -> None:
    adapter = OpenAIAdapter(httpx.AsyncClient())
    try:
        response = httpx.Response(
            500,
            json={"error": {"code": "content_filter", "message": "sk-upstream"}},
            request=httpx.Request("POST", "https://internal.provider.example/v1/chat"),
        )
        status_error = httpx.HTTPStatusError(
            "provider failed",
            request=response.request,
            response=response,
        )

        mapped = adapter.map_error(status_error)

        assert isinstance(mapped, ServiceUnavailableError)
        assert str(mapped) == "Provider unavailable"
        assert mapped.affects_deployment_health is True
        assert mapped.failure_classification is FailureClassification.CONTENT_POLICY
        assert "sk-upstream" not in str(mapped)
        assert "internal.provider.example" not in str(mapped)
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_gemini_5xx_context_failure_stays_health_affecting_and_classified() -> None:
    adapter = GeminiAdapter(httpx.AsyncClient())
    try:
        response = httpx.Response(
            500,
            json={
                "error": {
                    "status": "INTERNAL",
                    "message": "The input context is too long sk-upstream",
                }
            },
            request=httpx.Request("POST", "https://internal.provider.example/generateContent"),
        )
        status_error = httpx.HTTPStatusError(
            "provider failed",
            request=response.request,
            response=response,
        )

        mapped = adapter.map_error(status_error)

        assert isinstance(mapped, ServiceUnavailableError)
        assert mapped.affects_deployment_health is True
        assert mapped.failure_classification is FailureClassification.CONTEXT_WINDOW
        assert str(mapped) == "Provider unavailable"
        assert "sk-upstream" not in str(mapped)
        assert "internal.provider.example" not in str(mapped)
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_provider_429_status_remains_rate_limit_when_envelope_is_specialized() -> None:
    adapter = OpenAIAdapter(httpx.AsyncClient())
    try:
        response = httpx.Response(
            429,
            json={"error": {"code": "content_filter", "message": "sk-upstream"}},
            request=httpx.Request("POST", "https://internal.provider.example/v1/chat"),
        )
        status_error = httpx.HTTPStatusError(
            "provider failed",
            request=response.request,
            response=response,
        )

        mapped = adapter.map_error(status_error)

        assert isinstance(mapped, RateLimitError)
        assert mapped.affects_deployment_health is True
        assert mapped.failure_classification is FailureClassification.RATE_LIMIT
        assert str(mapped) == "Provider rate limited request"
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_provider_unclassified_5xx_remains_generic() -> None:
    adapter = OpenAIAdapter(httpx.AsyncClient())
    try:
        response = httpx.Response(
            503,
            json={"error": {"code": "upstream_unavailable", "message": "sk-upstream"}},
            request=httpx.Request("POST", "https://internal.provider.example/v1/chat"),
        )
        status_error = httpx.HTTPStatusError(
            "provider failed",
            request=response.request,
            response=response,
        )

        mapped = adapter.map_error(status_error)

        assert isinstance(mapped, ServiceUnavailableError)
        assert mapped.affects_deployment_health is True
        assert mapped.failure_classification is FailureClassification.GENERIC
        assert str(mapped) == "Provider unavailable"
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("inner_error_key", ["innererror", "inner_error"])
async def test_provider_error_mapper_registry_reuses_adapter_classification(
    inner_error_key: str,
) -> None:
    client = httpx.AsyncClient()
    try:
        registry = _provider_error_mapper_registry(client)
        response = httpx.Response(
            400,
            json={
                "error": {
                    "message": "provider-owned sensitive detail",
                    inner_error_key: {"code": "ResponsibleAIPolicyViolation"},
                }
            },
            request=httpx.Request("POST", "https://provider.invalid/embeddings"),
        )
        error = httpx.HTTPStatusError("bad request", request=response.request, response=response)

        mapped = registry.map_error("azure", error)

        assert isinstance(mapped, InvalidRequestError)
        assert str(mapped) == "Provider rejected request"
        assert mapped.failure_classification is FailureClassification.CONTENT_POLICY
        assert "provider-owned sensitive detail" not in str(mapped)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_provider_error_mapper_registry_limits_openai_rules_to_compatible_providers() -> None:
    client = httpx.AsyncClient()
    try:
        registry = _provider_error_mapper_registry(client)

        assert registry.resolve("openai") is registry.openai
        assert registry.resolve("vllm") is registry.openai
        assert registry.resolve("unknown") is registry.openai
        assert registry.resolve("") is registry.openai
        assert registry.resolve("custom-gateway") is None

        response = httpx.Response(
            400,
            json={
                "error": {
                    "code": "context_length_exceeded",
                    "message": "maximum context length sk-upstream",
                }
            },
            request=httpx.Request("POST", "https://custom.provider.invalid/embeddings"),
        )
        error = httpx.HTTPStatusError("bad request", request=response.request, response=response)

        mapped = registry.map_error("custom-gateway", error)

        assert isinstance(mapped, InvalidRequestError)
        assert mapped.failure_classification is FailureClassification.GENERIC
        assert str(mapped) == "Provider rejected request"
        assert "sk-upstream" not in str(mapped)
        assert "custom.provider.invalid" not in str(mapped)
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_type", [OpenAIAdapter, AzureOpenAIAdapter])
async def test_openai_compatible_adapter_classifies_content_filter_finish_reason(
    adapter_type,
) -> None:
    adapter = adapter_type(httpx.AsyncClient())
    try:
        with pytest.raises(InvalidRequestError, match="Provider rejected request") as error_info:
            await adapter.translate_response(
                {
                    "id": "chatcmpl-filtered",
                    "object": "chat.completion",
                    "created": 1700000000,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": None},
                            "finish_reason": "content_filter",
                        }
                    ],
                },
                model_name="provider-model",
            )

        assert error_info.value.failure_classification is FailureClassification.CONTENT_POLICY
        assert error_info.value.affects_deployment_health is False
    finally:
        await adapter.http_client.aclose()


@pytest.mark.parametrize(
    ("adapter_type", "payload", "expected"),
    [
        (
            OpenAIAdapter,
            {"error": {"code": "context_length_exceeded", "message": "sk-upstream"}},
            FailureClassification.CONTEXT_WINDOW,
        ),
        (
            OpenAIAdapter,
            {"error": {"type": "content_policy_violation", "message": "sk-upstream"}},
            FailureClassification.CONTENT_POLICY,
        ),
        (
            AzureOpenAIAdapter,
            {"error": {"code": "context_length_exceeded", "message": "sk-upstream"}},
            FailureClassification.CONTEXT_WINDOW,
        ),
        (
            AzureOpenAIAdapter,
            {
                "error": {
                    "message": "sk-upstream",
                    "innererror": {"code": "ResponsibleAIPolicyViolation"},
                }
            },
            FailureClassification.CONTENT_POLICY,
        ),
        (
            AnthropicAdapter,
            {
                "error": {
                    "type": "invalid_request_error",
                    "message": "prompt is too long sk-upstream",
                }
            },
            FailureClassification.CONTEXT_WINDOW,
        ),
        (
            AnthropicAdapter,
            {
                "error": {
                    "type": "invalid_request_error",
                    "message": "request blocked by content filtering policy sk-upstream",
                }
            },
            FailureClassification.CONTENT_POLICY,
        ),
        (
            GeminiAdapter,
            {
                "error": {
                    "status": "INVALID_ARGUMENT",
                    "message": "sk-upstream",
                    "details": [{"reason": "CONTEXT_LENGTH_EXCEEDED"}],
                }
            },
            FailureClassification.CONTEXT_WINDOW,
        ),
        (
            GeminiAdapter,
            {"error": {"code": "SAFETY", "message": "sk-upstream"}},
            FailureClassification.CONTENT_POLICY,
        ),
        (
            BedrockAdapter,
            {"__type": "ValidationException", "message": "maximum context length sk-upstream"},
            FailureClassification.CONTEXT_WINDOW,
        ),
        (
            BedrockAdapter,
            {"__type": "GuardrailIntervenedException", "message": "sk-upstream"},
            FailureClassification.CONTENT_POLICY,
        ),
    ],
)
@pytest.mark.asyncio
async def test_provider_adapters_return_typed_sanitized_classified_failures(
    adapter_type,
    payload: dict,
    expected: FailureClassification,
) -> None:
    adapter = adapter_type(httpx.AsyncClient())
    try:
        response = httpx.Response(
            400,
            json=payload,
            request=httpx.Request("POST", "https://internal.provider.example/v1/chat"),
        )
        exc = httpx.HTTPStatusError("bad request", request=response.request, response=response)

        mapped = adapter.map_error(exc)

        assert isinstance(mapped, InvalidRequestError)
        assert str(mapped) == "Provider rejected request"
        assert mapped.failure_classification is expected
        assert "sk-upstream" not in str(mapped)
        assert "internal.provider.example" not in str(mapped)
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_provider_adapter_malformed_error_body_is_sanitized_generic() -> None:
    adapter = OpenAIAdapter(httpx.AsyncClient())
    try:
        response = httpx.Response(
            400,
            content=b"maximum context length sk-upstream",
            request=httpx.Request("POST", "https://internal.provider.example/v1/chat"),
        )
        exc = httpx.HTTPStatusError("bad request", request=response.request, response=response)

        mapped = adapter.map_error(exc)

        assert str(mapped) == "Provider rejected request"
        assert mapped.failure_classification is FailureClassification.GENERIC
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_unread_streaming_provider_error_is_bounded_and_classified() -> None:
    adapter = AzureOpenAIAdapter(httpx.AsyncClient())
    try:
        response = httpx.Response(
            400,
            stream=_AsyncBytes(
                json.dumps(
                    {
                        "error": {
                            "code": "context_length_exceeded",
                            "message": "maximum context length sk-upstream",
                        }
                    }
                ).encode()
            ),
            request=httpx.Request("POST", "https://internal.provider.example/v1/chat"),
        )
        exc = httpx.HTTPStatusError("bad request", request=response.request, response=response)

        details = await read_streaming_provider_error_details(response)
        mapped = adapter.map_error(exc, details=details)

        assert isinstance(mapped, InvalidRequestError)
        assert mapped.failure_classification is FailureClassification.CONTEXT_WINDOW
        assert str(mapped) == "Provider rejected request"
        assert "sk-upstream" not in str(mapped)
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_oversized_streaming_provider_error_uses_status_only() -> None:
    response = httpx.Response(
        400,
        stream=_AsyncBytes(b"{" + b"x" * MAX_PROVIDER_ERROR_BODY_BYTES),
        request=httpx.Request("POST", "https://internal.provider.example/v1/chat"),
    )

    details = await read_streaming_provider_error_details(response)

    assert details.status_code == 400
    assert details.identifiers == frozenset()
    assert details.message is None


@pytest.mark.asyncio
async def test_deeply_nested_streaming_provider_error_uses_status_only() -> None:
    nested = b"[" * 2048 + b"0" + b"]" * 2048
    response = httpx.Response(
        400,
        stream=_AsyncBytes(nested),
        request=httpx.Request("POST", "https://internal.provider.example/v1/chat"),
    )

    details = await read_streaming_provider_error_details(response)

    assert details.status_code == 400
    assert details.identifiers == frozenset()
    assert details.message is None


@pytest.mark.asyncio
async def test_openai_adapter_health_check_uses_custom_auth_headers() -> None:
    adapter = OpenAIAdapter(httpx.AsyncClient())
    try:
        captured: dict[str, object] = {}

        async def fake_get(url, headers, timeout):  # noqa: ANN001, ANN201
            captured["url"] = url
            captured["headers"] = dict(headers)
            captured["timeout"] = timeout
            return httpx.Response(200, json={"data": []}, request=httpx.Request("GET", url))

        adapter.http_client.get = fake_get  # type: ignore[method-assign]

        healthy = await adapter.health_check(
            {
                "provider": "vllm",
                "api_base": "https://vllm.example/v1",
                "api_key": "provider-key",
                "auth_header_name": "X-API-Key",
                "auth_header_format": "{api_key}",
            }
        )

        assert healthy is True
        assert captured["url"] == "https://vllm.example/v1/models"
        assert captured["headers"] == {"X-API-Key": "provider-key"}
        timeout = captured["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        assert timeout.read == 10.0
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_openai_adapter_health_check_defaults_provider_when_missing() -> None:
    adapter = OpenAIAdapter(httpx.AsyncClient())
    try:
        captured: dict[str, object] = {}

        async def fake_get(url, headers, timeout):  # noqa: ANN001, ANN201
            captured["url"] = url
            captured["headers"] = dict(headers)
            captured["timeout"] = timeout
            return httpx.Response(200, json={"data": []}, request=httpx.Request("GET", url))

        adapter.http_client.get = fake_get  # type: ignore[method-assign]

        healthy = await adapter.health_check({"api_key": "provider-key"})

        assert healthy is True
        assert captured["url"] == "https://api.openai.com/v1/models"
        assert captured["headers"] == {"Authorization": "Bearer provider-key"}
        timeout = captured["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        assert timeout.read == 10.0
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_openai_adapter_health_check_returns_false_on_unexpected_error() -> None:
    adapter = OpenAIAdapter(httpx.AsyncClient())
    try:

        async def fake_get(url, headers, timeout):  # noqa: ANN001, ANN201
            del url, headers, timeout
            raise RuntimeError("unexpected client failure")

        adapter.http_client.get = fake_get  # type: ignore[method-assign]

        assert await adapter.health_check({"api_key": "provider-key"}) is False
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_azure_openai_adapter_maps_max_tokens_to_max_completion_tokens_for_gpt5() -> None:
    adapter = AzureOpenAIAdapter(httpx.AsyncClient())
    try:
        req = ChatCompletionRequest(
            model="gpt-5-mini",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=64,
        )
        payload = await adapter.translate_request(
            req,
            {"provider": "azure_openai", "model": "azure_openai/gpt-5-mini"},
        )
        assert "max_tokens" not in payload
        assert payload["max_completion_tokens"] == 64
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_anthropic_adapter_translate_request_and_response() -> None:
    adapter = AnthropicAdapter(httpx.AsyncClient())
    try:
        req = ChatCompletionRequest(
            model="claude-3-5-sonnet-latest",
            messages=[
                {"role": "system", "content": "be concise"},
                {"role": "user", "content": "Say hi"},
            ],
            max_tokens=32,
        )
        upstream = await adapter.translate_request(
            req, {"model": "anthropic/claude-3-5-sonnet-latest"}
        )
        assert upstream["model"] == "claude-3-5-sonnet-latest"
        assert upstream["system"] == "be concise"
        assert upstream["max_tokens"] == 32
        assert upstream["messages"][0]["role"] == "user"

        canonical = await adapter.translate_response(
            {
                "id": "msg_123",
                "model": "claude-3-5-sonnet-latest",
                "content": [{"type": "text", "text": "Hello"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 4, "output_tokens": 2},
            },
            model_name="anthropic/claude-3-5-sonnet-latest",
        )
        payload = canonical.model_dump(mode="json")
        assert payload["choices"][0]["message"]["content"] == "Hello"
        assert payload["usage"]["total_tokens"] == 6
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "assistant_content",
    [
        pytest.param(_OMITTED, id="omitted"),
        pytest.param(None, id="null"),
        pytest.param("", id="empty-string"),
    ],
)
async def test_anthropic_adapter_forwards_tools_and_tool_messages(
    assistant_content: object,
) -> None:
    adapter = AnthropicAdapter(httpx.AsyncClient())
    try:
        req = ChatCompletionRequest.model_validate(
            {
                "model": "claude-3-5-sonnet-latest",
                "max_tokens": 32,
                "messages": [
                    {"role": "user", "content": "search docs for delta"},
                    _assistant_tool_call_message(assistant_content),
                    {"role": "tool", "tool_call_id": "toolu_1", "content": "delta docs result"},
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "docs.search",
                            "description": "Search docs",
                            "parameters": {
                                "type": "object",
                                "properties": {"query": {"type": "string"}},
                            },
                        },
                    }
                ],
                "tool_choice": "required",
            }
        )
        upstream = await adapter.translate_request(req, {})

        assert upstream["tools"] == [
            {
                "name": "docs.search",
                "description": "Search docs",
                "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
            }
        ]
        assert upstream["tool_choice"] == {"type": "any"}
        assert upstream["messages"][1] == {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "docs.search",
                    "input": {"query": "delta"},
                }
            ],
        }
        assert upstream["messages"][2] == {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": "delta docs result"}
            ],
        }
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_anthropic_adapter_translate_response_maps_tool_use_blocks() -> None:
    adapter = AnthropicAdapter(httpx.AsyncClient())
    try:
        canonical = await adapter.translate_response(
            {
                "id": "msg_123",
                "model": "claude-3-5-sonnet-latest",
                "content": [
                    {"type": "text", "text": "Checking."},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "docs.search",
                        "input": {"query": "delta"},
                    },
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 4, "output_tokens": 2},
            },
            model_name="anthropic/claude-3-5-sonnet-latest",
        )
        payload = canonical.model_dump(mode="json")
        message = payload["choices"][0]["message"]
        assert message["content"] == "Checking."
        assert message["tool_calls"] == [
            {
                "id": "toolu_1",
                "type": "function",
                "function": {"name": "docs.search", "arguments": json.dumps({"query": "delta"})},
            }
        ]
        assert payload["choices"][0]["finish_reason"] == "tool_calls"
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stop_reason", "expected"),
    [
        ("refusal", FailureClassification.CONTENT_POLICY),
        ("model_context_window_exceeded", FailureClassification.CONTEXT_WINDOW),
    ],
)
async def test_anthropic_adapter_classifies_documented_stop_reason(
    stop_reason: str,
    expected: FailureClassification,
) -> None:
    adapter = AnthropicAdapter(httpx.AsyncClient())
    try:
        response = httpx.Response(
            200,
            json={
                "id": "msg_blocked",
                "model": "claude-3-5-sonnet-latest",
                "content": [],
                "stop_reason": stop_reason,
                "usage": {"input_tokens": 4, "output_tokens": 0},
            },
            request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
        )

        with pytest.raises(InvalidRequestError, match="Provider rejected request") as error_info:
            await adapter.translate_success_response(response, "anthropic/claude")

        assert error_info.value.failure_classification is expected
        assert error_info.value.affects_deployment_health is False
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_anthropic_adapter_translate_stream_emits_tool_call_chunks() -> None:
    adapter = AnthropicAdapter(httpx.AsyncClient())
    try:
        lines = [
            'data: {"type":"message_start","message":{"id":"msg_1","model":"claude-3-5-sonnet-latest"}}',
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_1","name":"docs.search","input":{}}}',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"query\\":"}}',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"\\"delta\\"}"}}',
            'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"}}',
            'data: {"type":"message_stop"}',
        ]
        out = [line async for line in adapter.translate_stream(_line_stream(lines))]
        assert any('"name":"docs.search"' in line for line in out)
        assert any('"arguments":"{\\"query\\":"' in line for line in out)
        assert any('"finish_reason":"tool_calls"' in line for line in out)
        assert out[-1] == "data: [DONE]"
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_anthropic_adapter_translate_stream_to_openai_chunks() -> None:
    adapter = AnthropicAdapter(httpx.AsyncClient())
    try:
        lines = [
            'data: {"type":"message_start","message":{"id":"msg_1","model":"claude-3-5-sonnet-latest"}}',
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}',
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":" world"}}',
            'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}',
            'data: {"type":"message_stop"}',
        ]
        out = [line async for line in adapter.translate_stream(_line_stream(lines))]
        assert any('"role":"assistant"' in line for line in out)
        assert any('"content":"Hello"' in line for line in out)
        assert any('"content":" world"' in line for line in out)
        assert out[-1] == "data: [DONE]"
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stop_reason", "expected"),
    [
        ("refusal", FailureClassification.CONTENT_POLICY),
        ("model_context_window_exceeded", FailureClassification.CONTEXT_WINDOW),
    ],
)
async def test_anthropic_stream_classified_stop_before_output_is_typed(
    stop_reason: str,
    expected: FailureClassification,
) -> None:
    adapter = AnthropicAdapter(httpx.AsyncClient())
    try:
        lines = [
            'data: {"type":"message_start","message":{"id":"msg_1","model":"claude"}}',
            f'data: {{"type":"message_delta","delta":{{"stop_reason":"{stop_reason}"}}}}',
            'data: {"type":"message_stop"}',
        ]
        translated = adapter.translate_stream(_line_stream(lines)).__aiter__()

        with pytest.raises(InvalidRequestError, match="Provider rejected request") as error_info:
            await anext(translated)

        assert error_info.value.failure_classification is expected
        assert error_info.value.affects_deployment_health is False
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stop_reason", "finish_reason"),
    [
        ("refusal", "content_filter"),
        ("model_context_window_exceeded", "length"),
    ],
)
async def test_anthropic_stream_classified_stop_after_output_finishes_without_retry_signal(
    stop_reason: str,
    finish_reason: str,
) -> None:
    adapter = AnthropicAdapter(httpx.AsyncClient())
    try:
        lines = [
            'data: {"type":"message_start","message":{"id":"msg_1","model":"claude"}}',
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"partial"}}',
            f'data: {{"type":"message_delta","delta":{{"stop_reason":"{stop_reason}"}}}}',
            'data: {"type":"message_stop"}',
        ]

        out = [line async for line in adapter.translate_stream(_line_stream(lines))]

        assert any('"content":"partial"' in line for line in out)
        assert any(f'"finish_reason":"{finish_reason}"' in line for line in out)
        assert out[-1] == "data: [DONE]"
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_anthropic_adapter_stream_error_is_typed_and_sanitized() -> None:
    adapter = AnthropicAdapter(httpx.AsyncClient())
    try:
        lines = [
            "event: error",
            'data: {"type":"error","error":{"type":"invalid_request_error",'
            '"message":"prompt is too long sk-upstream"}}',
        ]

        with pytest.raises(InvalidRequestError, match="Provider rejected request") as error_info:
            async for _ in adapter.translate_stream(_line_stream(lines)):
                pass

        assert error_info.value.failure_classification is FailureClassification.CONTEXT_WINDOW
        assert "sk-upstream" not in str(error_info.value)
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_anthropic_adapter_stream_rate_limit_is_retryable() -> None:
    adapter = AnthropicAdapter(httpx.AsyncClient())
    try:
        lines = [
            'data: {"type":"error","error":{"type":"rate_limit_error",'
            '"message":"too many requests sk-upstream"}}'
        ]

        with pytest.raises(RateLimitError, match="Provider rate limited request") as error_info:
            async for _ in adapter.translate_stream(_line_stream(lines)):
                pass

        assert error_info.value.failure_classification is FailureClassification.RATE_LIMIT
        assert error_info.value.affects_deployment_health is True
        assert "sk-upstream" not in str(error_info.value)
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lines",
    [
        ["data: [DONE]"],
        ['data: {"type":"message_start","message":{"id":"msg_1","model":"claude"}}'],
        ["data: not-json"],
    ],
)
async def test_anthropic_stream_rejects_terminal_only_or_truncated_input(
    lines: list[str],
) -> None:
    adapter = AnthropicAdapter(httpx.AsyncClient())
    try:
        with pytest.raises(ServiceUnavailableError) as error_info:
            async for _ in adapter.translate_stream(_line_stream(lines)):
                pass

        assert str(error_info.value) == INVALID_PROVIDER_RESPONSE_MESSAGE
        assert error_info.value.affects_deployment_health is True
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_gemini_adapter_translate_request_and_response() -> None:
    adapter = GeminiAdapter(httpx.AsyncClient())
    try:
        req = ChatCompletionRequest(
            model="gemini-2.5-flash",
            messages=[
                {"role": "system", "content": "be concise"},
                {"role": "user", "content": "Say hi"},
            ],
            max_tokens=32,
        )
        upstream = await adapter.translate_request(req, {"model": "gemini/gemini-2.5-flash"})
        assert upstream["systemInstruction"]["parts"][0]["text"] == "be concise"
        assert upstream["contents"][0]["role"] == "user"
        assert upstream["generationConfig"]["maxOutputTokens"] == 32

        canonical = await adapter.translate_response(
            {
                "responseId": "resp_123",
                "candidates": [
                    {
                        "content": {"parts": [{"text": "Hello"}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 4,
                    "candidatesTokenCount": 2,
                    "totalTokenCount": 6,
                },
            },
            model_name="gemini/gemini-2.5-flash",
        )
        payload = canonical.model_dump(mode="json")
        assert payload["choices"][0]["message"]["content"] == "Hello"
        assert payload["usage"]["total_tokens"] == 6
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_payload",
    [
        pytest.param(
            {
                "model": "gemini-2.5-flash",
                "messages": [{"role": "user", "content": "search"}],
                "tools": [
                    {
                        "type": "function",
                        "function": {"name": "search", "parameters": {"type": "object"}},
                    }
                ],
            },
            id="tool-definition",
        ),
        pytest.param(
            {
                "model": "gemini-2.5-flash",
                "messages": [
                    {"role": "user", "content": "search"},
                    _assistant_tool_call_message(),
                    {"role": "tool", "tool_call_id": "toolu_1", "content": "result"},
                ],
            },
            id="tool-history",
        ),
    ],
)
async def test_gemini_adapter_rejects_unsupported_tool_calling(
    request_payload: dict[str, object],
) -> None:
    adapter = GeminiAdapter(httpx.AsyncClient())
    try:
        request = ChatCompletionRequest.model_validate(request_payload)

        with pytest.raises(InvalidRequestError, match="does not support tool calling") as exc_info:
            await adapter.translate_request(request, {"model": "gemini/gemini-2.5-flash"})

        assert exc_info.value.param == "tools"
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_payload",
    [
        {"promptFeedback": {"blockReason": "SAFETY"}},
        {
            "candidates": [
                {
                    "content": {"parts": []},
                    "finishReason": "PROHIBITED_CONTENT",
                }
            ]
        },
        {
            "candidates": [
                {
                    "content": {"parts": []},
                    "finishReason": "IMAGE_RECITATION",
                }
            ]
        },
        {
            "candidates": [
                {
                    "content": {"parts": []},
                    "finishReason": "ESCALATION",
                }
            ]
        },
    ],
)
async def test_gemini_adapter_classifies_documented_policy_response(
    response_payload: dict,
) -> None:
    adapter = GeminiAdapter(httpx.AsyncClient())
    try:
        with pytest.raises(InvalidRequestError, match="Provider rejected request") as error_info:
            await adapter.translate_response(
                response_payload,
                model_name="gemini/gemini-2.5-flash",
            )

        assert error_info.value.failure_classification is FailureClassification.CONTENT_POLICY
        assert error_info.value.affects_deployment_health is False
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "finish_reason",
    [
        "LANGUAGE",
        "OTHER",
        "MALFORMED_FUNCTION_CALL",
        "IMAGE_OTHER",
        "NO_IMAGE",
        "UNEXPECTED_TOOL_CALL",
        "TOO_MANY_TOOL_CALLS",
        "MISSING_THOUGHT_SIGNATURE",
        "MALFORMED_RESPONSE",
        "FUTURE_PROVIDER_FAILURE",
    ],
)
async def test_gemini_adapter_rejects_non_success_finish_reason_as_malformed(
    finish_reason: str,
) -> None:
    adapter = GeminiAdapter(httpx.AsyncClient())
    try:
        with pytest.raises(ServiceUnavailableError) as error_info:
            await adapter.translate_response(
                {
                    "secret": "sk-upstream",
                    "candidates": [
                        {
                            "content": {"parts": []},
                            "finishReason": finish_reason,
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 4,
                        "candidatesTokenCount": 0,
                        "totalTokenCount": 4,
                    },
                },
                model_name="gemini/gemini-2.5-flash",
            )

        error = error_info.value
        assert str(error) == INVALID_PROVIDER_RESPONSE_MESSAGE
        assert error.affects_deployment_health is True
        assert error.failure_classification is FailureClassification.GENERIC
        assert "sk-upstream" not in str(error)
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_gemini_adapter_preserves_max_tokens_as_successful_length_finish() -> None:
    adapter = GeminiAdapter(httpx.AsyncClient())
    try:
        canonical = await adapter.translate_response(
            {
                "candidates": [
                    {
                        "content": {"parts": [{"text": "partial"}]},
                        "finishReason": "MAX_TOKENS",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 4,
                    "candidatesTokenCount": 2,
                    "totalTokenCount": 6,
                },
            },
            model_name="gemini/gemini-2.5-flash",
        )

        assert canonical.choices[0].finish_reason == "length"
        assert canonical.choices[0].message.content == "partial"
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_bedrock_adapter_translate_request_and_response() -> None:
    adapter = BedrockAdapter(httpx.AsyncClient())
    try:
        req = ChatCompletionRequest(
            model="anthropic.claude-3-5-sonnet-20240620-v1:0",
            messages=[
                {"role": "system", "content": "be concise"},
                {"role": "user", "content": "Say hi"},
            ],
            max_tokens=32,
        )
        upstream = await adapter.translate_request(
            req, {"model": "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0"}
        )
        assert upstream["system"][0]["text"] == "be concise"
        assert upstream["messages"][0]["role"] == "user"
        assert upstream["inferenceConfig"]["maxTokens"] == 32

        canonical = await adapter.translate_response(
            {
                "requestId": "req_123",
                "output": {
                    "message": {
                        "content": [{"text": "Hello"}],
                    }
                },
                "stopReason": "end_turn",
                "usage": {"inputTokens": 4, "outputTokens": 2, "totalTokens": 6},
            },
            model_name="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
        )
        payload = canonical.model_dump(mode="json")
        assert payload["choices"][0]["message"]["content"] == "Hello"
        assert payload["usage"]["total_tokens"] == 6
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stop_reason", "expected"),
    [
        ("guardrail_intervened", FailureClassification.CONTENT_POLICY),
        ("content_filtered", FailureClassification.CONTENT_POLICY),
        ("model_context_window_exceeded", FailureClassification.CONTEXT_WINDOW),
    ],
)
async def test_bedrock_adapter_classifies_documented_stop_reason(
    stop_reason: str,
    expected: FailureClassification,
) -> None:
    adapter = BedrockAdapter(httpx.AsyncClient())
    try:
        with pytest.raises(InvalidRequestError, match="Provider rejected request") as error_info:
            await adapter.translate_response(
                {
                    "requestId": "req_blocked",
                    "output": {"message": {"content": []}},
                    "stopReason": stop_reason,
                    "usage": {"inputTokens": 4, "outputTokens": 0, "totalTokens": 4},
                },
                model_name="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
            )

        assert error_info.value.failure_classification is expected
        assert error_info.value.affects_deployment_health is False
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_bedrock_adapter_only_sends_explicit_sampling_params() -> None:
    adapter = BedrockAdapter(httpx.AsyncClient())
    try:
        base = {
            "model": "anthropic.claude-3-5-sonnet-20240620-v1:0",
            "messages": [{"role": "user", "content": "Say hi"}],
        }

        omitted = await adapter.translate_request(ChatCompletionRequest.model_validate(base), {})
        assert "inferenceConfig" not in omitted

        temperature = await adapter.translate_request(
            ChatCompletionRequest.model_validate({**base, "temperature": 0.7}),
            {},
        )
        assert temperature["inferenceConfig"] == {"temperature": 0.7}

        top_p = await adapter.translate_request(
            ChatCompletionRequest.model_validate({**base, "top_p": 0.8}),
            {},
        )
        assert top_p["inferenceConfig"] == {"topP": 0.8}

        top_p_null = await adapter.translate_request(
            ChatCompletionRequest.model_validate({**base, "top_p": None}),
            {},
        )
        assert "inferenceConfig" not in top_p_null

        both_explicit = await adapter.translate_request(
            ChatCompletionRequest.model_validate({**base, "temperature": 0.5, "top_p": 0.8}),
            {},
        )
        assert both_explicit["inferenceConfig"] == {"temperature": 0.5, "topP": 0.8}
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_bedrock_adapter_sanitizes_provider_error_message() -> None:
    adapter = BedrockAdapter(httpx.AsyncClient())
    try:
        response = httpx.Response(
            400,
            json={"message": "`temperature` and `top_p` cannot both be specified"},
            request=httpx.Request(
                "POST", "https://bedrock-runtime.us-east-1.amazonaws.com/model/claude/converse"
            ),
        )
        exc = httpx.HTTPStatusError("bad request", request=response.request, response=response)
        mapped = adapter.map_error(exc)
        assert str(mapped) == "Provider rejected request"
        assert mapped.failure_classification is FailureClassification.GENERIC
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_bedrock_adapter_translate_stream_to_openai_chunks() -> None:
    adapter = BedrockAdapter(httpx.AsyncClient())
    assert adapter.stream_uses_bytes is True
    try:
        frames = b"".join(
            [
                _encode_eventstream_message(
                    {":message-type": "event", ":event-type": "messageStart"},
                    {"role": "assistant"},
                ),
                _encode_eventstream_message(
                    {":message-type": "event", ":event-type": "contentBlockDelta"},
                    {"contentBlockIndex": 0, "delta": {"text": "Hello"}},
                ),
                _encode_eventstream_message(
                    {":message-type": "event", ":event-type": "contentBlockDelta"},
                    {"contentBlockIndex": 0, "delta": {"text": " world"}},
                ),
                _encode_eventstream_message(
                    {":message-type": "event", ":event-type": "messageStop"},
                    {"stopReason": "end_turn"},
                ),
                _encode_eventstream_message(
                    {":message-type": "event", ":event-type": "metadata"},
                    {"usage": {"inputTokens": 4, "outputTokens": 2, "totalTokens": 6}},
                ),
            ]
        )
        # Split arbitrarily mid-frame to make sure the parser buffers correctly
        # across chunk boundaries, just like real HTTP reads would arrive.
        split = len(frames) // 2
        out = [
            line
            async for line in adapter.translate_stream(
                _byte_stream([frames[:split], frames[split:]]),
                model_name="bedrock-public-model",
            )
        ]
        payloads = _stream_json_payloads(out)

        assert any('"role":"assistant"' in line for line in out)
        assert any('"content":"Hello"' in line for line in out)
        assert any('"content":" world"' in line for line in out)
        assert any('"finish_reason":"stop"' in line for line in out)
        assert any('"total_tokens":6' in line for line in out)
        assert all(payload["model"] == "bedrock-public-model" for payload in payloads)
        assert out[-1] == "data: [DONE]"
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_bedrock_adapter_stream_stop_failure_is_not_reported_as_done() -> None:
    adapter = BedrockAdapter(httpx.AsyncClient())
    try:
        frames = b"".join(
            [
                _encode_eventstream_message(
                    {":message-type": "event", ":event-type": "messageStart"},
                    {"role": "assistant"},
                ),
                _encode_eventstream_message(
                    {":message-type": "event", ":event-type": "messageStop"},
                    {"stopReason": "guardrail_intervened"},
                ),
            ]
        )
        translated = adapter.translate_stream(_byte_stream([frames])).__aiter__()

        with pytest.raises(InvalidRequestError, match="Provider rejected request") as error_info:
            await anext(translated)

        assert error_info.value.failure_classification is FailureClassification.CONTENT_POLICY
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_bedrock_adapter_stream_classified_stop_after_output_is_terminal() -> None:
    adapter = BedrockAdapter(httpx.AsyncClient())
    try:
        frames = b"".join(
            [
                _encode_eventstream_message(
                    {":message-type": "event", ":event-type": "messageStart"},
                    {"role": "assistant"},
                ),
                _encode_eventstream_message(
                    {":message-type": "event", ":event-type": "contentBlockDelta"},
                    {"contentBlockIndex": 0, "delta": {"text": "partial"}},
                ),
                _encode_eventstream_message(
                    {":message-type": "event", ":event-type": "messageStop"},
                    {"stopReason": "guardrail_intervened"},
                ),
            ]
        )

        out = [line async for line in adapter.translate_stream(_byte_stream([frames]))]

        assert any('"content":"partial"' in line for line in out)
        assert any('"finish_reason":"content_filter"' in line for line in out)
        assert out[-1] == "data: [DONE]"
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("chunks", [[], [b""]])
async def test_bedrock_adapter_stream_rejects_empty_input(chunks: list[bytes]) -> None:
    adapter = BedrockAdapter(httpx.AsyncClient())
    try:
        with pytest.raises(ServiceUnavailableError) as error_info:
            async for _ in adapter.translate_stream(_byte_stream(chunks)):
                pass

        assert str(error_info.value) == INVALID_PROVIDER_RESPONSE_MESSAGE
        assert error_info.value.affects_deployment_health is True
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_bedrock_adapter_translate_stream_uses_sequential_tool_call_indexes() -> None:
    adapter = BedrockAdapter(httpx.AsyncClient())
    try:
        frames = b"".join(
            [
                _encode_eventstream_message(
                    {":message-type": "event", ":event-type": "messageStart"},
                    {"role": "assistant"},
                ),
                _encode_eventstream_message(
                    {":message-type": "event", ":event-type": "contentBlockDelta"},
                    {"contentBlockIndex": 0, "delta": {"text": "Let me check."}},
                ),
                _encode_eventstream_message(
                    {":message-type": "event", ":event-type": "contentBlockStart"},
                    {
                        "contentBlockIndex": 1,
                        "start": {"toolUse": {"toolUseId": "toolu_1", "name": "docs.search"}},
                    },
                ),
                _encode_eventstream_message(
                    {":message-type": "event", ":event-type": "contentBlockDelta"},
                    {"contentBlockIndex": 1, "delta": {"toolUse": {"input": '{"query":"delta"}'}}},
                ),
                _encode_eventstream_message(
                    {":message-type": "event", ":event-type": "messageStop"},
                    {"stopReason": "tool_use"},
                ),
            ]
        )

        out = [
            line
            async for line in adapter.translate_stream(
                _byte_stream([frames]), model_name="bedrock-public-model"
            )
        ]
        payloads = _stream_json_payloads(out)
        tool_deltas = [
            tool_call
            for payload in payloads
            for choice in payload.get("choices", [])
            for tool_call in (choice.get("delta") or {}).get("tool_calls", [])
        ]

        assert tool_deltas[0]["index"] == 0
        assert tool_deltas[1]["index"] == 0
        assert all(payload["model"] == "bedrock-public-model" for payload in payloads)
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "assistant_content",
    [
        pytest.param(_OMITTED, id="omitted"),
        pytest.param(None, id="null"),
        pytest.param("", id="empty-string"),
    ],
)
async def test_bedrock_adapter_forwards_tools_and_tool_messages(
    assistant_content: object,
) -> None:
    adapter = BedrockAdapter(httpx.AsyncClient())
    try:
        req = ChatCompletionRequest.model_validate(
            {
                "model": "anthropic.claude-3-5-sonnet-20240620-v1:0",
                "max_tokens": 32,
                "messages": [
                    {"role": "user", "content": "search docs for delta"},
                    _assistant_tool_call_message(assistant_content),
                    {"role": "tool", "tool_call_id": "toolu_1", "content": "delta docs result"},
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "docs.search",
                            "description": "Search docs",
                            "parameters": {
                                "type": "object",
                                "properties": {"query": {"type": "string"}},
                            },
                        },
                    }
                ],
                "tool_choice": {"type": "function", "function": {"name": "docs.search"}},
            }
        )
        upstream = await adapter.translate_request(req, {})

        assert upstream["toolConfig"] == {
            "tools": [
                {
                    "toolSpec": {
                        "name": "docs.search",
                        "description": "Search docs",
                        "inputSchema": {
                            "json": {"type": "object", "properties": {"query": {"type": "string"}}}
                        },
                    }
                }
            ],
            "toolChoice": {"tool": {"name": "docs.search"}},
        }
        assert upstream["messages"][1] == {
            "role": "assistant",
            "content": [
                {
                    "toolUse": {
                        "toolUseId": "toolu_1",
                        "name": "docs.search",
                        "input": {"query": "delta"},
                    }
                }
            ],
        }
        assert upstream["messages"][2] == {
            "role": "user",
            "content": [
                {"toolResult": {"toolUseId": "toolu_1", "content": [{"text": "delta docs result"}]}}
            ],
        }
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_bedrock_adapter_translate_response_maps_tool_use_blocks() -> None:
    adapter = BedrockAdapter(httpx.AsyncClient())
    try:
        canonical = await adapter.translate_response(
            {
                "output": {
                    "message": {
                        "content": [
                            {"text": "Checking."},
                            {
                                "toolUse": {
                                    "toolUseId": "toolu_1",
                                    "name": "docs.search",
                                    "input": {"query": "delta"},
                                }
                            },
                        ]
                    }
                },
                "stopReason": "tool_use",
                "usage": {"inputTokens": 4, "outputTokens": 2, "totalTokens": 6},
            },
            model_name="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
        )
        payload = canonical.model_dump(mode="json")
        message = payload["choices"][0]["message"]
        assert message["content"] == "Checking."
        assert message["tool_calls"] == [
            {
                "id": "toolu_1",
                "type": "function",
                "function": {"name": "docs.search", "arguments": json.dumps({"query": "delta"})},
            }
        ]
        assert payload["choices"][0]["finish_reason"] == "tool_calls"
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_bedrock_adapter_translate_stream_raises_on_exception_event() -> None:
    adapter = BedrockAdapter(httpx.AsyncClient())
    try:
        frame = _encode_eventstream_message(
            {":message-type": "exception", ":exception-type": "validationException"},
            {"message": "Malformed input request"},
        )
        with pytest.raises(InvalidRequestError, match="Provider rejected request") as error_info:
            async for _ in adapter.translate_stream(_byte_stream([frame])):
                pass
        assert error_info.value.failure_classification is FailureClassification.GENERIC
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
async def test_bedrock_adapter_translate_stream_classifies_retryable_errors() -> None:
    adapter = BedrockAdapter(httpx.AsyncClient())
    try:
        throttle_frame = _encode_eventstream_message(
            {":message-type": "exception", ":exception-type": "throttlingException"},
            {"message": "Too many requests"},
        )
        with pytest.raises(
            RateLimitError, match="Provider rate limited request"
        ) as rate_limit_info:
            async for _ in adapter.translate_stream(_byte_stream([throttle_frame])):
                pass
        assert rate_limit_info.value.affects_deployment_health is True

        server_frame = _encode_eventstream_message(
            {":message-type": "exception", ":exception-type": "internalServerException"},
            {"message": "Internal failure"},
        )
        with pytest.raises(
            ServiceUnavailableError, match="Provider unavailable"
        ) as unavailable_info:
            async for _ in adapter.translate_stream(_byte_stream([server_frame])):
                pass
        assert unavailable_info.value.affects_deployment_health is True

        unknown_frame = _encode_eventstream_message(
            {":message-type": "error", ":event-type": "somethingUnexpected"},
            {"message": "mystery failure"},
        )
        with pytest.raises(ServiceUnavailableError, match="Provider unavailable"):
            async for _ in adapter.translate_stream(_byte_stream([unknown_frame])):
                pass
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exception_type", "message", "error_type", "classification", "affects_health"),
    [
        (
            "internalServerException",
            "guardrail intervened",
            ServiceUnavailableError,
            FailureClassification.GENERIC,
            True,
        ),
        (
            "modelStreamErrorException",
            "guardrail intervened",
            ServiceUnavailableError,
            FailureClassification.GENERIC,
            True,
        ),
        (
            "serviceUnavailableException",
            "input is too long",
            ServiceUnavailableError,
            FailureClassification.GENERIC,
            True,
        ),
        (
            "throttlingException",
            "input is too long",
            RateLimitError,
            FailureClassification.RATE_LIMIT,
            True,
        ),
        (
            "validationException",
            "input is too long",
            InvalidRequestError,
            FailureClassification.CONTEXT_WINDOW,
            False,
        ),
        (
            "validationException",
            "guardrail intervened",
            InvalidRequestError,
            FailureClassification.CONTENT_POLICY,
            False,
        ),
    ],
)
async def test_bedrock_stream_exception_type_precedes_message_classification(
    exception_type: str,
    message: str,
    error_type: type[Exception],
    classification: FailureClassification,
    affects_health: bool,
) -> None:
    adapter = BedrockAdapter(httpx.AsyncClient())
    try:
        frame = _encode_eventstream_message(
            {":message-type": "exception", ":exception-type": exception_type},
            {"message": message},
        )

        with pytest.raises(error_type) as error_info:
            async for _ in adapter.translate_stream(_byte_stream([frame])):
                pass

        assert error_info.value.failure_classification is classification
        assert error_info.value.affects_deployment_health is affects_health
    finally:
        await adapter.http_client.aclose()
