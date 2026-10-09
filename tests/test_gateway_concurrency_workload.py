from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import httpx
from pydantic import ValidationError
import pytest
import yaml

from src.config import GeneralSettings
from tests.performance import gateway_concurrency_metrics as metrics
from tests.performance.gateway_concurrency_diagnostics import (
    DependencyDiagnosticsRecorder,
    KubernetesResourceRecorder,
    _quantity,
)
from tests.performance import run_gateway_concurrency as workload
from tests.performance.gateway_concurrency_dependencies import fixture_database_url
from tests.performance.gateway_concurrency_fixture import fixture_key
from tests.performance.gateway_concurrency_manifest import ServerManifest
from tests.performance.gateway_concurrency_metrics import MetricSource
from tests.performance.gateway_concurrency_mock import app, complete, CompletionRequest
from tests.performance.run_gateway_concurrency import error_code, valid_completion
from tests.performance.summarize_historical_concurrency import summarize

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_provider_is_fixed_and_rejects_other_workloads() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        body = {
            "model": "fixed-one-token",
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "max_tokens": 1,
            "stream": False,
        }
        response = await client.post("/v1/chat/completions", json=body)
        assert response.status_code == 200
        assert valid_completion(response.json())
        discovered = await client.get("/v1/models")
        assert discovered.status_code == 200
        assert discovered.json()["data"][0]["id"] == body["model"]
        for changed in ({"model": "other"}, {"max_tokens": 2}):
            assert (
                await client.post("/v1/chat/completions", json={**body, **changed})
            ).status_code == 400


async def test_lifecycle_provider_stream_starts_and_closes_without_terminal_success():
    response = await complete(
        CompletionRequest(
            model="fixed-one-token",
            messages=[{"role": "user", "content": "Reply with OK."}],
            max_tokens=1,
            stream=True,
        )
    )
    first = await anext(response.body_iterator)
    assert '"content": "OK"' in first
    assert "[DONE]" not in first
    await response.body_iterator.aclose()


def test_profile_uses_supported_settings_and_keeps_required_dependencies_enabled() -> None:
    profile = yaml.safe_load(
        (ROOT / "tests/performance/gateway_concurrency_profile.yaml").read_text()
    )
    settings = profile["general_settings"]
    assert not set(settings) - GeneralSettings.model_fields.keys()
    validated = GeneralSettings.model_validate(
        {
            key: value
            for key, value in settings.items()
            if not isinstance(value, str) or not value.startswith("os.environ/")
        }
    )
    assert validated.audit_enabled
    assert validated.audit_ingestion_mode == validated.spend_ingestion_mode == "outbox"
    assert validated.audit_ingestion_worker_enabled and validated.spend_ingestion_worker_enabled
    assert validated.gateway_preflight_capacity_enabled
    assert validated.redis_degraded_mode == "fail_closed"
    assert validated.budget_enforcement_query_mode == "legacy"


def test_only_known_error_codes_are_exported() -> None:
    assert (
        error_code({"error": {"code": "audit_persistence_unavailable"}})
        == "audit_persistence_unavailable"
    )
    for value in (
        "secret",
        {"code": "secret"},
        {"error": {"code": "secret"}},
        {"error": {"code": []}},
    ):
        assert error_code(value) == "unclassified_http_error"
    assert not valid_completion({"choices": [], "usage": {"completion_tokens": 1}})


def test_fixture_cannot_seed_arbitrary_databases_or_use_master_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://user:secret@remote.example/deltallm_concurrency"
    )
    with pytest.raises(ValueError, match="loopback"):
        fixture_database_url()
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@127.0.0.1/production")
    with pytest.raises(ValueError, match="named"):
        fixture_database_url()
    monkeypatch.setenv("DELTALLM_LOAD_API_KEY", "sk-concurrency-private")
    monkeypatch.setenv("DELTALLM_MASTER_KEY", "sk-concurrency-private")
    with pytest.raises(ValueError, match="distinct"):
        fixture_key()


