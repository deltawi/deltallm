"""Qualification tool ownership and gates do not need a gateway or cluster."""

import argparse
import asyncio
from contextlib import contextmanager, asynccontextmanager
import json
import sys
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest
import yaml
from src.config import GeneralSettings

from scripts import measure_gateway_load_sharded as shards
from scripts.measure_gateway_load import RequestResult, run_constant_arrival
from tests.performance import gateway_concurrency_metrics as metrics
from tests.performance.run_gateway_concurrency import generator_evidence_failures
from tests.performance.run_native_qualification import (
    latency_and_queue_gates,
    qualification_schedule,
    stage_seconds,
    qualification_values,
)
from tests.performance import native_qualification_economics as economics
from tests.performance import run_native_qualification as qualification
from tests.performance.cluster_load_generator import cluster_generator_job
from tests.performance.gateway_concurrency_diagnostics import POSTGRESQL_FIELDS, POSTGRESQL_SNAPSHOT
from tests.performance.native_qualification_resources import parse_cpu_stat, cpu_counter_deltas


def test_full_qualification_keeps_both_four_tier_series():
    phases, rates = qualification_schedule(30, None)
    assert phases == (("short", 30), ("qualification", 600))
    assert rates == (50, 100, 200, 500)


def test_normal_high_rate_stability_observation_is_longer_not_a_weaker_limit():
    assert [stage_seconds("short", rate, 30, diagnostic=False) for rate in (50, 100, 200, 500)] == [
        30,
        30,
        30,
        600,
    ]
    assert [
        stage_seconds("qualification", rate, 600, diagnostic=False) for rate in (50, 100, 200, 500)
    ] == [600, 600, 600, 600]
    assert stage_seconds("short", 500, 30, diagnostic=True) == 30
    assert stage_seconds("short", 1000, 60, diagnostic=True) == 60


def test_upper_tier_diagnostic_never_repeats_lower_tiers_or_runs_long_stages():
    phases, rates = qualification_schedule(30, (200, 500))
    assert phases == (("short", 30),)
    assert rates == (200, 500)


def test_thousand_rps_is_diagnostic_only_and_keeps_the_normal_schedule():
    assert qualification_schedule(60, (1000,)) == ((("short", 60),), (1000,))
    assert qualification_schedule(60, None) == (
        (("short", 60), ("qualification", 600)),
        (50, 100, 200, 500),
    )


@pytest.mark.parametrize("rates", [(), (500, 200), (200, 200), (1500,)])
def test_selected_tiers_reject_missing_duplicate_unsorted_or_unsupported_rates(rates):
    with pytest.raises(ValueError):
        qualification_schedule(30, rates)


@pytest.mark.parametrize("rates", [(200, 500), (50, 100, 200, 500), (1000,)])
def test_even_a_passing_selected_series_is_not_release_eligible(tmp_path, monkeypatch, rates):
    output = tmp_path / "diagnostic"

    class Cluster:
        def __init__(self, path, **options):
            self.output = path
            path.mkdir()

        @contextmanager
        def owned(self, image):
            yield self

    monkeypatch.setattr(qualification, "LifecycleCluster", Cluster)
    monkeypatch.setattr(qualification, "candidate_manifest", lambda *args, **options: {})
    monkeypatch.setattr(
        qualification.subprocess,
        "check_output",
        lambda command, **options: (
            "" if command[1] == "ps" else '{"cpu_count":6,"memory_bytes":12000000000}'
        ),
    )
    for name in ("install_native_dependencies", "install_resource_metrics", "prime_database"):
        monkeypatch.setattr(qualification, name, lambda *args: None)
    monkeypatch.setattr(qualification, "preload_images", lambda *args: "metrics:fixture")
    monkeypatch.setattr(qualification, "qualification_values", lambda *args: tmp_path / "values")
    monkeypatch.setattr(qualification, "collect_native_logs", lambda *args: None)
    exercise = AsyncMock(return_value=[{"passed": True} for _ in rates])
    monkeypatch.setattr(qualification, "exercise", exercise)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "qualification",
            "--image",
            "fixture:sealed",
            "--output",
            str(output),
            "--diagnostic-rates",
            *(str(rate) for rate in rates),
        ],
    )
    qualification.main()
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["diagnostic_passed"]
    assert not manifest["qualification_passed"] and not manifest["release_eligible"]
    assert exercise.call_args.kwargs["diagnostic_rates"] == rates


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


