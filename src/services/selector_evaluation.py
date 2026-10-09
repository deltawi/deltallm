"""Explicit fixture replay, never a provider execution or publication gate."""

from collections import Counter
from decimal import Decimal
import hashlib
import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.billing.routing_costs import (
    RoutingCostAggregate,
    RoutingCostObservation,
    aggregate_routing_costs,
)
from src.route_policy_contract import LLMTierSelectorPolicy
from src.router.selection.contracts import SelectorCause
from src.router.selection.parser import parse_selector_output

MAX_EVALUATION_BYTES = 262_144
MAX_EVALUATION_SAMPLES = 100
LaneId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")]
ExactCostText = Annotated[str, Field(pattern=r"^[0-9]{1,20}(\.[0-9]{1,18})?$", max_length=39)]


class EvaluationContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class EvaluationCosts(EvaluationContract):
    selector_provider_cost: ExactCostText | None = None
    selector_customer_charge: ExactCostText | None = None
    answer_provider_cost: ExactCostText | None = None
    answer_customer_charge: ExactCostText | None = None
    baseline_answer_provider_cost: ExactCostText | None = None
    measurable_penalty: ExactCostText | None = None

    def observation(self) -> RoutingCostObservation:
        return RoutingCostObservation(
            **{
                field: Decimal(value) if value is not None else None
                for field, value in self.model_dump().items()
            }
        )


class SelectorEvaluationSample(EvaluationContract):
    expected_lane: LaneId
    prompt: str | None = Field(default=None, max_length=32768, repr=False)
    output: str | None = Field(default=None, max_length=1024, repr=False)
    failure: Literal["selector_timeout", "provider_error", "capacity_denied"] | None = None
    latency_ms: float | None = Field(default=None, ge=0, le=300_000, allow_inf_nan=False)
    answer_quality_score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    costs: EvaluationCosts = Field(default_factory=EvaluationCosts)

    @model_validator(mode="after")
    def one_outcome(self) -> "SelectorEvaluationSample":
        if (self.output is None) == (self.failure is None):
            raise ValueError("Each sample needs exactly one output or failure")
        return self


class SelectorEvaluationRequest(EvaluationContract):
    selector: LLMTierSelectorPolicy
    samples: tuple[SelectorEvaluationSample, ...] = Field(
        min_length=1, max_length=MAX_EVALUATION_SAMPLES, repr=False
    )

    @model_validator(mode="after")
    def bounded_fixture(self) -> "SelectorEvaluationRequest":
        if len(self.model_dump_json().encode("utf-8")) > MAX_EVALUATION_BYTES:
            raise ValueError("Evaluation fixtures exceed the size limit")
        allowed = {lane.id for lane in self.selector.lanes}
        if any(sample.expected_lane not in allowed for sample in self.samples):
            raise ValueError("Expected lanes must belong to this selector")
        return self


class LaneEvaluation(EvaluationContract):
    lane: str
    expected: int
    selected: int
    correct: int
    precision: float | None
    recall: float | None


class LaneConfusion(EvaluationContract):
    expected_lane: str
    selected_lane: str
    count: int


class SelectorEvaluationReport(EvaluationContract):
    basis: Literal["supplied_fixture_replay"] = "supplied_fixture_replay"
    selector_fingerprint: str
    sample_count: int
    correct_count: int
    default_count: int
    default_rate: float
    failure_counts: dict[str, int]
    lanes: tuple[LaneEvaluation, ...]
    confusion: tuple[LaneConfusion, ...]
    latency_sample_count: int
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    answer_quality_sample_count: int
    answer_quality_mean: float | None
    costs: RoutingCostAggregate
    warnings: tuple[str, ...] = (
        "Fixture replay only: no provider calls, charges or publication changes.",
        "Scores, timings and costs are supplied evidence, not independently measured results.",
        "Lane agreement is not answer quality. Missing evidence remains unavailable.",
        "This report does not validate deployment eligibility for publication.",
    )


def evaluate_selector(request: SelectorEvaluationRequest) -> SelectorEvaluationReport:
    """One bounded deterministic pass with the runtime's strict output parser."""
    confusion: Counter[tuple[str, str]] = Counter()
    failures: Counter[str] = Counter()
    latencies: list[float] = []
    quality: list[float] = []
    for sample in request.samples:
        outcome = (
            SelectorCause(sample.failure)
            if sample.failure is not None
            else parse_selector_output(sample.output, request.selector)
        )
        if isinstance(outcome, SelectorCause):
            selected = request.selector.default_lane
            failures[outcome.value] += 1
        else:
            selected = outcome.id
        confusion[(sample.expected_lane, selected)] += 1
        if sample.latency_ms is not None:
            latencies.append(sample.latency_ms)
        if sample.answer_quality_score is not None:
            quality.append(sample.answer_quality_score)
    lanes = []
    for lane in request.selector.lanes:
        expected = sum(count for (label, _), count in confusion.items() if label == lane.id)
        selected = sum(count for (_, label), count in confusion.items() if label == lane.id)
        correct = confusion[(lane.id, lane.id)]
        lanes.append(
            LaneEvaluation(
                lane=lane.id,
                expected=expected,
                selected=selected,
                correct=correct,
                precision=correct / selected if selected else None,
                recall=correct / expected if expected else None,
            )
        )
    latencies.sort()
    return SelectorEvaluationReport(
        selector_fingerprint=hashlib.sha256(
            request.selector.model_dump_json().encode()
        ).hexdigest(),
        sample_count=len(request.samples),
        correct_count=sum(lane.correct for lane in lanes),
        default_count=sum(failures.values()),
        default_rate=sum(failures.values()) / len(request.samples),
        failure_counts=dict(sorted(failures.items())),
        lanes=tuple(lanes),
        confusion=tuple(
            LaneConfusion(expected_lane=a, selected_lane=b, count=count)
            for (a, b), count in sorted(confusion.items())
        ),
        latency_sample_count=len(latencies),
        latency_p50_ms=latencies[math.ceil(len(latencies) * 0.5) - 1] if latencies else None,
        latency_p95_ms=latencies[math.ceil(len(latencies) * 0.95) - 1] if latencies else None,
        answer_quality_sample_count=len(quality),
        answer_quality_mean=sum(quality) / len(quality) if quality else None,
        costs=aggregate_routing_costs(
            tuple(sample.costs.observation() for sample in request.samples)
        ),
    )
