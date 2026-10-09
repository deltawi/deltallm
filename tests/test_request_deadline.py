from __future__ import annotations

import pytest

from src.models.errors import TimeoutError
from src.router.execution import RequestDeadline


@pytest.mark.parametrize("limit", [0, -1])
async def test_exhausted_attempt_budget_does_not_start_work(limit: float) -> None:
    started = False

    async def work() -> str:
        nonlocal started
        started = True
        return "result"

    with pytest.raises(TimeoutError, match="Request deadline exceeded"):
        await RequestDeadline.after(10).wait_for(work(), limit=limit)

    assert not started
