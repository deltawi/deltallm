from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

from src.config import GeneralSettings
from tests.performance import soak_checks as checks
from tests.performance.gateway_concurrency_mock import app
from tests.performance.native_batch_load import (
    CHAT_ENDPOINT,
    EMBEDDING_ENDPOINT,
    batch_rows,
    job_plan,
)
from tests.performance.native_fixture import (
    EMBEDDING_DEPLOYMENT,
    EMBEDDING_MODEL,
    native_values,
    parse_metrics,
)

REPOSITORY = Path(__file__).resolve().parents[1]


def scrape(pod: str, text: str, role: str = "batch_worker") -> dict:
    return {pod: {"role": role, "samples": parse_metrics(text)}}


BASELINE = """
# TYPE deltallm_accounting_queue_depth gauge
deltallm_accounting_queue_depth{queue="reservation"} 0
# TYPE deltallm_accounting_queue_retained_bytes gauge
deltallm_accounting_queue_retained_bytes{queue="reservation"} 128
# TYPE deltallm_accounting_failures_total counter
deltallm_accounting_failures_total{phase="queue",queue="reservation",reason="queue_full"} 2
# TYPE deltallm_batch_item_retries_total counter
deltallm_batch_item_retries_total{category="gateway_busy"} 1
"""


def test_leaked_store_fails_and_drained_store_passes() -> None:
    baseline = scrape("batch-a", BASELINE)
    leaked = scrape(
        "batch-a",
        BASELINE.replace('reservation"} 0', 'reservation"} 1100')
        + "# TYPE deltallm_accounting_local_receipt_bytes gauge\n"
        "deltallm_accounting_local_receipt_bytes 8388608\n",
    )
    result = checks.gauges_returned_to_idle(baseline, leaked)
    assert not result["passed"]
    assert {row["metric"] for row in result["above_baseline"]} == {
        "deltallm_accounting_queue_depth",
        "deltallm_accounting_local_receipt_bytes",
    }
    assert checks.gauges_returned_to_idle(baseline, scrape("batch-a", BASELINE))["passed"]


def test_replacement_pod_is_held_to_zero() -> None:
    baseline = scrape("batch-a", BASELINE)
    replacement = scrape("batch-b", BASELINE)
    result = checks.gauges_returned_to_idle(baseline, replacement)
    assert [row["metric"] for row in result["above_baseline"]] == [
        "deltallm_accounting_queue_retained_bytes"
    ]


def test_capacity_refusals_count_only_increases() -> None:
    baseline = scrape("batch-a", BASELINE)
    unchanged = checks.capacity_failures(baseline, baseline)
    assert unchanged["passed"] and unchanged["all_failures_by_reason"] == {}
    rising = scrape("batch-a", BASELINE.replace('reason="queue_full"} 2', 'reason="queue_full"} 9'))
    result = checks.capacity_failures(baseline, rising)
    assert not result["passed"]
    assert result["capacity_refusals"] == {"batch-a:queue_full": 7}
    assert result["all_failures_by_reason"] == {"queue_full": 7}


def test_batch_outcome_names_each_failure() -> None:
    jobs = [
        {"status": "completed", "request_counts": {"completed": 100, "failed": 0}},
        {"status": "failed", "request_counts": {"completed": 40, "failed": 60}},
    ]
    result = checks.batch_outcome(
        jobs, expected_items=200, elapsed_seconds=10, min_items_per_second=50
    )
    assert result["failures"] == [
        "job_not_completed",
        "items_failed",
        "item_count_mismatch",
        "throughput_below_target",
    ]
    good = checks.batch_outcome(
        jobs[:1], expected_items=100, elapsed_seconds=1, min_items_per_second=50
    )
    assert good["passed"] and good["items_per_second"] == 100


def api_report(**changes):
    report = {
        "target_count": 1000,
        "success_count": 1000,
        "status_counts": {"200": 1000},
        "error_counts": {},
        "latency_seconds": {"p95": 0.02},
    }
    report.update(changes)
    return report


@pytest.mark.parametrize(
    ("changes", "failure"),
    [
        ({"status_counts": {"200": 999, "503": 1}, "success_count": 999}, "server_errors"),
        ({"error_counts": {"ReadTimeout": 1}}, "transport_errors"),
        ({"success_count": 900}, "success_ratio_below_target"),
        ({"latency_seconds": {"p95": 0.08}}, "p95_above_target"),
        ({"latency_seconds": {"p95": None}}, "p95_above_target"),
    ],
)
def test_api_window_gates(changes, failure) -> None:
    assert checks.api_window(api_report(), max_p95_seconds=0.05)["passed"]
    result = checks.api_window(api_report(**changes), max_p95_seconds=0.05)
    assert failure in result["failures"] and not result["passed"]


def snapshot(facts: int, charge: str, **changes) -> dict:
    return {
        "facts": facts,
        "fact_charge": charge,
        "unsafe_windows": 0,
        "legacy_spend_rows": 0,
        **changes,
    }


def scopes(committed: str, reserved: str = "0") -> list[dict]:
    return [
        {"scope_type": name, "committed": committed, "reserved": reserved, "provisional": "0"}
        for name in ("api_key", "organization", "team", "user")
    ]


