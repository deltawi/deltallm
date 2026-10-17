from __future__ import annotations

import pytest

from src.route_group_config import RouteGroupConfig
from src.router import (
    RedisStateBackend,
    Router,
    RouterConfig,
    RoutingStrategy,
    build_deployment_registry,
    build_route_group_policies,
)
from src.router.candidates import RouteCandidateLane
from src.router.policy_validation import merge_policy_document_for_write, validate_route_policy
from src.router.quality import DeploymentQualityTracker, QualityOutcome
from src.router.quality.policy import (
    QUALITY_PRESETS,
    LowQualityReason,
    demote_low_quality,
    low_quality_deployments,
    parse_quality_routing_policy,
    validate_quality_policy_block,
)
from src.router.quality.stats import BUCKET_SECONDS, snapshot_from_buckets
from src.router.router import Deployment
from src.router.selection.planning import CandidateOrdering


class Clock:
    def __init__(self) -> None:
        self.now = 10_000.0 * BUCKET_SECONDS

    def __call__(self) -> float:
        return self.now


def _record(
    tracker: DeploymentQualityTracker,
    deployment_id: str,
    *,
    ok: int = 0,
    errors: int = 0,
    first_token_ms: float | None = None,
) -> None:
    for _ in range(ok):
        tracker.record(deployment_id, QualityOutcome.OK, first_token_ms=first_token_ms)
    for _ in range(errors):
        tracker.record(deployment_id, QualityOutcome.PROVIDER_ERROR)


def _deployment(deployment_id: str) -> Deployment:
    return Deployment(deployment_id=deployment_id, model_name="m", deltallm_params={})


def test_tracker_totals_and_derived_rates() -> None:
    tracker = DeploymentQualityTracker(clock=Clock())
    tracker.record("a", QualityOutcome.OK, first_token_ms=200, output_tokens=50, output_ms=500)
    tracker.record("a", QualityOutcome.EMPTY_ANSWER)
    tracker.record("a", QualityOutcome.BAD_TOOL_CALL)
    tracker.record("a", None, first_token_ms=400)

    quality = tracker.local_snapshot().get("a")

    assert quality is not None
    assert (quality.calls, quality.failed_calls) == (3, 2)
    assert quality.failure_rate == pytest.approx(2 / 3)
    assert quality.avg_first_token_ms == 300
    assert quality.output_tokens_per_second == 100


def test_tracker_drops_buckets_outside_the_window() -> None:
    clock = Clock()
    tracker = DeploymentQualityTracker(clock=clock)
    _record(tracker, "a", errors=5)
    clock.now += 2 * BUCKET_SECONDS
    _record(tracker, "a", ok=1)
    assert tracker.local_snapshot().get("a").calls == 6

    clock.now += BUCKET_SECONDS
    assert tracker.local_snapshot().get("a").calls == 1


def test_pending_counts_are_taken_once_and_snapshot_waits_for_publish() -> None:
    tracker = DeploymentQualityTracker(clock=Clock())
    _record(tracker, "a", ok=2)

    pending = tracker.take_pending()

    assert [counts.calls for bucket in pending.values() for counts in bucket.values()] == [2]
    assert tracker.take_pending() == {}
    assert tracker.snapshot().get("a") is None
    tracker.publish(tracker.local_snapshot())
    assert tracker.snapshot().get("a").calls == 2


@pytest.mark.parametrize(
    ("sensitivity", "errors", "flagged"),
    [("relaxed", 7, False), ("relaxed", 9, True), ("balanced", 5, True), ("strict", 3, True)],
)
def test_presets_flag_by_failed_call_rate(sensitivity: str, errors: int, flagged: bool) -> None:
    tracker = DeploymentQualityTracker(clock=Clock())
    _record(tracker, "bad", ok=20 - errors, errors=errors)
    _record(tracker, "good", ok=20)

    result = low_quality_deployments(
        ["bad", "good"], tracker.local_snapshot(), QUALITY_PRESETS[sensitivity]
    )

    assert result == ({"bad": LowQualityReason.FAILED_CALLS} if flagged else {})


def test_few_calls_are_never_flagged() -> None:
    tracker = DeploymentQualityTracker(clock=Clock())
    _record(tracker, "bad", errors=19)
    _record(tracker, "good", ok=20)
    assert (
        low_quality_deployments(
            ["bad", "good"], tracker.local_snapshot(), QUALITY_PRESETS["balanced"]
        )
        == {}
    )


def test_strict_flags_slow_first_token_against_lane_peers() -> None:
    tracker = DeploymentQualityTracker(clock=Clock())
    _record(tracker, "slow", ok=20, first_token_ms=900)
    _record(tracker, "fast", ok=20, first_token_ms=300)
    snapshot = tracker.local_snapshot()

    assert low_quality_deployments(["slow", "fast"], snapshot, QUALITY_PRESETS["strict"]) == {
        "slow": LowQualityReason.SLOW_FIRST_TOKEN
    }
    assert low_quality_deployments(["slow", "fast"], snapshot, QUALITY_PRESETS["balanced"]) == {}


def test_every_deployment_flagged_means_no_change() -> None:
    tracker = DeploymentQualityTracker(clock=Clock())
    _record(tracker, "a", errors=20)
    _record(tracker, "b", errors=20)
    assert (
        low_quality_deployments(["a", "b"], tracker.local_snapshot(), QUALITY_PRESETS["balanced"])
        == {}
    )


def _bad_first_snapshot():
    tracker = DeploymentQualityTracker(clock=Clock())
    _record(tracker, "bad", ok=10, errors=10)
    _record(tracker, "good", ok=20)
    _record(tracker, "other", ok=20)
    return tracker.local_snapshot()


