from __future__ import annotations

from pathlib import Path

import yaml


WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def _load(name: str) -> dict:
    # PyYAML reads the bare `on:` key as True.
    return yaml.safe_load((WORKFLOWS / name).read_text())


def test_accounting_v2_acceptance_is_advisory_only_in_ci():
    native = _load("native-acceptance.yml")
    advisory = native[True]["workflow_call"]["inputs"]["advisory"]
    assert advisory["type"] == "boolean" and advisory["default"] is False
    assert native["jobs"]["native-cluster"]["continue-on-error"] == "${{ inputs.advisory }}"

    ci = _load("ci.yml")
    assert ci["jobs"]["accounting-v2-acceptance"]["with"] == {
        "advisory": True,
        "pod_loss": "${{ github.event_name != 'pull_request' }}",
    }
    assert "accounting-v2-acceptance" not in ci["jobs"]["test"]["needs"]

    # The nightly run keeps failing on a red acceptance.
    nightly = _load("nightly-soak.yml")
    assert "with" not in nightly["jobs"]["accounting-v2-acceptance"]


def test_pull_requests_skip_only_the_pod_loss_wait():
    native = _load("native-acceptance.yml")
    pod_loss = native[True]["workflow_call"]["inputs"]["pod_loss"]
    assert pod_loss["type"] == "boolean" and pod_loss["default"] is True
    run = next(
        step["run"]
        for step in native["jobs"]["native-cluster"]["steps"]
        if "run_native_acceptance" in step.get("run", "")
    )
    assert "${{ !inputs.pod_loss && '--skip-pod-loss' || '' }}" in run
