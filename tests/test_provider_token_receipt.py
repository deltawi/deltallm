import pytest

from src.providers.token_receipt import anthropic_token_receipt, openai_token_receipt
from src.providers.bedrock import BedrockAdapter


def test_openai_receipt_preserves_reported_cached_input_without_exposing_public_fields():
    receipt = openai_token_receipt(
        {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 5,
                "total_tokens": 105,
                "prompt_tokens_details": {"cached_tokens": 60},
            }
        }
    )
    assert receipt.cached_input_tokens == 60
    assert receipt.total_tokens == 105


def test_missing_cache_details_are_unknown_not_reported_zero():
    receipt = openai_token_receipt(
        {"usage": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105}}
    )
    assert receipt.cached_input_tokens is None


@pytest.mark.parametrize(
    "values",
    [
        {"prompt_tokens": True, "completion_tokens": 0, "total_tokens": 1},
        {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 12},
        {
            "prompt_tokens": 10,
            "completion_tokens": 1,
            "total_tokens": 11,
            "prompt_tokens_details": {"cached_tokens": 11},
        },
    ],
)
def test_invalid_billing_counts_are_unknown(values):
    assert openai_token_receipt({"usage": values}) is None


def test_anthropic_includes_cache_reads_but_rejects_unsupported_cache_creation_pricing():
    payload = {"usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 20}}
    receipt = anthropic_token_receipt(payload)
    assert (
        receipt.input_tokens == 30
        and receipt.total_tokens == 35
        and receipt.cached_input_tokens == 20
    )
    payload["usage"]["cache_creation_input_tokens"] = 5
    assert anthropic_token_receipt(payload) is None


def test_malformed_false_cache_receipt_is_not_reported_as_zero():
    assert (
        anthropic_token_receipt(
            {
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_read_input_tokens": False,
                }
            }
        )
        is None
    )


def test_bedrock_rejects_unpriced_cache_creation_receipt():
    assert (
        BedrockAdapter(None).reported_token_receipt(
            {
                "usage": {
                    "inputTokens": 10,
                    "outputTokens": 5,
                    "totalTokens": 15,
                    "cacheWriteInputTokens": 10,
                }
            }
        )
        is None
    )
