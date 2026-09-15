from __future__ import annotations

import pytest

from src.guardrails.base import GuardrailAction
from src.guardrails.exceptions import GuardrailViolationError
from src.guardrails.presidio import PresidioGuardrail
from src.blocking_work import BlockingWorkExecutor


@pytest.mark.parametrize(
    "text,expected",
    [
        ("hello person@example.com", ["person@example.com"]),
        (
            "first.last+tag@example.co.uk;next@test.io",
            ["first.last+tag@example.co.uk", "next@test.io"],
        ),
        ("a" * 20000, []),
        ("a" * 20000 + "@invalid", []),
    ],
)
def test_fallback_email_pattern_keeps_detection_at_local_part_boundaries(text, expected):
    assert PresidioGuardrail._PATTERN_MAP["EMAIL_ADDRESS"].findall(text) == expected


@pytest.fixture
async def executor():
    pool = BlockingWorkExecutor(
        allocation="guardrail",
        workers=1,
        max_pending=2,
        max_bytes=1048576,
        timeout_seconds=1,
        shutdown_seconds=1,
    )
    yield pool
    await pool.shutdown()


@pytest.mark.asyncio
async def test_presidio_anonymizes_email_and_ssn(executor):
    guardrail = PresidioGuardrail(
        executor=executor,
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
async def test_presidio_blocks_when_detect_only_mode(executor):
    guardrail = PresidioGuardrail(
        executor=executor,
        anonymize=False,
        action=GuardrailAction.BLOCK,
        entities=["EMAIL_ADDRESS"],
    )
    payload = {"messages": [{"role": "user", "content": "reach me: bob@example.com"}]}

    with pytest.raises(GuardrailViolationError):
        await guardrail.async_pre_call_hook({}, None, payload, "completion")


@pytest.mark.asyncio
async def test_presidio_anonymizes_pii_in_non_content_fields(executor):
    guardrail = PresidioGuardrail(
        executor=executor,
        anonymize=True,
        entities=["EMAIL_ADDRESS", "US_SSN"],
    )
    payload = {
        "messages": [
            {
                "role": "user",
                "content": "safe content",
                "name": "alice@example.com",
                "function_call": {"name": "send", "arguments": '{"ssn":"123-45-6789"}'},
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
async def test_presidio_blocks_when_pii_only_in_non_content_fields(executor):
    guardrail = PresidioGuardrail(
        executor=executor,
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
