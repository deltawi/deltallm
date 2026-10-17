from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.models.route_group_quality import (
    RouteGroupMemberQualityResponse,
    RouteGroupQualityResponse,
)
from src.router.group_policy import RouteGroupPolicy
from src.router.quality.policy import QualityRoutingPolicy, low_quality_deployments
from src.router.quality.stats import WINDOW_SECONDS, QualitySnapshot


def build_route_group_quality(
    *,
    group_key: str,
    runtime_group: Mapping[str, Any] | None,
    policy: RouteGroupPolicy | None,
    snapshot: QualitySnapshot | None,
) -> RouteGroupQualityResponse:
    """Project the live quality window onto one route group's members.

    "Sent last" uses the same lane grouping and rule as routing, so the table
    shows what the router does with the published policy.
    """

    quality_policy = policy.quality if policy is not None else None
    lanes = _member_lanes(runtime_group)
    if snapshot is None:
        return RouteGroupQualityResponse(
            group_key=group_key,
            source="unavailable",
            window_seconds=WINDOW_SECONDS,
            deprioritize=quality_policy is not None,
            sensitivity=quality_policy.sensitivity if quality_policy is not None else None,
            members=[],
        )
    sent_last = _sent_last(lanes, snapshot, quality_policy)
    members: list[RouteGroupMemberQualityResponse] = []
    for deployment_id in lanes:
        quality = snapshot.get(deployment_id)
        if quality is None:
            continue
        members.append(
            RouteGroupMemberQualityResponse(
                deployment_id=deployment_id,
                calls=quality.calls,
                failed_calls=quality.failed_calls,
                provider_errors=quality.provider_errors,
                empty_answers=quality.empty_answers,
                bad_tool_calls=quality.bad_tool_calls,
                bad_json=quality.bad_json,
                failure_rate=quality.failure_rate,
                avg_first_token_ms=quality.avg_first_token_ms,
                output_tokens_per_second=quality.output_tokens_per_second,
                sent_last_reason=sent_last.get(deployment_id),
            )
        )
    return RouteGroupQualityResponse(
        group_key=group_key,
        source=snapshot.source,
        window_seconds=snapshot.window_seconds,
        deprioritize=quality_policy is not None,
        sensitivity=quality_policy.sensitivity if quality_policy is not None else None,
        members=members,
    )


def _member_lanes(runtime_group: Mapping[str, Any] | None) -> dict[str, str | None]:
    raw_members = runtime_group.get("members") if runtime_group is not None else None
    lanes: dict[str, str | None] = {}
    for member in raw_members if isinstance(raw_members, Sequence) else ():
        if not isinstance(member, Mapping) or not member.get("enabled", True):
            continue
        deployment_id = str(member.get("deployment_id") or "").strip()
        if deployment_id:
            lane = member.get("lane")
            lanes[deployment_id] = str(lane) if lane is not None else None
    return lanes


def _sent_last(
    lanes: Mapping[str, str | None],
    snapshot: QualitySnapshot,
    policy: QualityRoutingPolicy | None,
) -> dict[str, str]:
    if policy is None:
        return {}
    by_lane: dict[str | None, list[str]] = {}
    for deployment_id, lane in lanes.items():
        by_lane.setdefault(lane, []).append(deployment_id)
    flagged: dict[str, str] = {}
    for deployment_ids in by_lane.values():
        if len(deployment_ids) < 2:
            continue
        for deployment_id, reason in low_quality_deployments(
            deployment_ids, snapshot, policy
        ).items():
            flagged[deployment_id] = reason.value
    return flagged
