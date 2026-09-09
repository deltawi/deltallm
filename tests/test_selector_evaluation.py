import json
from decimal import Decimal
from time import perf_counter

import pytest
from pydantic import ValidationError

from src.services.selector_evaluation import SelectorEvaluationRequest, evaluate_selector


def fixture_payload():
    return {
        "selector": {
            "kind": "llm-tier",
            "classifier_deployment_id": "mini",
            "lanes": [
                {"id": "economy", "rank": 0, "description": "Routine"},
                {"id": "quality", "rank": 1, "description": "Complex"},
            ],
        },
        "samples": [{"expected_lane": "economy", "output": '{"lane":"economy"}'}],
    }


def evaluate(payload):
    return evaluate_selector(SelectorEvaluationRequest.model_validate_json(json.dumps(payload)))


def test_replay_has_no_io_and_distinguishes_lane_agreement_from_answer_quality():
    report = evaluate(fixture_payload())
    assert report.basis == "supplied_fixture_replay"
    assert report.correct_count == report.sample_count == 1
    assert report.default_count == 0
    assert report.lanes[0].precision == report.lanes[0].recall == 1
    assert report.lanes[1].precision is None
    assert report.lanes[1].recall is None
    assert report.answer_quality_mean is None
    assert report.latency_p95_ms is None
    assert report.costs.net_savings_exact is None and report.costs.partial
    assert report.costs.selector_provider_cost_count == 0


@pytest.mark.parametrize(
    "output,cause",
    [
        ('{"lane":"unknown"}', "unknown_lane"),
        ("not json", "invalid_json"),
        ('{"lane":"economy","explanation":"private"}', "invalid_json"),
        ('{"lane":"economy","lane":"quality"}', "invalid_json"),
        ("x" * 257, "output_too_large"),
    ],
)
def test_replay_uses_runtime_parser_and_highest_default(output, cause):
    payload = fixture_payload()
    payload["samples"] = [{"expected_lane": "economy", "output": output}]
    report = evaluate(payload)
    assert report.default_count == 1 and report.default_rate == 1
    assert report.failure_counts == {cause: 1}
    assert report.confusion[0].selected_lane == "quality"
    assert output not in report.model_dump_json()


def test_replay_includes_failures_partial_timing_and_explicit_quality_coverage():
    payload = fixture_payload()
    payload["samples"] += [
        {
            "expected_lane": "quality",
            "failure": "selector_timeout",
            "latency_ms": 750,
            "answer_quality_score": 0.8,
        },
        {"expected_lane": "quality", "output": '{"lane":"quality"}', "latency_ms": 100},
    ]
    report = evaluate(payload)
    assert report.correct_count == 3
    assert report.default_count == 1
    assert report.latency_sample_count == 2
    assert report.latency_p50_ms == 100 and report.latency_p95_ms == 750
    assert report.answer_quality_sample_count == 1 and report.answer_quality_mean == 0.8


def test_supplied_costs_are_exact_and_negative_savings_remain_visible():
    payload = fixture_payload()
    payload["samples"][0]["costs"] = {
        "selector_provider_cost": "0.01",
        "selector_customer_charge": "0.01",
        "answer_provider_cost": "0.02",
        "answer_customer_charge": "0.04",
        "baseline_answer_provider_cost": "0.01",
        "measurable_penalty": "0.000000000000000001",
    }
    report = evaluate(payload)
    assert Decimal(report.costs.net_savings_exact) == Decimal("-0.020000000000000001")
    assert report.costs.complete_cost_count == 1 and not report.costs.partial


@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_lane", "absent"),
        ("latency_ms", -1),
        ("latency_ms", float("nan")),
        ("answer_quality_score", 1.1),
        ("output", "x" * 1025),
        ("prompt", "x" * 32769),
        ("failure", "provider_error"),
        ("unsupported", "sensitive"),
    ],
)
def test_fixture_fields_and_one_outcome_are_bounded(field, value):
    payload = fixture_payload()
    payload["samples"][0][field] = value
    with pytest.raises(ValidationError):
        evaluate(payload)


@pytest.mark.parametrize("count", [0, 101])
def test_sample_count_is_bounded(count):
    payload = fixture_payload()
    payload["samples"] *= count
    with pytest.raises(ValidationError):
        evaluate(payload)


def test_total_fixture_payload_is_bounded_even_for_individually_valid_prompts():
    payload = fixture_payload()
    payload["samples"] = [{**payload["samples"][0], "prompt": "x" * 32000} for _ in range(10)]
    with pytest.raises(ValidationError):
        evaluate(payload)


def test_maximum_replay_is_deterministic_and_does_not_expose_prompt_or_output():
    payload = fixture_payload()
    payload["samples"] = [
        {**payload["samples"][0], "prompt": "sensitive fixture"} for _ in range(100)
    ]
    request = SelectorEvaluationRequest.model_validate_json(json.dumps(payload))
    start = perf_counter()
    reports = [evaluate_selector(request).model_dump_json() for _ in range(5)]
    print(f"100-sample replay mean_ms={(perf_counter() - start) * 200:.3f}")
    assert len(set(reports)) == 1
    assert "sensitive fixture" not in reports[0]
    assert request.samples[0].output not in reports[0]


def test_cli_has_bounded_read_and_sanitized_failures(tmp_path, capsys, monkeypatch):
    from src.services.selector_evaluation_cli import main

    path = tmp_path / "fixtures.json"
    path.write_text(json.dumps(fixture_payload()))
    monkeypatch.setattr("sys.argv", ["selector_evaluation_cli", str(path)])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["sample_count"] == 1
    path.write_text('{"private":"must not leak"')
    assert main() == 2
    captured = capsys.readouterr()
    assert "Invalid or unreadable" in captured.err
    assert "must not leak" not in captured.err
