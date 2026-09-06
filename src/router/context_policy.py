from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from math import inf
from typing import TYPE_CHECKING, Any, Literal, cast

from src.models.errors import FailureClassification, InvalidRequestError
from src.providers.resolution import resolve_provider_required_chat_output_tokens
from src.rate_limit_policy import estimate_tokens
from src.router.candidates import invalidate_candidate_plan_cache

if TYPE_CHECKING:
    from src.router.router import Deployment


CONTEXT_TOKEN_DEMAND_KEY = "context_token_demand"
CONTEXT_ROUTING_METRICS_ENABLED_KEY = "context_routing_metrics_enabled"
_CONTEXT_ROUTING_POLICY_KEY = "_deltallm_context_routing_policy"

ContextRoutingMode = Literal["eligible-only", "smallest-sufficient"]
UnknownCapacityBehavior = Literal["allow", "exclude"]


@dataclass(frozen=True, slots=True)
class ContextRoutingPolicy:
    mode: ContextRoutingMode = "eligible-only"
    unknown_capacity: UnknownCapacityBehavior = "allow"
    default_output_tokens: int = 1024
    safety_margin_tokens: int = 256


def set_request_context_routing_policy(
    request_context: dict[str, Any], policy: ContextRoutingPolicy | None
) -> None:
    """Keep the initiating route group's context policy authoritative for failover."""

    if policy is None:
        if _CONTEXT_ROUTING_POLICY_KEY in request_context:
            request_context.pop(_CONTEXT_ROUTING_POLICY_KEY, None)
            invalidate_candidate_plan_cache(request_context)
        return
    if request_context.get(_CONTEXT_ROUTING_POLICY_KEY) == policy:
        return
    request_context[_CONTEXT_ROUTING_POLICY_KEY] = policy
    invalidate_candidate_plan_cache(request_context)


def get_request_context_routing_policy(
    request_context: dict[str, Any],
) -> ContextRoutingPolicy | None:
    value = request_context.get(_CONTEXT_ROUTING_POLICY_KEY)
    return value if isinstance(value, ContextRoutingPolicy) else None


@dataclass(frozen=True, slots=True)
class RequestTokenDemand:
    input_tokens: int
    requested_output_tokens: int | None = None

    def __post_init__(self) -> None:
        if self.input_tokens < 0:
            raise ValueError("input_tokens must be non-negative")
        if self.requested_output_tokens is not None and self.requested_output_tokens < 0:
            raise ValueError("requested_output_tokens must be non-negative")


class ContextFit(StrEnum):
    SUFFICIENT = "sufficient"
    INSUFFICIENT = "insufficient"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ContextEvaluation:
    fit: ContextFit
    routing_capacity: int | None


def set_request_token_demand(request_context: dict[str, Any], demand: RequestTokenDemand) -> None:
    """Attach final request demand and invalidate any plans made for older payload data."""

    if request_context.get(CONTEXT_TOKEN_DEMAND_KEY) == demand:
        return
    request_context[CONTEXT_TOKEN_DEMAND_KEY] = demand
    invalidate_candidate_plan_cache(request_context)


def get_request_token_demand(request_context: dict[str, Any]) -> RequestTokenDemand | None:
    value = request_context.get(CONTEXT_TOKEN_DEMAND_KEY)
    return value if isinstance(value, RequestTokenDemand) else None


def estimate_embedding_context_input_tokens(
    input_value: str | list[str] | list[int] | list[list[int]],
) -> int:
    """Estimate the largest logical embedding input for context routing."""
    if isinstance(input_value, str):
        return estimate_tokens(input_value)
    if not input_value:
        return 0

    first_item = input_value[0]
    if isinstance(first_item, int):
        return len(input_value)
    if isinstance(first_item, str):
        return max(estimate_tokens(item) for item in input_value)
    return max((len(item) for item in input_value), default=0)


def combine_request_token_demands(
    demands: Sequence[RequestTokenDemand],
) -> RequestTokenDemand:
    """Return the strictest demand for requests sharing one provider call."""
    if not demands:
        raise ValueError("at least one request token demand is required")

    requested_output_tokens = demands[0].requested_output_tokens
    if any(demand.requested_output_tokens != requested_output_tokens for demand in demands[1:]):
        raise ValueError("combined requests must use the same output token demand")

    return RequestTokenDemand(
        input_tokens=max(demand.input_tokens for demand in demands),
        requested_output_tokens=requested_output_tokens,
    )