def test_ledger_requires_one_fact_per_success_and_closed_budgets() -> None:
    before = snapshot(10, "0.00007")
    after = snapshot(15, "0.000105")
    assert checks.ledger_matches(before, after, charged=5, duplicates=0, scopes=scopes("0.000105"))[
        "passed"
    ]
    failures = checks.ledger_matches(
        before,
        snapshot(16, "0.000112", legacy_spend_rows=1),
        charged=5,
        duplicates=1,
        scopes=scopes("0.000105", reserved="0.000007"),
    )["failures"]
    assert failures == [
        "fact_count_mismatch",
        "duplicate_charges",
        "budget_scopes_do_not_match_facts",
        "legacy_spend_rows_written",
    ]
    # A killed pod may charge answers its clients never received, never more.
    assert checks.ledger_matches(
        before, after, charged=4, duplicates=0, scopes=scopes("0.000105"), allowed_extra=1
    )["passed"]


def test_job_plan_alternates_endpoints_and_keeps_every_item() -> None:
    plan = job_plan(20_000, 12)
    assert sum(count for _, count in plan) == 20_000
    assert {count for _, count in plan} == {1666, 1667}
    assert [endpoint for endpoint, _ in plan[:2]] == [CHAT_ENDPOINT, EMBEDDING_ENDPOINT]
    rows = batch_rows(EMBEDDING_ENDPOINT, "job", 3)
    assert [row["custom_id"] for row in rows] == ["job-0", "job-1", "job-2"]
    assert rows[0]["body"]["model"] == EMBEDDING_MODEL
    # The lifecycle prompt sleeps 10 s in the mock; the soak must not use it.
    assert batch_rows(CHAT_ENDPOINT, "job", 1)[0]["body"]["messages"][0]["content"] == (
        "Soak batch fixture."
    )
    with pytest.raises(ValueError):
        job_plan(5, 12)


@pytest.mark.asyncio
async def test_mock_embeddings_answer_each_input() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        response = await client.post(
            "/v1/embeddings", json={"model": EMBEDDING_DEPLOYMENT, "input": ["a", "b", "c"]}
        )
        assert response.status_code == 200
        body = response.json()
        assert [row["index"] for row in body["data"]] == [0, 1, 2]
        assert body["usage"] == {"prompt_tokens": 3, "total_tokens": 3}
        single = await client.post(
            "/v1/embeddings", json={"model": EMBEDDING_DEPLOYMENT, "input": "a"}
        )
        assert len(single.json()["data"]) == 1
        assert (
            await client.post("/v1/embeddings", json={"model": "other", "input": "a"})
        ).status_code == 400


def test_native_values_apply_the_shipped_bundle_with_batch(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(REPOSITORY)
    calls = []
    cluster = SimpleNamespace(
        directory=SimpleNamespace(name=str(tmp_path)), output=tmp_path, apply=calls.append
    )
    path = native_values(cluster, "gateway:unit", api_replicas=2, batch_replicas=3)
    values = yaml.safe_load(path.read_text())
    native = yaml.safe_load(
        (REPOSITORY / "deploy/kubernetes/helm/values-accounting-native.yaml").read_text()
    )
    settings = values["config"]["general_settings"]
    for key, value in native["config"]["general_settings"].items():
        assert settings[key] == value
    assert not set(settings) - GeneralSettings.model_fields.keys()
    assert values["replicaCount"] == 2
    assert values["batchWorker"]["enabled"] and values["batchWorker"]["replicaCount"] == 3
    assert values["accountingRequest"]["replicaCount"] == 2
    assert values["accountingWorker"]["enabled"]
    assert [model["model_name"] for model in values["config"]["model_list"]] == [
        "concurrency-fixture",
        EMBEDDING_MODEL,
    ]
    assert {
        model["model_info"]["batch_capacity"]["max_in_flight"]
        for model in values["config"]["model_list"]
    } == {60}
    assert values["dependencyCapacity"]["extended"]["providerDomains"]["local-mock"][
        "modelIds"
    ] == ["fixed-one-token", EMBEDDING_DEPLOYMENT]
    assert settings["embeddings_batch_enabled"] and settings["embeddings_batch_max_attempts"] == 5
    assert calls and calls[0][0]["metadata"]["name"] == "deltallm-accounting-rpc"
    with pytest.raises(ValueError):
        native_values(cluster, "gateway:unit", api_replicas=2, batch_replicas=4)


@pytest.mark.asyncio
async def test_busy_batch_create_is_retried_and_recorded(monkeypatch) -> None:
    from tests.performance import native_batch_load

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(native_batch_load.asyncio, "sleep", no_sleep)
    answers = iter([503, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/files":
            return httpx.Response(200, json={"id": "file-1"})
        status = next(answers)
        return httpx.Response(status, json={"id": "batch-1"} if status == 200 else {"error": {}})

    retries: list[dict] = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://fixture"
    ) as http:
        batch_id = await native_batch_load.submit_job(
            http, CHAT_ENDPOINT, "job", 2, retries=retries
        )
    assert batch_id == "batch-1"
    assert [(row["path"], row["status"]) for row in retries] == [("/v1/batches", 503)]
