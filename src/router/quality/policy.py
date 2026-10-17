from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from statistics import median
from typing import TYPE_CHECKING, Final

from pydantic import ValidationError

from src.metrics import increment_router_quality_demotion
from src.route_group_config import QualityRoutingConfig, QualitySensitivity
from src.router.candidates import RouteCandidateLane
from src.router.quality.stats import DeploymentQuality, QualitySnapshot

if TYPE_CHECKING:
    from src.router.router import Deployment
    from src.router.selection.planning import CandidateOrdering

QUALITY_POLICY_KEY: Final = "quality"


class LowQualityReason(StrEnum):
    FAILED_CALLS = "failed_calls"
    SLOW_FIRST_TOKEN = "slow_first_token"


@dataclass(frozen=True, slots=True)
class QualityRoutingPolicy:
    sensitivity: QualitySensitivity
    max_failure_rate: float
    min_calls: int
    # Strict also sends a deployment last when its average first token is this many
    # times slower than the median of the other deployments in the same lane.
    slow_first_token_factor: float | None = None


QUALITY_PRESETS: Final[Mapping[QualitySensitivity, QualityRoutingPolicy]] = {
    "relaxed": QualityRoutingPolicy("relaxed", max_failure_rate=0.40, min_calls=20),
    "balanced": QualityRoutingPolicy("balanced", max_failure_rate=0.20, min_calls=20),
    "strict": QualityRoutingPolicy(
        "strict", max_failure_rate=0.10, min_calls=20, slow_first_token_factor=2.0
    ),
}


def parse_quality_routing_policy(raw: object) -> QualityRoutingPolicy | None:
    """Return the active preset for a route group, or None when the option is off."""

    if not isinstance(raw, Mapping):
        return None
    config = QualityRoutingConfig.model_validate(raw)
    return QUALITY_PRESETS[config.sensitivity] if config.deprioritize else None


def validate_quality_policy_block(value: object) -> dict[str, object] | None:
    """Validate an authored policy block; None turns the option off."""

    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("quality must be an object or null")
    try:
        return QualityRoutingConfig.model_validate(value).model_dump(mode="json")
    except ValidationError as exc:
        messages = "; ".join(error["msg"] for error in exc.errors(include_input=False))
        raise ValueError(f"quality is invalid: {messages}") from exc


def low_quality_reason(
    quality: DeploymentQuality | None,
    policy: QualityRoutingPolicy,
    *,
    peer_first_token_ms: Sequence[float] = (),
) -> LowQualityReason | None:
    """Decide one deployment; peers are the other deployments in its lane."""

    if quality is None or quality.calls < policy.min_calls:
        return None
    failure_rate = quality.failure_rate
    if failure_rate is not None and failure_rate > policy.max_failure_rate:
        return LowQualityReason.FAILED_CALLS
    factor = policy.slow_first_token_factor
    first_token = quality.avg_first_token_ms
    if (
        factor is not None
        and first_token is not None
        and quality.first_token_samples >= policy.min_calls
        and peer_first_token_ms
        and first_token > factor * median(peer_first_token_ms)
    ):
        return LowQualityReason.SLOW_FIRST_TOKEN
    return None


def low_quality_deployments(
    deployment_ids: Sequence[str],
    snapshot: QualitySnapshot,
    policy: QualityRoutingPolicy,
) -> dict[str, LowQualityReason]:
    """Flag deployments of one lane. Never flags every deployment in the lane."""

    first_token = _first_token_samples(deployment_ids, snapshot, policy)
    flagged: dict[str, LowQualityReason] = {}
    for deployment_id in deployment_ids:
        peers = [value for key, value in first_token.items() if key != deployment_id]
        reason = low_quality_reason(snapshot.get(deployment_id), policy, peer_first_token_ms=peers)
        if reason is not None:
            flagged[deployment_id] = reason
    # Sending every deployment last is the same order as before, so report none.
    return flagged if len(flagged) < len(deployment_ids) else {}


def demote_low_quality(
    ordering: CandidateOrdering,
    policy: QualityRoutingPolicy | None,
    snapshot: QualitySnapshot,
) -> CandidateOrdering:
    """Move flagged deployments to the end of their lane, keeping every other order.

    The step runs after strategy, context and selector ordering. It never removes a
    candidate, so a low-quality deployment still answers when the others fail.
    """

    if policy is None or not snapshot.deployments:
        return ordering
    if not ordering.lanes:
        return replace(ordering, deployments=_demote(ordering.deployments, snapshot, policy))
    lanes = tuple(
        RouteCandidateLane(lane.lane, lane.rank, _demote(lane.deployments, snapshot, policy))
        for lane in ordering.lanes
    )
    # A pending selector plan has lanes but no executable deployments yet.
    deployments = (
        tuple(deployment for lane in lanes for deployment in lane.deployments)
        if ordering.deployments
        else ordering.deployments
    )
    return replace(ordering, deployments=deployments, lanes=lanes)


def _demote(
    deployments: tuple[Deployment, ...],
    snapshot: QualitySnapshot,
    policy: QualityRoutingPolicy,
) -> tuple[Deployment, ...]:
    if len(deployments) < 2:
        return deployments
    flagged = low_quality_deployments(
        [deployment.deployment_id for deployment in deployments], snapshot, policy
    )
    if not flagged:
        return deployments
    for deployment_id in flagged:
        increment_router_quality_demotion(deployment_id=deployment_id)
    kept = [deployment for deployment in deployments if deployment.deployment_id not in flagged]
    last = [deployment for deployment in deployments if deployment.deployment_id in flagged]
    return (*kept, *last)


def _first_token_samples(
    deployment_ids: Sequence[str],
    snapshot: QualitySnapshot,
    policy: QualityRoutingPolicy,
) -> dict[str, float]:
    if policy.slow_first_token_factor is None:
        return {}
    samples: dict[str, float] = {}
    for deployment_id in deployment_ids:
        quality = snapshot.get(deployment_id)
        if quality is None or quality.first_token_samples < policy.min_calls:
            continue
        average = quality.avg_first_token_ms
        if average is not None:
            samples[deployment_id] = average
    return samples
