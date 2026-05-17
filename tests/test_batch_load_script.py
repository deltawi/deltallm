from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "measure_embedding_batch_load.py"
_SCRIPT_SPEC = importlib.util.spec_from_file_location("measure_embedding_batch_load", _SCRIPT_PATH)
assert _SCRIPT_SPEC is not None and _SCRIPT_SPEC.loader is not None
_SCRIPT_MODULE = importlib.util.module_from_spec(_SCRIPT_SPEC)
sys.modules[_SCRIPT_SPEC.name] = _SCRIPT_MODULE
_SCRIPT_SPEC.loader.exec_module(_SCRIPT_MODULE)


def test_validate_database_url_rejects_non_local_host_without_force() -> None:
    with pytest.raises(RuntimeError, match="non-local database host"):
        _SCRIPT_MODULE.validate_database_url(
            "postgresql://postgres:postgres@db.internal:5432/deltallm",
            force=False,
        )


def test_validate_database_url_rejects_non_deltallm_database_without_force() -> None:
    with pytest.raises(RuntimeError, match="outside the deltallm local/dev naming pattern"):
        _SCRIPT_MODULE.validate_database_url(
            "postgresql://postgres:postgres@localhost:5432/analytics",
            force=False,
        )


def test_validate_database_url_allows_local_deltallm_database() -> None:
    _SCRIPT_MODULE.validate_database_url(
        "postgresql://postgres:postgres@localhost:5432/deltallm",
        force=False,
    )


def test_scheduler_load_report_emits_rollout_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    samples = [
        SimpleNamespace(
            name="deltallm_batch_scheduler_flow_skips_total",
            labels={"reason": "flow_lock_busy"},
            value=2,
        ),
        SimpleNamespace(
            name="deltallm_batch_scheduler_flow_skips_total",
            labels={"reason": "empty_flow"},
            value=3,
        ),
        SimpleNamespace(
            name="deltallm_batch_scheduler_snapshot_reads_total",
            labels={"kind": "candidate"},
            value=7,
        ),
        SimpleNamespace(
            name="deltallm_batch_scheduler_snapshot_reads_total",
            labels={"kind": "in_flight"},
            value=5,
        ),
        SimpleNamespace(
            name="deltallm_batch_scheduler_snapshot_latency_seconds_bucket",
            labels={"kind": "candidate", "le": "0.1"},
            value=6,
        ),
        SimpleNamespace(
            name="deltallm_batch_scheduler_snapshot_latency_seconds_bucket",
            labels={"kind": "candidate", "le": "0.5"},
            value=7,
        ),
        SimpleNamespace(
            name="deltallm_batch_scheduler_snapshot_latency_seconds_bucket",
            labels={"kind": "candidate", "le": "+Inf"},
            value=7,
        ),
        SimpleNamespace(
            name="deltallm_batch_scheduler_snapshot_latency_seconds_bucket",
            labels={"kind": "in_flight", "le": "0.05"},
            value=5,
        ),
        SimpleNamespace(
            name="deltallm_batch_scheduler_snapshot_latency_seconds_bucket",
            labels={"kind": "in_flight", "le": "+Inf"},
            value=5,
        ),
        SimpleNamespace(name="deltallm_batch_work_claims_total", labels={}, value=20),
        SimpleNamespace(
            name="deltallm_batch_scheduler_decision_latency_seconds_count",
            labels={"mode": "fair_share_v1"},
            value=25,
        ),
        SimpleNamespace(
            name="deltallm_batch_scheduler_decision_latency_seconds_bucket",
            labels={"mode": "fair_share_v1", "le": "0.1"},
            value=10,
        ),
        SimpleNamespace(
            name="deltallm_batch_scheduler_decision_latency_seconds_bucket",
            labels={"mode": "fair_share_v1", "le": "0.5"},
            value=24,
        ),
        SimpleNamespace(
            name="deltallm_batch_scheduler_decision_latency_seconds_bucket",
            labels={"mode": "fair_share_v1", "le": "1.0"},
            value=25,
        ),
        SimpleNamespace(
            name="deltallm_batch_scheduler_decision_latency_seconds_bucket",
            labels={"mode": "fair_share_v1", "le": "+Inf"},
            value=25,
        ),
        SimpleNamespace(name="deltallm_batch_item_reclaims_total", labels={}, value=4),
        SimpleNamespace(name="deltallm_batch_scheduler_fairness_deviation", labels={}, value=0.2),
        SimpleNamespace(
            name="deltallm_batch_duplicate_completion_rejections_total",
            labels={"reason": "not_owned"},
            value=1,
        ),
    ]

    class _Registry:
        def collect(self):
            return [SimpleNamespace(samples=samples)]

    monkeypatch.setattr(_SCRIPT_MODULE, "get_prometheus_registry", lambda: _Registry())

    report = _SCRIPT_MODULE.scheduler_load_report(processed_items=100)

    assert {
        "scheduler_decision_p95_seconds",
        "scheduler_decision_p99_seconds",
        "scheduler_lock_busy_count",
        "scheduler_flow_lock_busy_count",
        "scheduler_legacy_lock_busy_count",
        "scheduler_candidate_snapshot_read_count",
        "scheduler_in_flight_snapshot_read_count",
        "scheduler_candidate_snapshot_p95_seconds",
        "scheduler_in_flight_snapshot_p95_seconds",
        "scheduler_flow_skip_count",
        "scheduler_work_claim_attempt_count",
        "scheduler_decision_count",
        "scheduler_lock_busy_rate",
        "scheduler_lock_busy_share_of_flow_skips",
        "item_reclaim_rate",
        "fairness_deviation_max",
        "duplicate_completion_rejections",
    } <= report.keys()
    assert report["scheduler_decision_p95_seconds"] == 0.5
    assert report["scheduler_decision_p99_seconds"] == 1.0
    assert report["scheduler_lock_busy_count"] == 2
    assert report["scheduler_flow_lock_busy_count"] == 2
    assert report["scheduler_legacy_lock_busy_count"] == 0
    assert report["scheduler_candidate_snapshot_read_count"] == 7
    assert report["scheduler_in_flight_snapshot_read_count"] == 5
    assert report["scheduler_candidate_snapshot_p95_seconds"] == 0.5
    assert report["scheduler_in_flight_snapshot_p95_seconds"] == 0.05
    assert report["scheduler_flow_skip_count"] == 5
    assert report["scheduler_work_claim_attempt_count"] == 20
    assert report["scheduler_decision_count"] == 25
    assert report["scheduler_lock_busy_rate"] == 0.1
    assert report["scheduler_lock_busy_share_of_flow_skips"] == 0.4
    assert report["item_reclaim_rate"] == 0.04
    assert report["fairness_deviation_max"] == 0.2
    assert report["duplicate_completion_rejections"] == 1


