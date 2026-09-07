from __future__ import annotations

from enum import StrEnum
from typing import Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.route_policy_contract import MAX_SELECTOR_INPUT_CHARS

SELECTOR_PROMPT_VERSION = 1
SELECTOR_OUTPUT_TOKENS = 64
SELECTOR_OUTPUT_BYTES = 256
SELECTOR_MAX_JOINERS = 8
MAX_OBSERVED_COUNT = 2**63 - 1


class FrozenContract(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", hide_input_in_errors=True)


class SelectorCause(StrEnum):
    CLASSIFIED = "classified"
    INPUT_UNAVAILABLE = "input_unavailable"
    INPUT_BUDGET_INSUFFICIENT = "input_budget_insufficient"
    INVALID_INPUT = "invalid_input"
    SELECTOR_TIMEOUT = "selector_timeout"
    PROVIDER_ERROR = "provider_error"
    TRANSPORT_ERROR = "transport_error"
    INVALID_RESPONSE = "invalid_response"
    RESPONSE_TOO_LARGE = "response_too_large"
    RESPONSE_ENCODING = "response_encoding"
    INVALID_JSON = "invalid_json"
    UNKNOWN_LANE = "unknown_lane"
    OUTPUT_TOO_LARGE = "output_too_large"


class SelectorPolicyIdentity(FrozenContract):
    semantics_version: Literal[3] = 3
    policy_version: int | None = Field(default=None, ge=1, le=MAX_OBSERVED_COUNT)
    fingerprint: str = Field(pattern=r"^route-policy-v1:[0-9a-f]{64}$")
    prompt_version: Literal[1] = SELECTOR_PROMPT_VERSION


class SelectorSnippet(FrozenContract):
    role: Literal["system", "user", "assistant"]
    text: str = Field(max_length=MAX_SELECTOR_INPUT_CHARS, repr=False)


class SelectorRequestFeatures(FrozenContract):
    newest_user: str | None = Field(default=None, max_length=MAX_SELECTOR_INPUT_CHARS, repr=False)
    system_context: str = Field(default="", max_length=1024, repr=False)
    recent_context: tuple[SelectorSnippet, ...] = Field(default=(), max_length=4, repr=False)
    token_estimate: int = Field(ge=0, le=MAX_OBSERVED_COUNT)
    tool_count: int = Field(ge=0, le=MAX_OBSERVED_COUNT)
    response_format: Literal["text", "json_object", "json_schema"] = "text"
    modalities: frozenset[Literal["text", "image", "audio", "file", "unknown"]] = frozenset()
    truncated: bool = False
    features_incomplete: bool = False

    @model_validator(mode="after")
    def bound_recent_context(self) -> SelectorRequestFeatures:
        if sum(len(snippet.text) for snippet in self.recent_context) > 2048:
            raise ValueError("selector recent context exceeds its bound")
        return self


class SelectorPrompt(FrozenContract):
    system: str = Field(max_length=MAX_SELECTOR_INPUT_CHARS, repr=False)
    user: str = Field(max_length=MAX_SELECTOR_INPUT_CHARS, repr=False)

    @model_validator(mode="after")
    def bound_total_content(self) -> SelectorPrompt:
        if len(self.system) + len(self.user) > MAX_SELECTOR_INPUT_CHARS:
            raise ValueError("selector prompt exceeds its bound")
        return self


class ReportedSelectorUsage(FrozenContract):
    kind: Literal["reported"] = "reported"
    prompt_tokens: int = Field(ge=0, le=MAX_OBSERVED_COUNT)
    completion_tokens: int = Field(ge=0, le=MAX_OBSERVED_COUNT)
    total_tokens: int = Field(ge=0, le=MAX_OBSERVED_COUNT)


class UnknownSelectorUsage(FrozenContract):
    kind: Literal["unknown"] = "unknown"


class UnattemptedSelectorUsage(FrozenContract):
    kind: Literal["not_attempted"] = "not_attempted"


SelectorUsage: TypeAlias = ReportedSelectorUsage | UnknownSelectorUsage | UnattemptedSelectorUsage


class SelectorHopSuccess(FrozenContract):
    kind: Literal["success"] = "success"
    text: str = Field(max_length=SELECTOR_OUTPUT_BYTES, repr=False)
    usage: SelectorUsage

    @model_validator(mode="after")
    def bound_text(self) -> SelectorHopSuccess:
        if len(self.text.encode("utf-8")) > SELECTOR_OUTPUT_BYTES:
            raise ValueError("selector output exceeds its bound")
        return self


class SelectorHopFailure(FrozenContract):
    kind: Literal["failure"] = "failure"
    cause: SelectorCause
    usage: SelectorUsage

    @model_validator(mode="after")
    def reject_success_cause(self) -> SelectorHopFailure:
        if self.cause is SelectorCause.CLASSIFIED:
            raise ValueError("a failed selector hop cannot be classified")
        return self


SelectorHopOutcome: TypeAlias = SelectorHopSuccess | SelectorHopFailure


class SelectorDecision(FrozenContract):
    lane: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    minimum_rank: int = Field(ge=0, lt=8)
    cause: SelectorCause
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    policy_identity: SelectorPolicyIdentity
    usage: SelectorUsage

    @property
    def used_default(self) -> bool:
        return self.cause is not SelectorCause.CLASSIFIED


class SelectorModelHop(Protocol):
    async def invoke(
        self,
        *,
        deployment_id: str,
        prompt: SelectorPrompt,
        expires_at: float,
    ) -> SelectorHopOutcome:
        """Execute one concrete-deployment hop within the supplied monotonic deadline."""
        ...


class SelectorInvariantError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Invalid selector invocation state")


class SelectorJoinLimitError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Selector operation join limit exceeded")


class SelectorOperationAbortedError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Selector operation was aborted")
