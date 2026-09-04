from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class OpenAIStreamDeltaField(StrEnum):
    """Known OpenAI-compatible delta fields with routing significance."""

    CONTENT = "content"
    FUNCTION_CALL = "function_call"
    REASONING = "reasoning"
    REASONING_CONTENT = "reasoning_content"
    REASONING_DETAILS = "reasoning_details"
    REFUSAL = "refusal"
    ROLE = "role"
    TOOL_CALLS = "tool_calls"


OPENAI_STREAM_OUTPUT_DELTA_FIELDS = frozenset(
    {
        OpenAIStreamDeltaField.CONTENT.value,
        OpenAIStreamDeltaField.FUNCTION_CALL.value,
        OpenAIStreamDeltaField.REASONING.value,
        OpenAIStreamDeltaField.REASONING_CONTENT.value,
        OpenAIStreamDeltaField.REASONING_DETAILS.value,
        OpenAIStreamDeltaField.REFUSAL.value,
        OpenAIStreamDeltaField.TOOL_CALLS.value,
    }
)
OPENAI_STREAM_KNOWN_DELTA_FIELDS = OPENAI_STREAM_OUTPUT_DELTA_FIELDS | {
    OpenAIStreamDeltaField.ROLE.value
}


@dataclass(frozen=True, slots=True)
class OpenAIStreamChoiceInspection:
    has_output: bool = False
    has_unknown_output_candidate: bool = False


def inspect_openai_stream_choices(
    choices: list[object],
) -> OpenAIStreamChoiceInspection:
    """Classify bounded delta fields without retaining provider-owned values."""

    has_output = False
    has_unknown_output_candidate = False
    for value in choices:
        if not isinstance(value, Mapping):
            continue
        delta = value.get("delta")
        if not isinstance(delta, Mapping):
            continue
        for key, output in delta.items():
            if output in (None, "", [], {}):
                continue
            if key in OPENAI_STREAM_OUTPUT_DELTA_FIELDS:
                has_output = True
            elif key not in OPENAI_STREAM_KNOWN_DELTA_FIELDS:
                has_unknown_output_candidate = True

    return OpenAIStreamChoiceInspection(
        has_output=has_output,
        has_unknown_output_candidate=has_unknown_output_candidate,
    )
