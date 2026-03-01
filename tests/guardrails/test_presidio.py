from __future__ import annotations

import pytest

from src.guardrails.base import GuardrailAction
from src.guardrails.exceptions import GuardrailViolationError
from src.guardrails.presidio import PresidioGuardrail


@pytest.mark.asyncio
async def test_presidio_anonymizes_email_and_ssn():
    guardrail = PresidioGuardrail(
        anonymize=True,
        entities=["EMAIL_ADDRESS", "US_SSN"],
    )
    payload = {
        "messages": [
            {"role": "user", "content": "email me at alice@example.com and ssn 123-45-6789"},
        ]
    }

    modified = await guardrail.async_pre_call_hook({}, None, payload, "completion")
    assert modified is not None
    content = modified["messages"][0]["content"]
    assert "alice@example.com" not in content
    assert "123-45-6789" not in content


@pytest.mark.asyncio
async def test_presidio_blocks_when_detect_only_mode():
    guardrail = PresidioGuardrail(
        anonymize=False,
        action=GuardrailAction.BLOCK,
        entities=["EMAIL_ADDRESS"],
    )
    payload = {"messages": [{"role": "user", "content": "reach me: bob@example.com"}]}

    with pytest.raises(GuardrailViolationError):
        await guardrail.async_pre_call_hook({}, None, payload, "completion")


@pytest.mark.asyncio
async def test_presidio_anonymizes_pii_in_non_content_fields():
    guardrail = PresidioGuardrail(
        anonymize=True,
        entities=["EMAIL_ADDRESS", "US_SSN"],
    )
    payload = {
        "messages": [
            {
                "role": "user",
                "content": "safe content",
                "name": "alice@example.com",
                "function_call": {"name": "send", "arguments": "{\"ssn\":\"123-45-6789\"}"},
                "tool_calls": [{"id": "bob@example.com", "type": "function"}],
            },
        ]
    }

    modified = await guardrail.async_pre_call_hook({}, None, payload, "completion")
    assert modified is not None
    message = modified["messages"][0]
    assert "alice@example.com" not in message["name"]
    assert "123-45-6789" not in message["function_call"]["arguments"]
    assert "bob@example.com" not in message["tool_calls"][0]["id"]


@pytest.mark.asyncio
async def test_presidio_blocks_when_pii_only_in_non_content_fields():
    guardrail = PresidioGuardrail(
        anonymize=False,
        action=GuardrailAction.BLOCK,
        entities=["EMAIL_ADDRESS"],
    )
    payload = {
        "messages": [
            {
                "role": "user",
                "content": "safe content",
                "tool_call_id": "bob@example.com",
            },
        ]
    }

    with pytest.raises(GuardrailViolationError):
        await guardrail.async_pre_call_hook({}, None, payload, "completion")