@pytest.mark.parametrize(
    "error,reason",
    [
        (httpx.ReadTimeout("private token"), "http_timeout"),
        (httpx.ConnectError("private token"), "http_connection"),
        (TimeoutError("private token"), "deadline"),
        (UnicodeError("private token"), "decode"),
        (ValueError("private token"), "parse"),
        (RuntimeError("private token"), "unknown"),
    ],
)
def test_metric_failure_classification_preserves_only_bounded_reason(error, reason):
    assert metrics.scrape_failure_reason(error) == reason
    records = metrics._encode_metric_records(
        [None], [metrics.MetricSource("http://fixture/metrics", "api", 0)], 1.0, [reason]
    )
    saved = json.loads(records[0].line)
    assert saved["error"] == "scrape_failed" and saved["error_reason"] == reason
    assert "private" not in records[0].line and "fixture" not in records[0].line
    assert records[0].values is None


def test_metric_http_status_failure_does_not_export_response_or_url():
    request = httpx.Request("GET", "http://fixture/private-token")
    response = httpx.Response(503, request=request, text="private token")
    assert (
        metrics.scrape_failure_reason(
            httpx.HTTPStatusError("private token", request=request, response=response)
        )
        == "http_status"
    )


@pytest.mark.parametrize("reasons", [["private token"], ["unknown", "unknown"]])
def test_metric_failure_encoding_rejects_unbounded_or_misaligned_classifications(reasons):
    with pytest.raises(ValueError, match="classifications are invalid"):
        metrics._encode_metric_records(
            [None], [metrics.MetricSource("http://fixture/metrics", "api", 0)], 1.0, reasons
        )


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
    assert settings["accounting_microbatch_max_size"] == 32
    assert (
        values["accountingRequest"]["config"]["general_settings"]["accounting_microbatch_max_size"]
        == 32
    )
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
    "unsafe",
    [None, "fsync", "synchronous_commit", "full_page_writes", "autovacuum", "max_connections"],
)
def test_native_database_fixture_has_explicit_resources_and_preserves_durability(
    tmp_path, monkeypatch, unsafe
):
    observed = {
        "shared_buffers": "512MB",
        "max_wal_size": "4GB",
        "max_connections": "1000",
        "fsync": "on",
        "synchronous_commit": "on",
        "full_page_writes": "on",
        "autovacuum": "on",
    }
    if unsafe:
        observed[unsafe] = "2000" if unsafe == "max_connections" else "off"
    commands = []

    def kubectl(*arguments, **options):
        commands.append(arguments)
        if "df" in arguments:
            return SimpleNamespace(
                stdout="Filesystem 1B-blocks Used Available Use% Mounted on\n"
                "shm 268435456 0 268435456 0% /dev/shm\n"
            )
        return SimpleNamespace(stdout=json.dumps(observed))

    base = []
    monkeypatch.setattr(
        qualification, "install_capacity_dependencies", lambda *args: base.append(args)
    )
    cluster = SimpleNamespace(output=tmp_path, kubectl=kubectl, event=lambda *args, **options: None)
    if unsafe:
        with pytest.raises(RuntimeError, match="settings do not match"):
            qualification.install_native_dependencies(cluster, "image:sealed")
        assert not (tmp_path / "database-fixture.json").exists()
    else:
        qualification.install_native_dependencies(cluster, "image:sealed")
        saved = json.loads((tmp_path / "database-fixture.json").read_text())
        assert saved["observed"] == observed
        assert saved["shared_memory_bytes"] == 256 * 1024 * 1024
    assert base == [(cluster, "image:sealed")]
    pod = json.loads(commands[0][-1])["spec"]["template"]["spec"]
    patch = pod["containers"][0]
    assert patch["resources"]["limits"] == {"cpu": "4", "memory": "4Gi"}
    assert patch["volumeMounts"] == [{"name": "postgres-shm", "mountPath": "/dev/shm"}]
    assert pod["volumes"] == [
        {"name": "postgres-shm", "emptyDir": {"medium": "Memory", "sizeLimit": "256Mi"}}
    ]
    assert "max_connections=1000" in patch["args"]
    assert {"fsync=on", "synchronous_commit=on", "full_page_writes=on"}.issubset(patch["args"])
    assert commands[1] == ("rollout", "status", "deployment/postgres", "--timeout=180s")


