"""Provider refusal evidence: only a provider's own HTTP error status counts."""

from unittest.mock import MagicMock

import httpx
import pytest

from src.models.errors import FailureClassification, InvalidRequestError
from src.providers.anthropic import AnthropicAdapter, map_anthropic_stream_error
from src.providers.anthropic_messages import AnthropicNativeMessagesAdapter
from src.providers.base import (
    map_standard_provider_error,
    map_standard_provider_status_error,
    provider_refused_request,
)
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
