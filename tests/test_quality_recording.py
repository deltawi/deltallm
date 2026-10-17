from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.chat.quality_recording import _DEFECT_OUTCOMES, AttemptQualityRecorder
from src.chat.response_validation import ResponseValidationError, ResponseValidationReason
from src.models.errors import InvalidRequestError, ServiceUnavailableError, TimeoutError
from src.router.quality import DeploymentQualityTracker, QualityOutcome
from src.router.quality.sync import QualityStatsSync
from src.router.router import Deployment

DEPLOYMENT = Deployment(deployment_id="dep-a", model_name="m", deltallm_params={})
FALLBACK = Deployment(deployment_id="dep-b", model_name="m", deltallm_params={})


def _quality(tracker: DeploymentQualityTracker, deployment_id: str = "dep-a"):  # noqa: ANN202
    return tracker.local_snapshot().get(deployment_id)


async def _answer() -> tuple[dict[str, Any], float]:
    return {"choices": [], "usage": {"completion_tokens": 40}}, 1.0


async def _raise(exc: BaseException) -> None:
    raise exc


def test_every_validation_reason_has_a_quality_outcome() -> None:
    assert set(_DEFECT_OUTCOMES) == set(ResponseValidationReason)


@pytest.mark.asyncio
async def test_successful_attempt_counts_a_call_and_output_tokens() -> None:
    tracker = DeploymentQualityTracker()
    recorder = AttemptQualityRecorder(tracker)

    await recorder.run(recorder.attempt(DEPLOYMENT, _answer()))

    quality = _quality(tracker)
    assert (quality.calls, quality.failed_calls, quality.output_tokens) == (1, 0, 40)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "field"),
    [
        (ResponseValidationError(ResponseValidationReason.EMPTY_RESPONSE), "empty_answers"),
        (ResponseValidationError(ResponseValidationReason.UNKNOWN_TOOL_NAME), "bad_tool_calls"),
        (ResponseValidationError(ResponseValidationReason.INVALID_JSON_OUTPUT), "bad_json"),
        (ServiceUnavailableError(affects_deployment_health=True), "provider_errors"),
        (TimeoutError(), "provider_errors"),
    ],
)
async def test_failed_attempts_count_against_the_deployment(error: Exception, field: str) -> None:
    tracker = DeploymentQualityTracker()
    with pytest.raises(type(error)):
        await AttemptQualityRecorder(tracker).attempt(DEPLOYMENT, _raise(error))
    quality = _quality(tracker)
    assert (quality.calls, getattr(quality, field)) == (1, 1)


@pytest.mark.asyncio
async def test_request_side_errors_are_not_counted() -> None:
    tracker = DeploymentQualityTracker()
    with pytest.raises(InvalidRequestError):
        await AttemptQualityRecorder(tracker).attempt(DEPLOYMENT, _raise(InvalidRequestError()))
    assert _quality(tracker) is None


@pytest.mark.asyncio
async def test_attempt_timeout_counts_once_the_next_attempt_starts() -> None:
    tracker = DeploymentQualityTracker()
    recorder = AttemptQualityRecorder(tracker)

    async def failover() -> object:
        try:
            async with asyncio.timeout(0.01):
                await recorder.attempt(DEPLOYMENT, asyncio.sleep(1))
        except asyncio.TimeoutError:
            pass
        return await recorder.attempt(FALLBACK, _answer())

    await recorder.run(failover())

    assert _quality(tracker).provider_errors == 1
    assert _quality(tracker, "dep-b").calls == 1


@pytest.mark.asyncio
async def test_attempt_timeout_counts_when_failover_gives_up() -> None:
    tracker = DeploymentQualityTracker()
    recorder = AttemptQualityRecorder(tracker)

    async def failover() -> object:
        try:
            async with asyncio.timeout(0.01):
                await recorder.attempt(DEPLOYMENT, asyncio.sleep(1))
        except asyncio.TimeoutError as exc:
            raise TimeoutError() from exc

    with pytest.raises(TimeoutError):
        await recorder.run(failover())
    assert _quality(tracker).provider_errors == 1


@pytest.mark.asyncio
async def test_client_cancellation_is_not_counted() -> None:
    tracker = DeploymentQualityTracker()
    recorder = AttemptQualityRecorder(tracker)
    task = asyncio.create_task(recorder.run(recorder.attempt(DEPLOYMENT, asyncio.sleep(1))))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _quality(tracker) is None


@pytest.mark.asyncio
async def test_stream_records_first_token_then_one_call_at_the_end() -> None:
    tracker = DeploymentQualityTracker()
    recorder = AttemptQualityRecorder(tracker)

    async def first_chunk() -> str:
        return "data: {}"

    await recorder.opened(DEPLOYMENT, first_chunk())
    assert (_quality(tracker).calls, _quality(tracker).first_token_samples) == (0, 1)

    recorder.finished(DEPLOYMENT, provider_failed=False, usage={"completion_tokens": 12})
    recorder.finished(FALLBACK, provider_failed=True, usage=None)

    assert (_quality(tracker).calls, _quality(tracker).output_tokens) == (1, 12)
    assert _quality(tracker, "dep-b").provider_errors == 1


@pytest.mark.asyncio
async def test_recorder_without_tracker_passes_results_through() -> None:
    recorder = AttemptQualityRecorder(None)
    assert await recorder.run(recorder.attempt(DEPLOYMENT, _answer())) == await _answer()


@pytest.mark.asyncio
async def test_sync_without_redis_publishes_local_counts() -> None:
    tracker = DeploymentQualityTracker()
    tracker.record("dep-a", QualityOutcome.OK)

    await QualityStatsSync(tracker, None).run_once()

    snapshot = tracker.snapshot()
    assert snapshot.source == "local"
    assert snapshot.get("dep-a").calls == 1


class _BrokenPipeline:
    def __getattr__(self, name: str):  # noqa: ANN204
        return lambda *args, **kwargs: None

    async def execute(self) -> list[object]:
        raise ConnectionError("redis down")


class _BrokenRedis:
    def pipeline(self, *, transaction: bool) -> _BrokenPipeline:
        del transaction
        return _BrokenPipeline()


@pytest.mark.asyncio
async def test_sync_falls_back_to_local_counts_when_redis_fails() -> None:
    tracker = DeploymentQualityTracker()
    tracker.record("dep-a", QualityOutcome.PROVIDER_ERROR)

    await QualityStatsSync(tracker, _BrokenRedis()).run_once()

    assert tracker.snapshot().source == "local"
    assert tracker.snapshot().get("dep-a").provider_errors == 1


@pytest.mark.asyncio
async def test_sync_loop_starts_and_closes() -> None:
    tracker = DeploymentQualityTracker()
    tracker.record("dep-a", QualityOutcome.OK)
    sync = QualityStatsSync(tracker, None, interval_seconds=60)

    sync.start()
    await asyncio.sleep(0)
    await sync.close()

    assert tracker.snapshot().get("dep-a").calls == 1
