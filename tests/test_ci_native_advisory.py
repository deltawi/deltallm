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
    assert ci["jobs"]["accounting-v2-acceptance"]["with"] == {"advisory": True}
    assert "accounting-v2-acceptance" not in ci["jobs"]["test"]["needs"]

    # The nightly run keeps failing on a red acceptance.
    nightly = _load("nightly-soak.yml")
    assert "with" not in nightly["jobs"]["accounting-v2-acceptance"]
