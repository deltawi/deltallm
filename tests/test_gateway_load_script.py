from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "measure_gateway_load.py"
_SPEC = importlib.util.spec_from_file_location("measure_gateway_load", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)


@pytest.mark.asyncio
async def test_constant_arrival_separates_arrival_and_drain() -> None:
    async def request(_index: int, _request_id: str):  # noqa: ANN202
        await _MODULE.asyncio.sleep(0.03)
        return _MODULE.RequestResult(status_code=200, bytes_received=10)

    result = await _MODULE.run_constant_arrival(
        rate=100.0,
        duration_seconds=0.1,
        max_in_flight=20,
        request=request,
    )
    summary = _MODULE.summarize(result, target_rate=100.0)

    assert result.target_count == 10
    assert result.scheduled_count == 10
    assert result.generator_dropped_count == 0
    assert summary["client_started_rps"] == pytest.approx(100.0)
    assert summary["completion_count"] == 10
    assert result.drain_window_seconds >= 0


@pytest.mark.asyncio
async def test_constant_arrival_reports_generator_drops() -> None:
    gate = _MODULE.asyncio.Event()

    async def request(_index: int, _request_id: str):  # noqa: ANN202
        await gate.wait()
        return _MODULE.RequestResult(status_code=200)

    task = _MODULE.asyncio.create_task(
        _MODULE.run_constant_arrival(
            rate=100.0,
            duration_seconds=0.05,
            max_in_flight=1,
            request=request,
        )
    )
    await _MODULE.asyncio.sleep(0.06)
    gate.set()
    result = await task

    assert result.target_count == 5
    assert result.scheduled_count == 1
    assert result.generator_dropped_count == 4


def test_write_results_never_contains_api_keys(tmp_path: Path) -> None:
    result = _MODULE.RunResult(
        run_id="run-1",
        started_at="2026-01-01T00:00:00+00:00",
        target_count=0,
        scheduled_count=0,
        generator_dropped_count=0,
        arrival_window_seconds=1.0,
        drain_window_seconds=0.0,
        max_in_flight_observed=0,
        samples=(),
    )
    raw, summary = _MODULE.write_results(
        result,
        _MODULE.summarize(result, target_rate=1.0),
        tmp_path,
    )

    assert "api_key" not in raw.read_text(encoding="utf-8")
    assert "api_key" not in summary.read_text(encoding="utf-8")


def test_nearest_rank_percentiles_are_deterministic() -> None:
    result = _MODULE._percentiles([0.1, 0.2, 0.3, 0.4, 0.5])

    assert result == {"mean": 0.3, "p50": 0.3, "p95": 0.5, "p99": 0.5, "max": 0.5}


async def test_500_rps_keeps_only_live_tasks_and_accounts_every_arrival():
    async def request(_index, _request_id):
        return _MODULE.RequestResult(status_code=200)

    result = await _MODULE.run_constant_arrival(
        rate=500, duration_seconds=0.2, max_in_flight=20, request=request
    )
    assert result.target_count == result.scheduled_count == len(result.samples) == 100
    assert result.generator_dropped_count == 0
    assert result.max_tracked_tasks_observed <= 20
    assert [sample.index for sample in result.samples] == list(range(100))
    assert _MODULE.summarize(result, target_rate=500)["success_count"] == 100


@pytest.mark.parametrize(
    "change",
    [
        {"rate": float("nan")},
        {"rate": float("inf")},
        {"rate": 1001},
        {"duration_seconds": float("inf")},
        {"duration_seconds": 1801},
        {"max_in_flight": 10001},
        {"max_in_flight": True},
        {"target_count": 1000001},
        {"target_count": True},
        {"drain_timeout_seconds": float("nan")},
        {"schedule_offset_seconds": float("nan")},
    ],
)
async def test_invalid_load_budgets_fail_before_a_request(change):
    async def request(_index, _request_id):
        raise AssertionError("invalid load must not start")

    options = dict(rate=500, duration_seconds=0.02, max_in_flight=10, request=request)
    options.update(change)
    with pytest.raises(ValueError):
        await _MODULE.run_constant_arrival(**options)


async def test_drain_deadline_records_cancelled_requests_and_closes_owned_tasks():
    closed = 0

    async def request(_index, _request_id):
        nonlocal closed
        try:
            await _MODULE.asyncio.Event().wait()
        finally:
            closed += 1

    result = await _MODULE.run_constant_arrival(
        rate=500,
        duration_seconds=0.02,
        max_in_flight=3,
        drain_timeout_seconds=0.01,
        request=request,
    )
    assert result.target_count == 10
    assert result.scheduled_count == closed == len(result.samples) == 3
    assert result.generator_dropped_count == 7
    assert {sample.error for sample in result.samples} == {"generator_drain_timeout"}
    assert _MODULE.summarize(result, target_rate=500)["success_count"] == 0


async def test_generator_cancellation_closes_owned_request_tasks():
    opened, closed = _MODULE.asyncio.Event(), _MODULE.asyncio.Event()

    async def request(_index, _request_id):
        opened.set()
        try:
            await _MODULE.asyncio.Event().wait()
        finally:
            closed.set()

    task = _MODULE.asyncio.create_task(
        _MODULE.run_constant_arrival(
            rate=500,
            duration_seconds=0.1,
            max_in_flight=1,
            request=request,
        )
    )
    await opened.wait()
    task.cancel()
    with pytest.raises(_MODULE.asyncio.CancelledError):
        await task
    assert closed.is_set()


async def test_invalid_200_response_is_not_a_success(tmp_path):
    async def request(_index, _request_id):
        return _MODULE.RequestResult(status_code=200, error="invalid_response")

    result = await _MODULE.run_constant_arrival(
        rate=500,
        duration_seconds=0.002,
        max_in_flight=1,
        request=request,
    )
    summary = _MODULE.summarize(result, target_rate=500)
    assert summary["success_count"] == 0
    assert summary["status_counts"] == {"200": 1}
    assert summary["error_counts"] == {"invalid_response": 1}
    raw, report = _MODULE.write_results(result, summary, tmp_path, compress=True)
    assert _MODULE.read_results(raw, report) == result


def test_error_body_does_not_leak_credentials_or_internal_message():
    assert (
        _MODULE.classify_response(
            503,
            b'{"error":{"code":"provider-secret","message":"sk-secret"}}',
            expect_fixed_one_token=True,
        )
        == "unclassified_http_error"
    )
    assert (
        _MODULE.classify_response(
            503,
            b'{"error":{"code":"gateway_draining","message":"sk-secret"}}',
            expect_fixed_one_token=True,
        )
        == "gateway_draining"
    )