def test_metrics_export_cannot_leak_arbitrary_names_or_labels() -> None:
    text = """
deltallm_http_requests_in_flight{route="chat_completions"} 2
deltallm_http_requests_in_flight{route="private-key"} 100
deltallm_http_requests_in_flight{route="chat_completions",tenant="private"} 200
deltallm_telemetry_acceptance_private_key 1
deltallm_request_phase_latency_seconds_bucket{route="chat_completions",phase="authentication",outcome="success",response_kind="unknown",le="+Inf"} 10
deltallm_event_loop_last_lag_seconds NaN
"""
    selected = metrics.select_metrics(text)
    assert len(selected) == 2
    assert "private" not in repr(selected)
    assert len(metrics.select_metrics(text, buckets=False)) == 1


def test_deadline_and_work_metrics_preserve_capacity_without_private_labels():
    selected = metrics.select_metrics("""
deltallm_bounded_work_in_flight{allocation="guardrail"} 8
deltallm_bounded_work_bytes{allocation="callback_sync"} 1024
deltallm_bounded_work_rejections_total{allocation="callback",reason="payload"} 2
deltallm_callback_outcomes_total{integration="custom",outcome="timeout"} 3
deltallm_request_deadline_expirations_total{response="started"} 1
deltallm_bounded_work_in_flight{allocation="private-tenant"} 99
deltallm_callback_outcomes_total{integration="custom",outcome="private-error"} 99
""")
    assert len(selected) == 5
    assert "private" not in repr(selected)
    for code in ("request_deadline_exceeded", "gateway_work_unavailable", "edge_unavailable"):
        assert error_code({"error": {"code": code}}) == code


def test_admission_metrics_and_errors_are_preserved_without_identity_labels() -> None:
    text = """
deltallm_ingress_active{allocation="inference"} 4
deltallm_ingress_rejections_total{allocation="inference",reason="gateway_ingress_full"} 10
deltallm_auth_fallback_events_total{phase="lookup",outcome="coalesced"} 3
deltallm_database_allocation_occupied{allocation="foreground"} 1
deltallm_database_allocation_events_total{allocation="telemetry_settlement",outcome="queue_timeout"} 2
deltallm_spend_ingestion_failures_total{stage="operation_receipt"} 3
deltallm_spend_ingestion_failures_total{stage="private-stage"} 99
deltallm_auth_fallback_tasks{api_key="private-key"} 99
deltallm_ingress_rejections_total{allocation="inference",reason="private-error"} 99
"""
    selected = metrics.select_metrics(text)
    assert len(selected) == 6
    assert "private" not in repr(selected)
    for code in ("gateway_ingress_full", "auth_fallback_unavailable", "database_unavailable"):
        assert workload.error_code({"error": {"code": code}}) == code


def test_accounting_metrics_preserve_bounded_diagnostic_labels() -> None:
    text = """
deltallm_accounting_queue_depth{queue="finalization"} 12
deltallm_accounting_failures_total{queue="finalization",phase="database",reason="pool_timeout"} 3
deltallm_accounting_database_call_seconds_count{operation="finalize_grant",outcome="error"} 4
deltallm_accounting_queue_wait_seconds_count{queue="reservation"} 5
deltallm_accounting_projection_backlog 7
deltallm_accounting_failures_total{queue="private",phase="database",reason="pool_timeout"} 99
deltallm_accounting_failures_total{queue="finalization",phase="database",reason="private"} 99
"""

    selected = metrics.select_metrics(text)

    assert len(selected) == 5
    assert "private" not in repr(selected)


def test_recovery_and_rollover_metrics_remain_in_the_qualification_evidence():
    text = """
deltallm_accounting_database_call_seconds_count{operation="recovery_roll_windows",outcome="error"} 1
deltallm_accounting_projection_actions_total{action="roll_windows",outcome="success"} 2
deltallm_accounting_projection_actions_total{action="expired_grants",outcome="success"} 3
deltallm_accounting_projection_actions_total{action="expired_operations",outcome="success"} 4
deltallm_accounting_projection_actions_total{action="settle_grants",outcome="success"} 5
deltallm_accounting_projection_actions_total{action="recovery_tick",outcome="unavailable"} 6
deltallm_accounting_projection_actions_total{action="private-scope",outcome="success"} 99
"""
    selected = metrics.select_metrics(text)
    assert len(selected) == 6
    assert "private" not in repr(selected)