def test_scheduler_load_report_subtracts_metric_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    samples = [
        SimpleNamespace(
            name="deltallm_batch_scheduler_flow_skips_total",
            labels={"reason": "flow_lock_busy"},
            value=3,
        ),
        SimpleNamespace(
            name="deltallm_batch_scheduler_snapshot_reads_total",
            labels={"kind": "candidate"},
            value=2,
        ),
        SimpleNamespace(
            name="deltallm_batch_scheduler_snapshot_latency_seconds_bucket",
            labels={"kind": "candidate", "le": "0.1"},
            value=2,
        ),
        SimpleNamespace(
            name="deltallm_batch_scheduler_snapshot_latency_seconds_bucket",
            labels={"kind": "candidate", "le": "0.5"},
            value=2,
        ),
        SimpleNamespace(
            name="deltallm_batch_scheduler_snapshot_latency_seconds_bucket",
            labels={"kind": "candidate", "le": "+Inf"},
            value=2,
        ),
        SimpleNamespace(name="deltallm_batch_work_claims_total", labels={}, value=10),
        SimpleNamespace(
            name="deltallm_batch_scheduler_decision_latency_seconds_count",
            labels={"mode": "fair_share_v1"},
            value=10,
        ),
        SimpleNamespace(
            name="deltallm_batch_scheduler_decision_latency_seconds_bucket",
            labels={"mode": "fair_share_v1", "le": "0.1"},
            value=5,
        ),
        SimpleNamespace(
            name="deltallm_batch_scheduler_decision_latency_seconds_bucket",
            labels={"mode": "fair_share_v1", "le": "0.5"},
            value=10,
        ),
        SimpleNamespace(
            name="deltallm_batch_scheduler_decision_latency_seconds_bucket",
            labels={"mode": "fair_share_v1", "le": "+Inf"},
            value=10,
        ),
    ]

    class _Registry:
        def collect(self):
            return [SimpleNamespace(samples=samples)]

    monkeypatch.setattr(_SCRIPT_MODULE, "get_prometheus_registry", lambda: _Registry())
    baseline = _SCRIPT_MODULE.scheduler_load_metric_snapshot()
    samples[0].value = 5
    samples[1].value = 5
    samples[2].value = 3
    samples[3].value = 5
    samples[4].value = 5
    samples[5].value = 30
    samples[6].value = 35
    samples[7].value = 10
    samples[8].value = 34
    samples[9].value = 35

    report = _SCRIPT_MODULE.scheduler_load_report(processed_items=100, baseline=baseline)

    assert report["scheduler_lock_busy_count"] == 2
    assert report["scheduler_flow_lock_busy_count"] == 2
    assert report["scheduler_candidate_snapshot_read_count"] == 3
    assert report["scheduler_candidate_snapshot_p95_seconds"] == 0.5
    assert report["scheduler_work_claim_attempt_count"] == 20
    assert report["scheduler_decision_count"] == 25
    assert report["scheduler_lock_busy_rate"] == 0.1
    assert report["scheduler_decision_p95_seconds"] == 0.5


