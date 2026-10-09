from __future__ import annotations

import os
from collections.abc import Generator

import pytest


def use_ci_app_workers(
    *,
    github_actions: str | None,
    mark_expression: str,
    worker_id: str | None,
    collect_only: bool,
    use_debugger: bool,
    worker_count: int | str | None,
    worker_transports: list[str],
    xdist_enabled: bool,
) -> bool:
    return (
        github_actions == "true"
        and mark_expression.strip() == "app"
        and worker_id is None
        and not collect_only
        and not use_debugger
        and worker_count is None
        and not worker_transports
        and xdist_enabled
    )


@pytest.hookimpl(wrapper=True)
def pytest_cmdline_main(config: pytest.Config) -> Generator[None, int | None, int | None]:
    # The wrapper runs before xdist builds its transports. Workers keep the
    # normal pytest configuration and cannot start more workers.
    if use_ci_app_workers(
        github_actions=os.environ.get("GITHUB_ACTIONS"),
        mark_expression=config.option.markexpr,
        worker_id=os.environ.get("PYTEST_XDIST_WORKER"),
        collect_only=config.option.collectonly,
        use_debugger=config.option.usepdb,
        worker_count=config.getoption("numprocesses", default=None),
        worker_transports=config.getoption("tx", default=[]),
        xdist_enabled=config.pluginmanager.hasplugin("xdist"),
    ):
        config.option.numprocesses = 2
    return (yield)