@pytest.mark.asyncio
async def test_recorder_closes_and_records_failed_scrapes_without_urls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_client = httpx.AsyncClient

    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.port == 8001:
            return httpx.Response(503, text="private backend failure")
        return httpx.Response(
            200, text='deltallm_http_requests_in_flight{route="chat_completions"} 2\n'
        )

    def client(**kwargs: object) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(transport), **kwargs)

    monkeypatch.setattr(metrics.httpx, "AsyncClient", client)
    output = tmp_path / "metrics.jsonl"
    async with metrics.MetricsRecorder(
        ["http://127.0.0.1:8000/metrics", "http://127.0.0.1:8001/metrics"], output
    ) as recorder:
        await recorder.snapshot()
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(rows) == 6
    assert recorder.errors == 3
    assert recorder._task is not None and recorder._task.done()
    assert recorder._client is not None and recorder._client.is_closed
    assert "private" not in output.read_text()
    assert "127.0.0.1" not in output.read_text()
    assert all(row.get("error") == "scrape_failed" for row in rows if row["source"] == 1)


@pytest.mark.asyncio
async def test_recorder_attributes_accounting_worker_metrics_without_exporting_urls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_client = httpx.AsyncClient

    def client(**kwargs: object) -> httpx.AsyncClient:
        return real_client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    text="deltallm_accounting_projection_backlog 3\n"
                    if request.url.port == 8001
                    else "deltallm_event_loop_samplers 1\n",
                )
            ),
            **kwargs,
        )

    monkeypatch.setattr(metrics.httpx, "AsyncClient", client)
    output = tmp_path / "metrics.jsonl"
    async with metrics.MetricsRecorder(
        [
            MetricSource("http://127.0.0.1:8000/metrics", "api", 0),
            MetricSource("http://127.0.0.1:8001/metrics", "accounting_worker", 0),
        ],
        output,
    ) as recorder:
        await recorder.snapshot()

    rows = [json.loads(line) for line in output.read_text().splitlines()]
    worker_rows = [row for row in rows if row["source_role"] == "accounting_worker"]
    assert worker_rows and all(row["source_process"] == 0 for row in worker_rows)
    assert recorder.evidence()[1]["accounting_metrics_observed"] is True
    assert "127.0.0.1" not in output.read_text()


def test_manifest_forbids_credentials_and_hides_values_in_validation_errors() -> None:
    with pytest.raises(ValidationError) as caught:
        ServerManifest.model_validate({"api_key": "private-secret-value"})
    assert "private-secret-value" not in str(caught.value)


@pytest.mark.asyncio
async def test_dependency_diagnostics_are_bounded_and_do_not_export_query_values(
    tmp_path: Path,
) -> None:
    class Database:
        async def query_raw(self, query: str) -> list[dict[str, object]]:
            assert "pg_stat_activity" in query
            return [
                {
                    "connections": 12,
                    "waiting_lock": 2,
                    "reservation_calls": 100,
                    "private_query": "select private-secret",
                }
            ]

    class Redis:
        async def info(self) -> dict[str, object]:
            return {
                "connected_clients": 4,
                "total_commands_processed": 300,
                "private_key": "private-secret",
            }

    output = tmp_path / "dependencies.jsonl"
    async with DependencyDiagnosticsRecorder(Database(), Redis(), output, interval=60) as recorder:
        await recorder.snapshot()

    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(rows) == 3
    assert recorder.errors == 0
    assert rows[0]["postgresql"] == {
        "connections": 12,
        "reservation_calls": 100,
        "waiting_lock": 2,
    }
    assert rows[0]["redis"] == {
        "connected_clients": 4,
        "total_commands_processed": 300,
    }
    assert "private" not in output.read_text()