def build_combined_request_context(
    request_contexts: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Build a fresh routing context covering every request in a provider call."""
    if not request_contexts:
        raise ValueError("at least one request context is required")

    demands: list[RequestTokenDemand] = []
    for request_context in request_contexts:
        demand = get_request_token_demand(request_context)
        if demand is None:
            raise ValueError("request context is missing token demand")
        demands.append(demand)

    combined_context = dict(request_contexts[0])
    invalidate_candidate_plan_cache(combined_context)
    combined_context.pop("route_decision", None)
    set_request_token_demand(
        combined_context,
        combine_request_token_demands(demands),
    )
    return combined_context


def context_routing_metrics_enabled(request_context: dict[str, Any]) -> bool:
    return request_context.get(CONTEXT_ROUTING_METRICS_ENABLED_KEY) is not False


def evaluate_context_capacity(
    deployment: Deployment,
    demand: RequestTokenDemand,
    policy: ContextRoutingPolicy,
) -> ContextEvaluation:
    model_info = deployment.model_info
    max_total = _positive_int(model_info.get("max_tokens"))
    max_input = _positive_int(model_info.get("max_input_tokens"))
    max_output = _positive_int(model_info.get("max_output_tokens"))
    requested_output = _requested_output_tokens(deployment, demand, policy)
    input_with_margin = demand.input_tokens + policy.safety_margin_tokens

    if max_input is not None and input_with_margin > max_input:
        return ContextEvaluation(ContextFit.INSUFFICIENT, max_input)
    if max_output is not None and requested_output > max_output:
        return ContextEvaluation(ContextFit.INSUFFICIENT, max_total or max_input)
    if max_total is not None and input_with_margin + requested_output > max_total:
        return ContextEvaluation(ContextFit.INSUFFICIENT, max_total)

    routing_capacity = max_total or max_input
    if routing_capacity is None:
        return ContextEvaluation(ContextFit.UNKNOWN, None)
    return ContextEvaluation(ContextFit.SUFFICIENT, routing_capacity)


def filter_context_candidates(
    deployments: list[Deployment],
    demand: RequestTokenDemand | None,
    policy: ContextRoutingPolicy | None,
) -> tuple[list[Deployment], list[ContextEvaluation]]:
    if policy is None or demand is None:
        return list(deployments), []

    eligible: list[Deployment] = []
    evaluations: list[ContextEvaluation] = []
    for deployment in deployments:
        evaluation = evaluate_context_capacity(deployment, demand, policy)
        evaluations.append(evaluation)
        if evaluation.fit is ContextFit.SUFFICIENT or (
            evaluation.fit is ContextFit.UNKNOWN and policy.unknown_capacity == "allow"
        ):
            eligible.append(deployment)
    return eligible, evaluations


def order_context_candidates(
    deployments: list[Deployment],
    demand: RequestTokenDemand | None,
    policy: ContextRoutingPolicy | None,
) -> list[Deployment]:
    """Prefer the smallest sufficient tier while preserving strategy order within a tier."""

    if policy is None or demand is None or policy.mode != "smallest-sufficient":
        return deployments
    return sorted(
        deployments,
        key=lambda deployment: (
            evaluate_context_capacity(deployment, demand, policy).routing_capacity or inf
        ),
    )


def context_rejection_reason(evaluations: list[ContextEvaluation]) -> str | None:
    if not evaluations:
        return None
    if all(evaluation.fit is ContextFit.INSUFFICIENT for evaluation in evaluations):
        return "context_capacity_exceeded"
    if all(evaluation.fit is not ContextFit.SUFFICIENT for evaluation in evaluations):
        return "context_capacity_unknown"
    return None


def context_capacity_error(model_group: str) -> InvalidRequestError:
    return InvalidRequestError(
        message=f"Request exceeds configured context capacity for model '{model_group}'",
        code="context_length_exceeded",
        affects_deployment_health=False,
        failure_classification=FailureClassification.CONTEXT_WINDOW,
    )


def parse_context_routing_policy(value: Any) -> ContextRoutingPolicy | None:
    if not isinstance(value, dict):
        return None
    raw_mode = value["mode"] if "mode" in value else "eligible-only"
    raw_unknown_capacity = value["unknown_capacity"] if "unknown_capacity" in value else "allow"
    if not isinstance(raw_mode, str) or not isinstance(raw_unknown_capacity, str):
        return None
    mode = raw_mode.strip()
    unknown_capacity = raw_unknown_capacity.strip()
    if mode not in {"eligible-only", "smallest-sufficient"}:
        return None
    if unknown_capacity not in {"allow", "exclude"}:
        return None
    raw_default_output = value.get("default_output_tokens", 1024)
    raw_safety_margin = value.get("safety_margin_tokens", 256)
    default_output_tokens = _non_negative_int(raw_default_output)
    safety_margin_tokens = _non_negative_int(raw_safety_margin)
    if default_output_tokens is None or safety_margin_tokens is None:
        return None
    return ContextRoutingPolicy(
        mode=cast(ContextRoutingMode, mode),
        unknown_capacity=cast(UnknownCapacityBehavior, unknown_capacity),
        default_output_tokens=default_output_tokens,
        safety_margin_tokens=safety_margin_tokens,
    )


def _requested_output_tokens(
    deployment: Deployment,
    demand: RequestTokenDemand,
    policy: ContextRoutingPolicy,
) -> int:
    if demand.requested_output_tokens is not None:
        return demand.requested_output_tokens
    provider_required = resolve_provider_required_chat_output_tokens(
        deployment.deltallm_params,
        demand.requested_output_tokens,
    )
    if provider_required is not None:
        return provider_required
    defaults = deployment.model_info.get("default_params")
    if isinstance(defaults, dict):
        configured = _positive_int(defaults.get("max_tokens"))
        if configured is not None:
            return configured
    return policy.default_output_tokens


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if (
        not isinstance(value, str)
        or not value.strip()
        or not value.strip().isascii()
        or not value.strip().isdigit()
    ):
        return None
    normalized = int(value.strip())
    return normalized if normalized > 0 else None


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if not isinstance(value, str) or not value.strip().isdigit():
        return None
    return int(value.strip())
