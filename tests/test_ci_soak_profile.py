from __future__ import annotations

from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _profile_script() -> str:
    workflow = yaml.safe_load((ROOT / ".github/workflows/nightly-soak.yml").read_text())
    steps = workflow["jobs"]["soak"]["steps"]
    return next(step["run"] for step in steps if step.get("id") == "profile")


def _args(tmp_path: Path, event: str, larger_runner: str = "", **inputs: str) -> str:
    output = tmp_path / f"{event}-{larger_runner or 'hosted'}.txt"
    env = {
        "PATH": "/usr/bin:/bin",
        "EVENT": event,
        "LARGER_RUNNER": larger_runner,
        "BATCH_ITEMS": inputs.get("batch", ""),
        "API_RATE": inputs.get("rate", ""),
        "API_SECONDS": inputs.get("seconds", ""),
        "GITHUB_OUTPUT": str(output),
    }
    subprocess.run(["bash", "-c", _profile_script()], env=env, check=True)
    return output.read_text().strip().removeprefix("args=")


def test_scheduled_soak_on_the_hosted_runner_uses_the_proven_rate(tmp_path):
    hosted = _args(tmp_path, "schedule").split()
    assert hosted[:2] == ["--api-rate", "50"]
    # The batch size and API length stay at their full defaults.
    assert "--batch-items" not in hosted and "--api-seconds" not in hosted
    assert "--max-p95-seconds" in hosted


def test_scheduled_soak_on_a_larger_runner_keeps_every_target(tmp_path):
    assert _args(tmp_path, "schedule", "ubuntu-latest-16-cores") == ""


def test_manual_and_pull_request_profiles_are_unchanged(tmp_path):
    manual = _args(tmp_path, "workflow_dispatch", batch="5000", rate="300", seconds="0")
    assert manual.startswith("--batch-items 5000 --api-rate 300 --api-seconds 0")
    pull = _args(tmp_path, "pull_request")
    assert pull.startswith("--batch-items 4000 --batch-timeout 900 --api-rate 50 --api-seconds 120")
