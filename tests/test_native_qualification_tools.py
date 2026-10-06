"""Qualification tool ownership and gates do not need a gateway or cluster."""

import argparse
import asyncio
import sys
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
import yaml
from src.config import GeneralSettings

from scripts import measure_gateway_load_sharded as shards
from scripts.measure_gateway_load import RequestResult, run_constant_arrival
from tests.performance import gateway_concurrency_metrics as metrics
from tests.performance.run_gateway_concurrency import generator_evidence_failures
from tests.performance.run_native_qualification import latency_and_queue_gates, qualification_values
from tests.performance import native_qualification_economics as economics
from tests.performance.cluster_load_generator import cluster_generator_job
from tests.performance.gateway_concurrency_diagnostics import POSTGRESQL_FIELDS, POSTGRESQL_SNAPSHOT
from tests.performance.native_qualification_resources import parse_cpu_stat, cpu_counter_deltas


def test_retained_runtime_metrics_use_only_fixed_diagnostic_labels():
    selected = metrics.select_metrics("""
deltallm_redis_command_round_trips_total{allocation="critical",owner="routing",family="lua",outcome="success"} 6
deltallm_redis_allocation_waiters{allocation="critical"} 2
deltallm_metrics_snapshot_timestamp_seconds 123
deltallm_metrics_snapshot_generations_total{outcome="success"} 1
deltallm_python_gc_pause_seconds_count{generation="2"} 1
deltallm_request_phase_in_flight{route="chat_completions",phase="upstream_http"} 4
deltallm_optional_request_diagnostics_total{route="chat_completions",reason="dependency_unavailable"} 1
deltallm_prompt_cache_lookups_total{entity="binding",tier="negative_l1"} 3
deltallm_redis_command_round_trips_total{allocation="critical",owner="private",family="lua",outcome="success"} 99
deltallm_python_gc_pause_seconds_count{generation="private"} 99
""")
    assert len(selected) == 8
    assert "private" not in repr(selected)


def test_cpu_counters_are_complete_and_deltas_do_not_hide_missing_data():
    counters = parse_cpu_stat(
        "usage_usec 100\nuser_usec 50\nsystem_usec 50\nnr_periods 10\n"
        "nr_throttled 2\nthrottled_usec 5\n"
    )
    source = {"source_role": "api", "source_identity": "owned-pod", "counters": counters}
    assert cpu_counter_deltas({"sources": [source]}, {"sources": [source]})[0]["counters"] == {
        name: 0 for name in counters
    }
    assert cpu_counter_deltas({"sources": []}, {"sources": [source]})[0]["error"] == (
        "cpu_counter_baseline_unavailable"
    )
    changed = {**source, "counters": {**counters, "usage_usec": 1}}
    assert cpu_counter_deltas({"sources": [source]}, {"sources": [changed]})[0]["error"] == (
        "cpu_counter_reset"
    )


@pytest.mark.parametrize(
    "payload", ["usage_usec 1", "x" * 4097, "usage_usec -1", "usage_usec 1\nusage_usec 1"]
)
def test_cpu_counter_parser_rejects_incomplete_or_unbounded_input(payload):
    with pytest.raises(ValueError):
        parse_cpu_stat(payload)


def test_shards_preserve_exact_arrival_counts_and_connection_budget():
    specs = shards.build_shard_specs(
        urls=["http://one", "http://two"],
        workers=4,
        rate=500,
        duration=600,
        max_in_flight=1000,
        max_keepalive=100,
    )
    assert sum(spec.target_count for spec in specs) == 300000
    assert sum(spec.max_in_flight for spec in specs) == 1000
    assert sum(spec.max_keepalive for spec in specs) == 100
    assert {spec.rate for spec in specs} == {125}
    assert [spec.schedule_offset_seconds for spec in specs] == [0, 0.002, 0.004, 0.006]


@pytest.mark.parametrize(
    "change",
    [
        {"rate": float("nan")},
        {"rate": 1001},
        {"duration": float("inf")},
        {"workers": 17},
        {"max_in_flight": 10001},
        {"max_keepalive": 1001},
    ],
)
def test_shards_reject_invalid_budgets(change):
    options = dict(
        urls=["http://one"],
        workers=4,
        rate=500,
        duration=600,
        max_in_flight=1000,
        max_keepalive=100,
    )
    options.update(change)
    with pytest.raises(ValueError):
        shards.build_shard_specs(**options)


