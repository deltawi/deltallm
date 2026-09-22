from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.bootstrap.organization_deletion import require_organization_deletion_readiness
from src.bootstrap.organization_deletion import _report_background_task_exit


@pytest.mark.asyncio
async def test_disabled_requests_do_not_run_expensive_readiness_scan(monkeypatch) -> None:  # noqa: ANN001
    verify = AsyncMock()
    monkeypatch.setattr(
        "src.bootstrap.organization_deletion.verify_readiness",
        verify,
    )

    await require_organization_deletion_readiness(object(), requests_enabled=False)

    verify.assert_not_awaited()


@pytest.mark.asyncio
async def test_enabled_requests_fail_closed_until_database_is_ready(monkeypatch) -> None:  # noqa: ANN001
    verify = AsyncMock(return_value={"ready": False})
    monkeypatch.setattr(
        "src.bootstrap.organization_deletion.verify_readiness",
        verify,
    )

    with pytest.raises(RuntimeError, match="database readiness is incomplete"):
        await require_organization_deletion_readiness(object(), requests_enabled=True)

    verify.assert_awaited_once()


@pytest.mark.parametrize(
    "expected_stop,crash,reported", [(False, False, True), (True, False, False), (True, True, True)]
)
async def test_worker_exit_reporting_distinguishes_drain_from_failure(
    caplog, expected_stop, crash, reported
):
    async def worker():
        if crash:
            raise RuntimeError("worker failed")

    task = asyncio.create_task(worker())
    await asyncio.gather(task, return_exceptions=True)
    _report_background_task_exit("organization deletion worker", task, expected_stop=expected_stop)
    assert bool(caplog.records) is reported
