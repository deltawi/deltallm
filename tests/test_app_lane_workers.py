from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.app_lane_workers import use_ci_app_workers

pytest_plugins = ("pytester",)


@pytest.mark.parametrize(
    "override",
    [
        {},
        {"github_actions": None},
        {"github_actions": "false"},
        {"mark_expression": "hermetic"},
        {"mark_expression": "postgres"},
        {"mark_expression": "redis"},
        {"mark_expression": "helm"},
        {"mark_expression": "app or postgres"},
        {"mark_expression": ""},
        {"worker_id": "gw0"},
        {"collect_only": True},
        {"use_debugger": True},
        {"worker_count": 0},
        {"worker_count": 4},
        {"worker_count": "auto"},
        {"worker_transports": ["popen"]},
        {"xdist_enabled": False},
    ],
)
def test_only_ci_app_execution_uses_default_workers(override: dict) -> None:
    settings = {
        "github_actions": "true",
        "mark_expression": "app",
        "worker_id": None,
        "collect_only": False,
        "use_debugger": False,
        "worker_count": None,
        "worker_transports": [],
        "xdist_enabled": True,
    }
    assert use_ci_app_workers(**(settings | override)) is (not override)


@pytest.mark.parametrize("serial", [False, True])
def test_ci_workers_run_each_test_once_and_preserve_failure(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, serial: bool
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    monkeypatch.delenv("PYTEST_XDIST_WORKER_COUNT", raising=False)
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
    pytester.makeini("[pytest]\nmarkers = app: application tests")
    pytester.makepyfile(
        """
        import json
        import os
        import time
        from pathlib import Path
        import pytest

        @pytest.mark.app
        @pytest.mark.parametrize("case", range(8))
        def test_application(case):
            with Path(f"case-{case}.json").open("x") as output:
                json.dump(os.environ.get("PYTEST_XDIST_WORKER", "serial"), output)
            time.sleep(0.08)
            assert case != 7, "expected failure must reach the controller"
        """
    )
    arguments = ["-p", "tests.app_lane_workers", "-q", "-m", "app"]
    if serial:
        arguments.extend(["-n", "0"])
    result = pytester.runpytest_subprocess(*arguments, timeout=45)
    result.assert_outcomes(passed=7, failed=1)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    records = sorted(pytester.path.glob("case-*.json"))
    assert len(records) == 8
    worker_ids = {json.loads(record.read_text()) for record in records}
    assert worker_ids == ({"serial"} if serial else {"gw0", "gw1"})