@pytest.mark.asyncio
async def test_fair_share_load_config_exercises_work_slice_scheduler() -> None:
    args = SimpleNamespace(
        scheduler_mode="fair_share_v1",
        worker_concurrency=4,
        item_claim_limit=5,
        workers=2,
        model="m1",
        service_tier="standard",
    )

    config = _SCRIPT_MODULE._worker_config_for_scheduler_mode(args, worker_index=1)
    resolver = _SCRIPT_MODULE._model_capacity_resolver_for_scheduler_mode(args)
    selections = await resolver.select_model_groups(max_items=20, max_work_units=80)

    assert config.scheduler_claim_mode == "work_slice"
    assert config.scheduler_mode == "fair_share_v1"
    assert config.worker_id == "load-worker-2"
    assert config.model_capacity_enabled is True
    assert config.tenant_fair_share_enabled is True
    assert config.size_aware_scheduling_enabled is False
    assert selections[0].snapshot.model_group == "m1"
    assert selections[0].snapshot.service_tier == "standard"


@pytest.mark.asyncio
async def test_drain_workers_runs_workers_until_full_idle_round() -> None:
    class _Worker:
        def __init__(self, outcomes: list[bool]) -> None:
            self.outcomes = outcomes
            self.calls = 0

        async def process_once(self) -> bool:
            self.calls += 1
            return self.outcomes.pop(0) if self.outcomes else False

    first = _Worker([True, False, False])
    second = _Worker([False, True, False, False])

    await _SCRIPT_MODULE.drain_workers([first, second], max_rounds=10)

    assert first.calls == 4
    assert second.calls == 4


@pytest.mark.asyncio
async def test_run_measurement_rejects_non_standard_service_tier_before_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakePrisma:
        def __init__(self, datasource):  # noqa: ANN001
            del datasource
            raise AssertionError("non-standard service tier should fail before db connection")

    monkeypatch.setattr(_SCRIPT_MODULE, "Prisma", _FakePrisma)
    args = SimpleNamespace(
        database_url="postgresql://postgres:postgres@localhost:5432/deltallm",
        force=False,
        service_tier="priority",
    )

    with pytest.raises(RuntimeError, match="supports only standard"):
        await _SCRIPT_MODULE.run_measurement(args)


@pytest.mark.asyncio
async def test_cleanup_run_data_scopes_deletes_by_api_key() -> None:
    class _FakeDB:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def execute_raw(self, sql: str, *params):
            self.calls.append((sql, params))

    db = _FakeDB()

    await _SCRIPT_MODULE.cleanup_run_data(db, created_by_api_key="load-script-1")

    assert len(db.calls) == 3
    for sql, params in db.calls:
        assert params == ("load-script-1",)
        assert "created_by_api_key = $1" in sql


@pytest.mark.asyncio
async def test_run_measurement_preserves_schema_failure_without_running_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakePrisma:
        def __init__(self, datasource):  # noqa: ANN001
            self.datasource = datasource
            self.connected = False
            self.disconnected = False

        async def connect(self) -> None:
            self.connected = True

        async def disconnect(self) -> None:
            self.disconnected = True

    cleanup_calls = {"count": 0}

    async def _fail_ensure_batch_schema(db) -> None:  # noqa: ANN001
        raise RuntimeError("schema missing")

    async def _cleanup_run_data(db, *, created_by_api_key: str) -> None:  # noqa: ANN001
        del created_by_api_key
        cleanup_calls["count"] += 1

    monkeypatch.setattr(_SCRIPT_MODULE, "Prisma", _FakePrisma)
    monkeypatch.setattr(_SCRIPT_MODULE, "ensure_batch_schema", _fail_ensure_batch_schema)
    monkeypatch.setattr(_SCRIPT_MODULE, "cleanup_run_data", _cleanup_run_data)

    args = SimpleNamespace(
        database_url="postgresql://postgres:postgres@localhost:5432/deltallm",
        force=False,
    )

    with pytest.raises(RuntimeError, match="schema missing"):
        await _SCRIPT_MODULE.run_measurement(args)

    assert cleanup_calls["count"] == 0
