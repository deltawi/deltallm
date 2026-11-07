from __future__ import annotations

from pathlib import Path

import yaml


WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def test_every_workflow_that_runs_uv_keeps_the_lock_frozen():
    # A plain `uv run` re-checks uv.lock and downloads every direct-URL wheel in it,
    # including the 400 MB presidio model from github.com, so a GitHub 504 failed jobs
    # that never install that extra.
    checked = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        source = path.read_text()
        if "uv run" not in source:
            continue
        workflow = yaml.safe_load(source)
        assert workflow.get("env", {}).get("UV_FROZEN") == "1", path.name
        checked.append(path.name)
    assert "ci.yml" in checked
