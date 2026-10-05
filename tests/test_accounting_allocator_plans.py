import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.performance.accounting_allocator_plans import (
    AccountingPlanCapture,
    CapturedAccountingPlan,
    capture_accounting_plans,
)
from tests.test_accounting_allocator_bounds_postgres import assert_bounded_allocator_plan


def message(value):
    return SimpleNamespace(message="duration: 0.1 ms  plan:\n" + json.dumps(value))


def test_plan_report_drops_query_values_and_sensitive_expressions():
    plan = CapturedAccountingPlan(
        "SELECT 'private-key'",
        {
            "Node Type": "Index Scan",
            "Index Name": "safe-primary-key",
            "Actual Rows": 1,
            "Index Cond": "token='private-key'",
            "Output": ["private-owner"],
            "Plans": [{"Node Type": "Result", "Filter": "private-fence"}],
        },
    )
    value = plan.safe_report()
    serialized = json.dumps(value)
    assert "private" not in serialized
    assert len(value["query_sha256"]) == 64
    assert value["plan"]["Plans"] == [{"Node Type": "Result"}]
    assert "private" not in repr(plan)


@pytest.mark.parametrize("bound", ["count", "bytes"])
def test_capture_rejects_excess_without_retaining_more_plans(bound):
    owner = AccountingPlanCapture(None)
    if bound == "count":
        owner.plans = [CapturedAccountingPlan("safe", {}) for _ in range(512)]
    else:
        owner._bytes = 67_108_864
    before = len(owner.plans)
    for _ in range(3):
        owner.capture(None, message({"Query Text": "safe", "Plan": {}}))
    assert len(owner.plans) == before
    assert owner.errors == ["plan capture exceeded its bound"]


def test_capture_reports_invalid_input_and_does_not_ignore_an_incomplete_profile():
    owner = AccountingPlanCapture(None)
    owner.capture(None, message({"Query Text": "safe", "Plan": {"Node Type": "Result"}}))
    assert len(owner.plans) == 1
    for _ in range(3):
        owner.capture(None, SimpleNamespace(message="plan:\nnot JSON"))
    assert owner.errors == ["plan capture returned invalid JSON"]


@pytest.mark.parametrize("fault", ["load", "cancel"])
async def test_diagnostic_connection_closes_after_setup_failure_or_cancellation(monkeypatch, fault):
    from tests.performance import accounting_allocator_plans as module

    connection = MagicMock(execute=AsyncMock(), close=AsyncMock())
    monkeypatch.setattr(module.asyncpg, "connect", AsyncMock(return_value=connection))
    if fault == "load":
        connection.execute.side_effect = RuntimeError("module unavailable")
        error = RuntimeError
    else:
        error = asyncio.CancelledError
    with pytest.raises(error):
        async with capture_accounting_plans("postgresql://localhost/disposable"):
            raise asyncio.CancelledError
    connection.close.assert_awaited_once_with(timeout=5)


@pytest.mark.parametrize("planner", ["auto", "generic", "custom", "alternate_join"])
async def test_planner_probe_is_scoped_to_its_owned_connection(monkeypatch, planner):
    from tests.performance import accounting_allocator_plans as module

    connection = MagicMock(execute=AsyncMock(), close=AsyncMock())
    monkeypatch.setattr(module.asyncpg, "connect", AsyncMock(return_value=connection))
    async with capture_accounting_plans("postgresql://localhost/disposable", planner=planner):
        pass
    commands = [call.args[0] for call in connection.execute.await_args_list]
    assert ("SET jit=off" in commands) == (planner == "alternate_join")
    assert any("plan_cache_mode=" in command for command in commands)
    assert any("enable_nestloop=off" in command for command in commands) == (
        planner == "alternate_join"
    )
    connection.close.assert_awaited_once_with(timeout=5)


@pytest.mark.parametrize("failure", ["sequential", "rows", "filtered", "bitmap"])
def test_allocator_bound_rejects_history_work_even_when_the_result_is_small(failure):
    node = {
        "Node Type": "Index Scan",
        "Relation Name": "deltallm_accounting_budget_windows",
        "Actual Loops": 1,
        "Actual Rows": 1,
    }
    if failure == "sequential":
        node["Node Type"] = "Seq Scan"
    elif failure == "rows":
        node["Actual Rows"] = 50002
    elif failure == "filtered":
        node["Rows Removed by Filter"] = 50000
    else:
        node["Node Type"] = "Bitmap Heap Scan"
        node["Plans"] = [
            {"Node Type": "Bitmap Index Scan", "Actual Loops": 1, "Actual Rows": 50000}
        ]
    entry = CapturedAccountingPlan("SELECT 'private-owner'", node)
    with pytest.raises(AssertionError) as error:
        assert_bounded_allocator_plan(entry)
    assert "private-owner" not in str(error.value)


def test_allocator_bound_accepts_only_bounded_indexed_bitmap_work():
    entry = CapturedAccountingPlan(
        "safe",
        {
            "Node Type": "Bitmap Heap Scan",
            "Relation Name": "deltallm_accounting_budget_windows",
            "Actual Loops": 1,
            "Actual Rows": 1,
            "Plans": [{"Node Type": "Bitmap Index Scan", "Actual Loops": 1, "Actual Rows": 1}],
        },
    )
    assert assert_bounded_allocator_plan(entry) == {"deltallm_accounting_budget_windows"}
