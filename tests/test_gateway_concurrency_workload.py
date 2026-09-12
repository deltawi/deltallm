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
from tests.performance import run_gateway_concurrency as workload
from tests.performance.gateway_concurrency_fixture import fixture_database_url, fixture_key
from tests.performance.gateway_concurrency_manifest import ServerManifest
from tests.performance.gateway_concurrency_mock import app
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
        for changed in ({"model": "other"}, {"max_tokens": 2}, {"stream": True}):
            assert (
                await client.post("/v1/chat/completions", json={**body, **changed})
            ).status_code == 400


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


def test_manifest_forbids_credentials_and_hides_values_in_validation_errors() -> None:
    with pytest.raises(ValidationError) as caught:
        ServerManifest.model_validate({"api_key": "private-secret-value"})
    assert "private-secret-value" not in str(caught.value)


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