async def test_shard_cancellation_terminates_and_reaps_the_process(tmp_path, monkeypatch):
    started = asyncio.Event()
    processes = []
    original = asyncio.create_subprocess_exec

    async def create(*command, **options):
        process = await original(*command, **options)
        processes.append(process)
        started.set()
        return process

    monkeypatch.setattr(shards.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(
        shards,
        "_child_command",
        lambda *args, **options: (
            sys.executable,
            "-c",
            "import time;time.sleep(60)",
        ),
    )
    args = argparse.Namespace(start_delay=1, duration=1, drain_timeout=1)
    spec = shards.ShardSpec(0, "http://one", 1, 1, 0, 1, 1)
    task = asyncio.create_task(
        shards._run_worker(args, spec, start_at_epoch=0, output=tmp_path / "child")
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processes[0].returncode is not None


async def test_shard_output_overflow_is_bounded_and_process_is_reaped(tmp_path, monkeypatch):
    processes = []
    original = asyncio.create_subprocess_exec

    async def create(*command, **options):
        process = await original(*command, **options)
        processes.append(process)
        return process

    monkeypatch.setattr(shards.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(
        shards,
        "_child_command",
        lambda *args, **options: (
            sys.executable,
            "-c",
            "import sys,time;sys.stdout.write('x'*300000);sys.stdout.flush();time.sleep(60)",
        ),
    )
    args = argparse.Namespace(start_delay=1, duration=1, drain_timeout=1)
    spec = shards.ShardSpec(0, "http://one", 1, 1, 0, 1, 1)
    with pytest.raises(ExceptionGroup, match="TaskGroup"):
        await shards._run_worker(args, spec, start_at_epoch=0, output=tmp_path / "child")
    assert processes[0].returncode is not None


def test_native_role_metrics_are_collected_without_tenant_labels():
    payload = (
        "deltallm_accounting_read_model_progress_available 1\n"
        "deltallm_accounting_native_oldest_work_age_seconds 0.1\n"
        'deltallm_accounting_journal_worker_actions_total{action="materialize",outcome="success"} 2\n'
        'deltallm_accounting_permit_bank_subjects{lane="0"} 4\n'
        'deltallm_accounting_read_model_pending_partitions{tenant="private"} 99\n'
    )
    selected = metrics.select_metrics(payload)
    assert len(selected) == 4
    assert all("tenant" not in value.labels for value in selected)


def test_dependency_diagnostics_measure_native_writes_not_only_legacy_functions():
    for function in (
        "allocate_local_permit_grants_batch",
        "append_terminal_journal",
        "materialize_terminal_journal",
        "project_read_models",
    ):
        assert "deltallm_accounting_" + function in POSTGRESQL_SNAPSHOT
    assert {
        "native_funding_calls",
        "native_terminal_ack_calls",
        "native_materialization_calls",
        "native_reporting_calls",
        "native_exec_milliseconds",
        "native_wal_bytes",
    } <= POSTGRESQL_FIELDS


def test_metric_deltas_keep_missing_series_and_reject_counter_reset(tmp_path):
    recorder = metrics.MetricsRecorder(["http://one"], tmp_path / "metrics")
    identity = ("deltallm_accounting_journal_worker_actions_total", (("action", "claim"),))
    recorder._capture_values(0, {identity: 5})
    recorder._capture_values(0, {identity: 7})
    recorder._capture_values(0, {})
    assert recorder.counter_delta(identity[0]) == 2
    recorder._capture_values(0, {identity: 1})
    with pytest.raises(ValueError, match="decreased"):
        recorder.counter_delta(identity[0])


def test_retained_metric_identities_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(metrics, "MAX_SAMPLES_PER_SCRAPE", 1)
    recorder = metrics.MetricsRecorder(["http://one"], tmp_path / "metrics")
    recorder._capture_values(0, {("first", ()): 0})
    with pytest.raises(ValueError, match="identity budget"):
        recorder._capture_values(0, {("second", ()): 0})


async def test_generator_evidence_accepts_complete_500_rps():
    async def request(_index, _request_id):
        return RequestResult(200)

    run = await run_constant_arrival(
        rate=500, duration_seconds=0.1, max_in_flight=10, request=request
    )
    assert generator_evidence_failures(run, target_rate=500) == []


@pytest.mark.parametrize(
    "p95,p99,expected", [(0.15, 0.3, True), (0.151, 0.3, False), (0.1, 0.301, False)]
)
def test_latency_gate_is_independent_from_success(p95, p99, expected):
    report = {
        "latency_seconds": {"p95": p95, "p99": p99},
        "client_in_flight": [
            {"offset_seconds": second, "client_in_flight": 2} for second in range(61)
        ],
        "success_count": 30000,
        "target_count": 30000,
    }
    gate = latency_and_queue_gates(report)
    assert gate["success_passed"] and gate["queue_passed"]
    assert (gate["p95_passed"] and gate["p99_passed"]) is expected


def test_qualification_profile_and_generator_keep_fixed_bounded_topology(tmp_path, monkeypatch):
    monkeypatch.chdir(shards.Path(__file__).resolve().parents[1])
    calls = []
    cluster = SimpleNamespace(
        directory=SimpleNamespace(name=str(tmp_path)), output=tmp_path, apply=calls.append
    )
    values = yaml.safe_load(qualification_values(cluster, "gateway:unit").read_text())
    assert values["replicaCount"] == 4 and values["autoscaling"] == {"enabled": False}
    assert values["accountingRequest"]["replicaCount"] == 2
    assert values["accountingWorker"]["replicaCount"] == 1
    assert values["resources"]["limits"] == {"cpu": "2", "memory": "1Gi"}
    assert values["batchWorker"] == {"enabled": False}
    assert values["config"]["general_settings"]["accounting_execution_mode"] == "local_journal"
    settings = values["config"]["general_settings"]
    assert not set(settings) - GeneralSettings.model_fields.keys()
    assert settings["gateway_ingress_max_active"] == 256
    assert settings["gateway_preflight_global_max_parallel"] == 150
    assert settings["gateway_preflight_org_max_parallel"] == 150
    assert settings["prompt_negative_cache_enabled"]
    assert settings["prompt_negative_l1_ttl_seconds"] == 30
    assert (
        values["accountingWorker"]["config"]["general_settings"]["accounting_hot_path_db_pool_size"]
        == 8
    )
    assert (
        values["accountingWorker"]["config"]["general_settings"]["accounting_projection_batch_size"]
        == 256
    )
    assert settings["redis_critical_max_waiters"] == 64
    assert settings["redis_cache_max_waiters"] == settings["redis_bulk_max_waiters"] == 0
    for rate in (50, 100, 200, 500):
        job = cluster_generator_job("gateway:unit", ["http://one"] * 4, rate=rate, duration=600)
        container = job["spec"]["template"]["spec"]["containers"][0]
        assert container["resources"]["limits"] == {"cpu": "4", "memory": "1Gi"}
        assert "--expect-fixed-one-token" in container["args"]
        assert "--bypass-cache" in container["args"]
    assert len(calls) == 1


@pytest.mark.parametrize(
    "problem", [None, "missing_scope", "scope_charge", "reserved", "legacy", "failed_response"]
)
async def test_economic_gate_requires_exact_charge_and_every_scope(monkeypatch, problem):
    charge = Decimal("0.000007") * 11
    before = {"facts": 0, "fact_charge": "0"}
    after = {
        "facts": 11,
        "fact_charge": str(charge),
        "unsafe_windows": 0,
        "legacy_spend_rows": int(problem == "legacy"),
    }
    rows = [
        {"scope_type": name, "committed": str(charge), "reserved": "0", "provisional": "0"}
        for name in ("api_key", "user", "team", "organization")
    ]
    if problem == "missing_scope":
        rows.pop()
    if problem == "scope_charge":
        rows[0]["committed"] = "0"
    if problem == "reserved":
        rows[0]["reserved"] = "1"
    monkeypatch.setattr(economics, "accounting_snapshot", AsyncMock(return_value=after))
    db = SimpleNamespace(query_raw=AsyncMock(return_value=rows))
    result = await economics.reconcile_native(
        db, before=before, successes=10, all_successful=problem != "failed_response"
    )
    assert result["passed"] is (problem is None)
    assert result["expected_exact_charge_delta"] == str(charge)