@pytest.mark.parametrize("failure", [False, True])
def test_native_log_collection_is_bounded_and_does_not_replace_failures(tmp_path, failure):
    commands = []

    def kubectl(*arguments, **options):
        commands.append((arguments, options))
        if failure:
            raise RuntimeError("private diagnostic detail")
        return SimpleNamespace(stdout="x" * (3 * 1024 * 1024), stderr="")

    qualification.collect_native_logs(SimpleNamespace(output=tmp_path, kubectl=kubectl))
    assert len(commands) == 2
    for arguments, options in commands:
        assert "--tail=5000" in arguments
        assert options == {"check": False, "timeout": 20}
    for name in ("postgres", "gateway"):
        saved = (tmp_path / f"native-final-{name}.log").read_text()
        assert len(saved) <= 2 * 1024 * 1024
        assert "private diagnostic detail" not in saved
        if failure:
            assert saved == "Log collection failed: RuntimeError\n"


@pytest.mark.parametrize("capacity", [67108864, 536870912])
def test_native_database_fixture_rejects_wrong_shared_memory_capacity(
    tmp_path, monkeypatch, capacity
):
    observed = {
        "shared_buffers": "512MB",
        "max_wal_size": "4GB",
        "max_connections": "1000",
        "fsync": "on",
        "synchronous_commit": "on",
        "full_page_writes": "on",
        "autovacuum": "on",
    }

    def kubectl(*arguments, **options):
        if "df" in arguments:
            return SimpleNamespace(
                stdout="Filesystem 1B-blocks Used Available Use% Mounted on\n"
                f"shm {capacity} 0 {capacity} 0% /dev/shm\n"
            )
        return SimpleNamespace(stdout=json.dumps(observed))

    monkeypatch.setattr(qualification, "install_capacity_dependencies", lambda *args: None)
    cluster = SimpleNamespace(output=tmp_path, kubectl=kubectl, event=lambda *args, **options: None)
    with pytest.raises(RuntimeError, match="shared-memory capacity does not match"):
        qualification.install_native_dependencies(cluster, "image:sealed")
    assert not (tmp_path / "database-fixture.json").exists()


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


@pytest.mark.parametrize("extra_fact", [0, 1])
async def test_warmup_has_no_precheck_and_rejects_an_extra_charge(monkeypatch, extra_fact):
    charge = Decimal("0.000007") * (10 + extra_fact)
    after = {
        "facts": 10 + extra_fact,
        "fact_charge": str(charge),
        "unsafe_windows": 0,
        "legacy_spend_rows": 0,
    }
    rows = [
        {"scope_type": name, "committed": str(charge), "reserved": "0", "provisional": "0"}
        for name in ("api_key", "user", "team", "organization")
    ]
    monkeypatch.setattr(economics, "accounting_snapshot", AsyncMock(return_value=after))
    result = await economics.reconcile_native(
        SimpleNamespace(query_raw=AsyncMock(return_value=rows)),
        before={"facts": 0, "fact_charge": "0"},
        successes=10,
        all_successful=True,
        precheck_count=0,
    )
    assert result["precheck_count"] == 0
    assert result["passed"] is (extra_fact == 0)
    assert result["expected_exact_charge_delta"] == "0.000070"


