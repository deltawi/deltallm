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
