"""Provider refusal evidence: only a provider's own HTTP error status counts."""

import gzip
import json
from unittest.mock import MagicMock

import httpx
import pytest

from src.models.errors import FailureClassification, InvalidRequestError
from src.providers.anthropic import AnthropicAdapter, map_anthropic_stream_error
from src.providers.anthropic_messages import AnthropicNativeMessagesAdapter
from src.providers.base import (
    bound_provider_error_response_body,
    map_standard_provider_error,
    map_standard_provider_status_error,
    provider_refused_request,
    read_streaming_provider_error_details,
)
from src.providers.error_body import MAX_PROVIDER_ERROR_BODY_BYTES
from src.providers.openai import OpenAIAdapter
from src.providers.openai_responses import OpenAINativeResponsesAdapter


def _status_error(status_code: int, body: dict | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://provider.test/v1/chat/completions")
    response = httpx.Response(status_code, json=body or {}, request=request)
    return httpx.HTTPStatusError("provider error", request=request, response=response)


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
def test_provider_error_status_that_proves_no_work_is_a_refusal(status_code):
    mapped = map_standard_provider_error(_status_error(status_code))

    assert mapped.provider_status_code == status_code
    assert provider_refused_request(mapped)


@pytest.mark.parametrize("status_code", [408, 409, 413, 429, 500, 502, 503, 529])
def test_provider_error_status_that_may_follow_work_is_not_a_refusal(status_code):
    mapped = map_standard_provider_error(_status_error(status_code))

    assert mapped.provider_status_code == status_code
    assert not provider_refused_request(mapped)


def test_content_filter_rejection_is_not_a_refusal():
    # Azure OpenAI bills a prompt that its content filter rejects with a 400.
    mapped = OpenAIAdapter(MagicMock()).map_error(
        _status_error(400, {"error": {"message": "filtered", "code": "content_filter"}})
    )

    assert mapped.failure_classification is FailureClassification.CONTENT_POLICY
    assert mapped.provider_status_code == 400
    assert not provider_refused_request(mapped)


def test_errors_read_after_a_success_status_are_not_refusals():
    # These statuses come from a stream event or a stop reason, after the provider worked.
    streamed = map_anthropic_stream_error(
        {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}
    )
    synthetic = map_standard_provider_status_error(400)

    assert isinstance(streamed, InvalidRequestError)
    assert streamed.provider_status_code is None
    assert not provider_refused_request(streamed)
    assert not provider_refused_request(synthetic)
    assert not provider_refused_request(ValueError("not a provider error"))


def test_native_adapters_keep_the_provider_status_when_they_forward_the_text():
    body = {
        "type": "error",
        "error": {"type": "invalid_request_error", "message": "max_tokens: too large"},
    }
    messages = AnthropicNativeMessagesAdapter(AnthropicAdapter(MagicMock()), MagicMock())
    inner = OpenAIAdapter(MagicMock())
    responses = OpenAINativeResponsesAdapter(
        inner, MagicMock(), classify_failure=inner._classify_failure
    )

    for adapter in (messages, responses):
        mapped = adapter.map_error(_status_error(400, body))
        assert mapped.message == "max_tokens: too large"
        assert mapped.provider_status_code == 400
        assert provider_refused_request(mapped)


class _Bytes(httpx.AsyncByteStream):
    def __init__(self, content: bytes, *, fail: bool = False) -> None:
        self._content = content
        self._fail = fail

    async def __aiter__(self):
        yield self._content
        if self._fail:
            raise httpx.ReadError("connection lost while reading the error body")

    async def aclose(self) -> None:
        return None


_CONTENT_FILTER = json.dumps({"error": {"message": "filtered", "code": "content_filter"}}).encode()
_INVALID_REQUEST = json.dumps(
    {"error": {"message": "bad value", "type": "invalid_request_error"}}
).encode()


async def _hooked_status_error(
    content: bytes, headers: dict[str, str] | None = None
) -> httpx.HTTPStatusError:
    """A 400 after the shared upstream client's response hook, as a non-stream call reads it."""
    request = httpx.Request("POST", "https://provider.test/v1/chat/completions")
    response = httpx.Response(400, headers=headers or {}, stream=_Bytes(content), request=request)
    await bound_provider_error_response_body(response)
    await response.aread()
    return httpx.HTTPStatusError("provider error", request=request, response=response)


async def test_a_readable_error_body_through_the_response_hook_is_a_refusal():
    mapped = OpenAIAdapter(MagicMock()).map_error(await _hooked_status_error(_INVALID_REQUEST))

    assert mapped.provider_error_body_parsed
    assert provider_refused_request(mapped)


@pytest.mark.parametrize(
    ("content", "headers"),
    [
        # The hook never decompresses an error body, so its classification is unknown.
        (gzip.compress(_CONTENT_FILTER), {"content-encoding": "gzip"}),
        # The hook cuts a body above the limit, so the JSON cannot be parsed.
        (_CONTENT_FILTER[:-1] + b" " * MAX_PROVIDER_ERROR_BODY_BYTES + b"}", None),
        (b"Bad Request", {"content-type": "text/plain"}),
    ],
    ids=["compressed", "oversized", "not-json"],
)
async def test_an_error_body_that_cannot_be_classified_is_not_a_refusal(content, headers):
    # Each body is an Azure content-filter refusal, which Azure can bill, or could be one.
    mapped = OpenAIAdapter(MagicMock()).map_error(await _hooked_status_error(content, headers))

    assert mapped.provider_status_code == 400
    assert not mapped.provider_error_body_parsed
    assert not provider_refused_request(mapped)


@pytest.mark.parametrize("fail", [False, True], ids=["read", "read-failed"])
async def test_a_stream_error_body_counts_only_when_the_gateway_read_it(fail):
    request = httpx.Request("POST", "https://provider.test/v1/chat/completions")
    response = httpx.Response(400, stream=_Bytes(_INVALID_REQUEST, fail=fail), request=request)
    details = await read_streaming_provider_error_details(response)
    error = httpx.HTTPStatusError("provider error", request=request, response=response)

    mapped = OpenAIAdapter(MagicMock()).map_error(error, details=details)

    assert mapped.provider_status_code == 400
    assert provider_refused_request(mapped) is not fail


def test_native_adapters_keep_an_unreadable_body_out_of_the_refusals():
    request = httpx.Request("POST", "https://provider.test/v1/chat/completions")
    response = httpx.Response(400, content=b"<html>Bad Request</html>", request=request)
    error = httpx.HTTPStatusError("provider error", request=request, response=response)
    messages = AnthropicNativeMessagesAdapter(AnthropicAdapter(MagicMock()), MagicMock())
    inner = OpenAIAdapter(MagicMock())
    responses = OpenAINativeResponsesAdapter(
        inner, MagicMock(), classify_failure=inner._classify_failure
    )

    for adapter in (messages, responses):
        mapped = adapter.map_error(error)
        assert mapped.provider_status_code == 400
        assert not provider_refused_request(mapped)