@pytest.mark.parametrize("precheck_count", [-1, 2, True, 0.0])
async def test_economic_gate_rejects_invalid_precheck_counts(precheck_count):
    with pytest.raises(ValueError, match="Precheck count"):
        await economics.reconcile_native(
            None, before={}, successes=10, all_successful=True, precheck_count=precheck_count
        )


@pytest.mark.parametrize("problem", [None, "responses", "generator", "drain", "economics"])
async def test_same_rate_warmup_keeps_raw_evidence_and_exact_economic_gates(
    tmp_path, monkeypatch, problem
):
    @asynccontextmanager
    async def database():
        yield "db"

    stage = tmp_path / "qualification-500rps"
    events = []
    cluster = SimpleNamespace(event=lambda *args, **options: events.append((args, options)))
    run = SimpleNamespace(target_count=30000)

    async def generate(*args, **options):
        options["output"].mkdir(parents=True)
        return run

    generator = AsyncMock(side_effect=generate)
    reconcile = AsyncMock(return_value={"passed": problem != "economics"})
    monkeypatch.setattr(qualification, "local_database", database)
    monkeypatch.setattr(qualification, "accounting_snapshot", AsyncMock(return_value={"facts": 0}))
    monkeypatch.setattr(qualification, "run_cluster_generator", generator)
    monkeypatch.setattr(
        qualification,
        "summarize",
        lambda *args, **options: {"success_count": 29999 if problem == "responses" else 30000},
    )
    monkeypatch.setattr(
        qualification,
        "generator_evidence_failures",
        lambda *args, **options: ["generator_drops"] if problem == "generator" else [],
    )
    monkeypatch.setattr(
        qualification, "wait_native_drain", AsyncMock(return_value={"passed": problem != "drain"})
    )
    monkeypatch.setattr(qualification, "reconcile_native", reconcile)
    capture = AsyncMock(return_value={"available": True})
    monkeypatch.setattr(qualification, "capture_unsettled_operations", capture)
    result = await qualification.warm_stage(
        cluster, "image:sealed", ["http://api"] * 4, stage=stage, rate=500
    )
    assert generator.call_args.kwargs == {"rate": 500, "duration": 60, "output": stage / "warmup"}
    assert result["passed"] is (problem is None)
    assert json.loads((stage / "warmup.json").read_text()) == result
    assert reconcile.call_args.kwargs["precheck_count"] == 0
    assert reconcile.call_args.kwargs["all_successful"] is (problem != "responses")
    assert capture.await_count == int(problem == "drain")
    assert len(events) == 1


async def test_failed_warmup_stops_before_measured_load_and_retains_the_failed_stage(
    tmp_path, monkeypatch
):
    from tests.performance.native_qualification_failures import QualificationStageStopped

    stage = tmp_path / "qualification-500rps"
    stage.mkdir()
    monkeypatch.setattr(qualification, "warm_stage", AsyncMock(return_value={"passed": False}))
    measure = AsyncMock()
    monkeypatch.setattr(qualification, "measure", measure)
    with pytest.raises(QualificationStageStopped, match="Warm-up failed") as stopped:
        await qualification.run_stage(
            SimpleNamespace(output=tmp_path),
            "image:sealed",
            ["http://api"] * 4,
            api_ports=[1, 2, 3, 4],
            worker_ports=[5, 6, 7],
            manifest=tmp_path / "manifest.json",
            rate=500,
            duration=600,
            phase="qualification",
        )
    assert measure.await_count == 0
    assert not stopped.value.report["passed"]
    assert not stopped.value.report["measurement_started"]
    assert json.loads((stage / "qualification.json").read_text()) == stopped.value.report


