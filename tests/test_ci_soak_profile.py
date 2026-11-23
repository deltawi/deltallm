from __future__ import annotations

from pathlib import Path
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def _profile_script() -> str:
    workflow = yaml.safe_load((ROOT / ".github/workflows/nightly-soak.yml").read_text())
    steps = workflow["jobs"]["soak"]["steps"]
    return next(step["run"] for step in steps if step.get("id") == "profile")


def _args(tmp_path: Path, larger_runner: str = "", **inputs: str) -> str:
    output = tmp_path / f"{larger_runner or 'hosted'}.txt"
    env = {
        "PATH": "/usr/bin:/bin",
        "LARGER_RUNNER": larger_runner,
        "BATCH_ITEMS": inputs.get("batch", ""),
        "API_RATE": inputs.get("rate", ""),
        "API_SECONDS": inputs.get("seconds", ""),
        "GITHUB_OUTPUT": str(output),
    }
    subprocess.run(["bash", "-c", _profile_script()], env=env, check=True)
    return output.read_text().strip().removeprefix("args=")


def test_soak_runs_by_hand_only():
    workflow = yaml.safe_load((ROOT / ".github/workflows/nightly-soak.yml").read_text())
    # PyYAML reads the bare `on:` key as True.
    assert set(workflow[True]) == {"workflow_dispatch"}
    rate = workflow[True]["workflow_dispatch"]["inputs"]["api-rate"]
    assert rate["default"] == "50"


def test_manual_soak_on_the_hosted_runner_relaxes_only_the_runner_bound_gates(tmp_path):
    hosted = _args(tmp_path, batch="20000", rate="50", seconds="1800")
    assert hosted.startswith("--batch-items 20000 --api-rate 50 --api-seconds 1800")
    assert "--max-p95-seconds 0.5" in hosted
    assert "--min-batch-items-per-second 30" in hosted


def test_manual_soak_on_a_larger_runner_keeps_every_target(tmp_path):
    larger = _args(tmp_path, "ubuntu-latest-16-cores", batch="5000", rate="300", seconds="0")
    assert larger == "--batch-items 5000 --api-rate 300 --api-seconds 0"


def test_manual_soak_rejects_inputs_that_are_not_whole_numbers(tmp_path):
    with pytest.raises(subprocess.CalledProcessError):
        _args(tmp_path, batch="5000", rate="fast", seconds="0")