def test_kubernetes_resource_quantity_parser_rejects_cross_dimension_units() -> None:
    assert _quantity("250m", cpu=True) == 250
    assert _quantity("125000n", cpu=True) == 0.125
    assert _quantity("2Mi", cpu=False) == 2 * 1024 * 1024
    with pytest.raises(ValueError):
        _quantity("2Mi", cpu=True)
    with pytest.raises(ValueError):
        _quantity("private", cpu=False)


@pytest.mark.asyncio
async def test_kubernetes_resources_are_attributed_without_pod_names(
    tmp_path: Path,
) -> None:
    class Cluster:
        def kubectl(self, *args: str, timeout: float) -> object:
            assert timeout == 2
            if "--raw" not in args:
                payload = {
                    "items": [
                        {
                            "metadata": {
                                "name": "private-api-pod",
                                "labels": {"app.kubernetes.io/component": "api"},
                            }
                        },
                        {
                            "metadata": {
                                "name": "private-worker-pod",
                                "labels": {"app.kubernetes.io/component": "accounting-worker"},
                            }
                        },
                    ]
                }
            else:
                payload = {
                    "items": [
                        {
                            "metadata": {"name": "private-api-pod"},
                            "containers": [{"usage": {"cpu": "50m", "memory": "128Mi"}}],
                        },
                        {
                            "metadata": {"name": "private-worker-pod"},
                            "containers": [{"usage": {"cpu": "125m", "memory": "256Mi"}}],
                        },
                    ]
                }
            return type("Result", (), {"stdout": json.dumps(payload)})()

    output = tmp_path / "resources.jsonl"
    recorder = KubernetesResourceRecorder(
        Cluster(), output, required_roles={"api", "accounting_worker"}, interval=60
    )
    async with recorder:
        await recorder.snapshot()

    evidence = recorder.evidence()
    assert evidence["missing_required_roles"] == []
    assert evidence["snapshots"] == 3
    assert evidence["errors"] == 0
    assert "private" not in output.read_text()
    first = json.loads(output.read_text().splitlines()[0])
    assert first["sources"] == [
        {
            "cpu_millicores": 125.0,
            "memory_bytes": 256 * 1024 * 1024,
            "source_process": 0,
            "source_role": "accounting_worker",
        },
        {
            "cpu_millicores": 50.0,
            "memory_bytes": 128 * 1024 * 1024,
            "source_process": 0,
            "source_role": "api",
        },
    ]


@pytest.mark.asyncio
async def test_dependency_snapshot_deadline_cancels_a_stuck_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    released = asyncio.Event()

    class StuckDatabase:
        async def query_raw(self, query: str) -> None:
            del query
            try:
                await asyncio.Event().wait()
            finally:
                released.set()

    monkeypatch.setattr(workload, "DEPENDENCY_SNAPSHOT_TIMEOUT_SECONDS", 0)
    with pytest.raises(TimeoutError):
        await workload.dependency_counts(StuckDatabase(), None)
    assert released.is_set()


def test_published_samples_reproduce_the_historical_summary_and_contain_only_allowlisted_fields() -> (
    None
):
    directory = ROOT / "docs/project/benchmarks/concurrency-2026-09-11"
    manifest = json.loads((directory / "manifest.json").read_text())
    allowed = {
        "index",
        "status_code",
        "error_code",
        "start_offset_seconds",
        "completion_offset_seconds",
        "latency_seconds",
    }
    codes = {
        None,
        "audit_persistence_unavailable",
        "gateway_preflight_global_parallel_exceeded",
        "prompt_resolution_timeout",
        "unclassified_http_error",
    }
    for stage in manifest["stages"]:
        data = (directory / stage["samples"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == stage["sha256"]
        for line in data.splitlines():
            row = json.loads(line)
            assert set(row) == allowed
            assert row["error_code"] in codes
    assert summarize(directory) == json.loads((directory / "summary.json").read_text())
