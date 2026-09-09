from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from src.models.requests import ChatCompletionRequest
from src.chat_capabilities import ChatRoutingCapabilities
from src.router.candidates import invalidate_candidate_plan_cache

_ELIGIBILITY_KEY = "_deltallm_selector_capability_eligibility"


@dataclass(frozen=True, slots=True)
class SelectorCapabilityEligibility:
    deployment_ids: frozenset[str]
    qualified_ids: frozenset[str]


def set_selector_capability_eligibility(
    context: dict[str, object],
    payload: ChatCompletionRequest,
    capabilities: Mapping[str, ChatRoutingCapabilities],
    *,
    requirements: frozenset[str] | None = None,
) -> None:
    required = chat_requirements(payload) if requirements is None else requirements
    eligible = {
        deployment_id
        for deployment_id, capability in capabilities.items()
        if required.issubset(key for key, supported in capability.model_dump().items() if supported)
    }
    previous = context.get(_ELIGIBILITY_KEY)
    qualified_ids = frozenset(capabilities)
    if isinstance(previous, SelectorCapabilityEligibility):
        eligible.update(previous.deployment_ids - qualified_ids)
        qualified_ids |= previous.qualified_ids
    context[_ELIGIBILITY_KEY] = SelectorCapabilityEligibility(frozenset(eligible), qualified_ids)
    invalidate_candidate_plan_cache(context)


def clear_selector_capability_eligibility(context: dict[str, object]) -> None:
    context.pop(_ELIGIBILITY_KEY, None)
    invalidate_candidate_plan_cache(context)


def capability_allows(context: dict[str, object], deployment_id: str) -> bool:
    eligibility = context.get(_ELIGIBILITY_KEY)
    # The planner can still be used independently, but activation's application
    # owner always supplies the qualified requirements before executable planning.
    return eligibility is None or (
        isinstance(eligibility, SelectorCapabilityEligibility)
        and (
            deployment_id not in eligibility.qualified_ids
            or deployment_id in eligibility.deployment_ids
        )
    )


def chat_requirements(payload: ChatCompletionRequest) -> frozenset[str]:
    """Inspect the normalized request, not the intentionally truncated selector prompt.

    One linear pass over an already admitted body, no content copies or I/O. The
    execution owner reuses this immutable result across groups until payload changes.
    """
    required: set[str] = set()
    if payload.stream:
        required.add("streaming")
    if payload.tools:
        required.add("tools")
    if payload.n is not None and payload.n > 1:
        required.add("multiple_choices")
    if payload.response_format is not None and payload.response_format.type != "text":
        required.add(payload.response_format.type)
    modalities = {
        "image_url": "image",
        "input_image": "image",
        "input_audio": "audio",
        "audio": "audio",
        "file": "file",
        "input_file": "file",
    }
    for message in payload.messages:
        if message.role == "tool" or message.tool_calls:
            required.add("tools")
        if isinstance(message.content, list):
            for block in message.content:
                kind = block.get("type")
                if kind not in ("text", "input_text"):
                    required.add(
                        modalities.get(kind, "unknown") if isinstance(kind, str) else "unknown"
                    )
    return frozenset(required)