@pytest.mark.parametrize(
    "problem",
    [
        None,
        "http",
        "id",
        "preserved",
        "facts",
        "charge",
        "drain",
        "readiness",
        "models",
        "auth",
        "schema",
        "ui",
        "stream",
    ],
)
async def test_ordinary_client_gate_records_failures_before_load(tmp_path, monkeypatch, problem):
    import httpx
    from tests.performance import native_client_requests as clients
    from src.request_identity import resolve_request_id

    @asynccontextmanager
    async def database():
        yield object()

    requests = []

    def respond(request):
        if request.method == "GET":
            if request.url.path == "/v1/models":
                return httpx.Response(
                    200,
                    json={
                        "data": [{"id": "other" if problem == "models" else "concurrency-fixture"}]
                    },
                )
            if request.url.path == "/ui":
                return httpx.Response(
                    200,
                    headers={"content-type": "text/html"},
                    text="missing"
                    if problem == "ui"
                    else '<html><script src="/ui/assets/app.js"></script></html>',
                )
            return httpx.Response(503 if problem == "readiness" else 200)
        payload = json.loads(request.content)
        if "authorization" not in request.headers:
            return httpx.Response(200 if problem == "auth" else 401)
        if payload["messages"] == "invalid":
            return httpx.Response(200 if problem == "schema" else 422)
        supplied = request.headers.get("x-request-id")
        requests.append(supplied)
        if problem == "http":
            return httpx.Response(503, text="not JSON")
        resolved = resolve_request_id(supplied)
        if problem == "id":
            resolved = ""
        if problem == "preserved":
            resolved = "other-valid-id"
        if payload["stream"]:
            chunks = [
                {"choices": [{"delta": {"content": "OK"}}]},
                {
                    "choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 1},
                },
            ]
            stream = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
            if problem != "stream":
                stream += "data: [DONE]\n\n"
            return httpx.Response(200, headers={"x-request-id": resolved}, text=stream)
        return httpx.Response(
            200,
            headers={"x-request-id": resolved},
            json={
                "usage": {"completion_tokens": 1},
                "choices": [{"message": {"role": "assistant", "content": "OK"}}],
            },
        )

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        clients.httpx,
        "AsyncClient",
        lambda **options: original_client(**options, transport=httpx.MockTransport(respond)),
    )
    monkeypatch.setattr(clients, "local_database", database)
    monkeypatch.setattr(
        clients,
        "accounting_snapshot",
        AsyncMock(
            side_effect=[
                {"facts": 0, "fact_charge": "0"},
                {
                    "facts": 23 if problem == "facts" else 24,
                    "fact_charge": "0" if problem == "charge" else "0.000168",
                },
            ]
        ),
    )
    monkeypatch.setattr(
        clients, "wait_native_drain", AsyncMock(return_value={"passed": problem != "drain"})
    )
    if problem is None:
        await clients.verify_native_clients([1, 2, 3, 4], [5, 6, 7], tmp_path)
    else:
        with pytest.raises(RuntimeError, match="Ordinary native client requests failed"):
            await clients.verify_native_clients([1, 2, 3, 4], [5, 6, 7], tmp_path)
    result = json.loads((tmp_path / "native-client-requests.json").read_text())
    assert result["passed"] is (problem is None)
    assert len(requests) == len(result["requests"]) == 24
    assert requests.count(None) == 8
    assert len(result["functional_checks"]) == 16