def test_demote_moves_flagged_deployment_last_and_keeps_others_in_order() -> None:
    bad, good, other = _deployment("bad"), _deployment("good"), _deployment("other")
    ordering = CandidateOrdering((bad, other, good))

    demoted = demote_low_quality(ordering, QUALITY_PRESETS["balanced"], _bad_first_snapshot())

    assert [d.deployment_id for d in demoted.deployments] == ["other", "good", "bad"]
    assert demote_low_quality(ordering, None, _bad_first_snapshot()) is ordering


def test_demote_reorders_inside_each_selector_lane() -> None:
    bad, good, other = _deployment("bad"), _deployment("good"), _deployment("other")
    lanes = (
        RouteCandidateLane("economy", 0, (bad, good)),
        RouteCandidateLane("quality", 1, (other,)),
    )
    ordering = CandidateOrdering((bad, good, other), lanes, minimum_rank=0)

    demoted = demote_low_quality(ordering, QUALITY_PRESETS["balanced"], _bad_first_snapshot())

    assert [d.deployment_id for d in demoted.deployments] == ["good", "bad", "other"]
    assert [d.deployment_id for d in demoted.lanes[0].deployments] == ["good", "bad"]

    pending = CandidateOrdering((), lanes, rejection_reason="selector_decision_required")
    assert (
        demote_low_quality(pending, QUALITY_PRESETS["balanced"], _bad_first_snapshot()).deployments
        == ()
    )


def test_quality_policy_parsing_and_validation() -> None:
    assert parse_quality_routing_policy(None) is None
    assert parse_quality_routing_policy({"deprioritize": False, "sensitivity": "strict"}) is None
    assert parse_quality_routing_policy({"deprioritize": True}) is QUALITY_PRESETS["balanced"]
    assert validate_quality_policy_block(None) is None
    assert validate_quality_policy_block({"deprioritize": True, "sensitivity": "strict"}) == {
        "deprioritize": True,
        "sensitivity": "strict",
    }
    for invalid in ({"deprioritize": "yes"}, {"sensitivity": "loose"}, {"extra": 1}, []):
        with pytest.raises(ValueError, match="quality"):
            validate_quality_policy_block(invalid)


def test_route_policy_documents_keep_quality_until_removed() -> None:
    normalized, _ = validate_route_policy(
        {
            "strategy": "simple-shuffle",
            "members": [{"deployment_id": "a"}],
            "quality": {"deprioritize": True},
        }
    )
    assert normalized["quality"] == {"deprioritize": True, "sensitivity": "balanced"}

    stored = {"strategy": "simple-shuffle", "quality": {"deprioritize": True}}
    assert merge_policy_document_for_write(stored, {"strategy": "weighted"})["quality"] == {
        "deprioritize": True
    }
    assert "quality" not in merge_policy_document_for_write(stored, {"quality": None})


def test_file_config_accepts_quality_on_selector_free_groups() -> None:
    group = RouteGroupConfig.model_validate(
        {"key": "g", "quality": {"deprioritize": True, "sensitivity": "relaxed"}}
    )
    assert group.quality is not None and group.quality.sensitivity == "relaxed"


def _quality_router(strategy: str, tracker: DeploymentQualityTracker) -> Router:
    route_groups = [
        {
            "key": "chat-group",
            "mode": "chat",
            "strategy": strategy,
            "quality": {"deprioritize": True, "sensitivity": "balanced"},
            "members": [
                {"deployment_id": "bad", "priority": 0},
                {"deployment_id": "good", "priority": 1},
            ],
        }
    ]
    registry = build_deployment_registry(
        {
            "backing": [
                {"deployment_id": "bad", "deltallm_params": {"model": "openai/backing"}},
                {"deployment_id": "good", "deltallm_params": {"model": "openai/backing"}},
            ]
        },
        route_groups=route_groups,
    )
    return Router(
        strategy=RoutingStrategy.SIMPLE_SHUFFLE,
        state_backend=RedisStateBackend(redis=None),
        config=RouterConfig(route_group_policies=build_route_group_policies(route_groups)),
        deployment_registry=registry,
        quality=tracker,
    )


def _bad_and_good_tracker() -> DeploymentQualityTracker:
    tracker = DeploymentQualityTracker(clock=Clock())
    _record(tracker, "bad", ok=10, errors=10)
    _record(tracker, "good", ok=20)
    return tracker


@pytest.mark.asyncio
async def test_router_plan_sends_low_quality_primary_last() -> None:
    tracker = _bad_and_good_tracker()
    router = _quality_router("priority-based-routing", tracker)

    before = await router.select_deployment("chat-group", {})
    tracker.publish(tracker.local_snapshot())
    after = await router.select_deployment("chat-group", {})

    assert (before.deployment_id, after.deployment_id) == ("bad", "good")


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["simple-shuffle", "weighted"])
async def test_router_plan_sends_low_quality_member_last_for_random_strategies(
    strategy: str,
) -> None:
    tracker = _bad_and_good_tracker()
    tracker.publish(tracker.local_snapshot())
    router = _quality_router(strategy, tracker)

    picks = {(await router.select_deployment("chat-group", {})).deployment_id for _ in range(20)}

    assert picks == {"good"}


def test_snapshot_from_buckets_adds_every_bucket() -> None:
    tracker = DeploymentQualityTracker(clock=Clock())
    _record(tracker, "a", ok=1)
    pending = tracker.take_pending()
    merged = snapshot_from_buckets([*pending.values(), *pending.values()], source="cluster")
    assert merged.get("a").calls == 2
    assert merged.source == "cluster"