@pytest.mark.parametrize("replace_all", [True, False])
async def test_native_recovery_waits_for_restarts_and_requires_a_full_rollout(
    tmp_path, monkeypatch, replace_all
):
    from tests.performance import native_recovery as recovery

    class Cluster:
        output = tmp_path
        rolled = False
        calls = []
        completed_roles = set()

        def kubectl(self, *arguments, **options):
            self.calls.append(arguments)
            if arguments[:2] == ("rollout", "status"):
                assert options["timeout"] == 190
                assert arguments[-1] == "--timeout=180s"
                self.completed_roles.add(arguments[2])
            if arguments[0] != "get":
                return SimpleNamespace(stdout="")
            role = arguments[3].split("component=", 1)[1]
            items = [
                {
                    "metadata": {
                        "name": f"{role}-{index}",
                        "uid": f"{role}-{index}-"
                        + ("new" if self.rolled and replace_all else "old"),
                    },
                    "status": {
                        "conditions": [{"type": "Ready", "status": "True"}],
                        "containerStatuses": [{"restartCount": 0}],
                    },
                }
                for index in range(recovery.ROLES[role])
            ]
            deployment = "deployment/gateway-deltallm" + ("" if role == "api" else "-" + role)
            if self.rolled and deployment not in self.completed_roles:
                # Helm can return while the last old ready pod still exists.
                items.append(
                    {
                        "metadata": {"name": role + "-retiring", "uid": role + "-old"},
                        "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                    }
                )
            return SimpleNamespace(stdout=json.dumps({"items": items}))

        def kill_container(self, name):
            self.calls.append(("kill", name))
            return "containerd://" + "a" * 64

        def event(self, *arguments, **options):
            self.calls.append(arguments)

    cluster = Cluster()
    overrides = []

    def rollout(owner, values, *arguments):
        assert owner is cluster and values == tmp_path / "values.yaml"
        overrides.extend(arguments)
        cluster.rolled = True

    check = AsyncMock()
    monkeypatch.setattr(recovery, "capacity_release", rollout)
    monkeypatch.setattr(recovery, "check_clients", check)
    if replace_all:
        await recovery.verify_native_recovery(cluster, tmp_path / "values.yaml")
        assert check.await_count == 2
        result = json.loads((tmp_path / "native-recovery.json").read_text())
        assert result["passed"] and len(result["process_losses"]) == 3
    else:
        with pytest.raises(RuntimeError, match="did not replace every process"):
            await recovery.verify_native_recovery(cluster, tmp_path / "values.yaml")
        assert check.await_count == 1
        assert not (tmp_path / "native-recovery.json").exists()
    assert len([call for call in cluster.calls if call[0] == "kill"]) == 3
    waits = [call for call in cluster.calls if call[0] == "wait"]
    assert len(waits) == 6
    assert sum("restartCount" in call[1] for call in waits) == 3
    assert cluster.completed_roles == {
        "deployment/gateway-deltallm",
        "deployment/gateway-deltallm-accounting-request",
        "deployment/gateway-deltallm-accounting-worker",
    }
    assert len(overrides) == 6 and all("image" not in argument for argument in overrides)


@pytest.mark.parametrize("failed_role", ["api", "accounting-request", "accounting-worker"])
async def test_native_recovery_rejects_an_incomplete_rollout(tmp_path, monkeypatch, failed_role):
    from tests.performance import native_recovery as recovery

    pods = {
        role: [
            {
                "metadata": {"name": role + "-pod", "uid": role + "-old"},
                "status": {"containerStatuses": [{"restartCount": 0}]},
            }
        ]
        for role in recovery.ROLES
    }
    failing_deployment = "deployment/gateway-deltallm" + (
        "" if failed_role == "api" else "-" + failed_role
    )

    def kubectl(*arguments, **options):
        if arguments[:3] == ("rollout", "status", failing_deployment):
            raise TimeoutError("The fixture rollout did not complete")

    cluster = SimpleNamespace(output=tmp_path, kubectl=kubectl, kill_container=lambda name: name)
    check = AsyncMock()
    monkeypatch.setattr(recovery, "role_pods", lambda owner: pods)
    monkeypatch.setattr(recovery, "check_clients", check)
    monkeypatch.setattr(recovery, "capacity_release", lambda *arguments: None)
    with pytest.raises(TimeoutError, match="rollout did not complete"):
        await recovery.verify_native_recovery(cluster, tmp_path / "values.yaml")
    assert check.await_count == 1
    assert not (tmp_path / "native-recovery.json").exists()
