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
from tests.test_accounting_allocator_bounds_postgres import (
    assert_bounded_allocator_plan,
    assert_bounded_window_plan,
)
from tests.test_accounting_read_model_frontier_postgres import HISTORY_INSERT_ROWS, retain


@pytest.mark.parametrize("start,end", [(1, 40_000), (40_001, 300_000), (7, 8)])
@pytest.mark.parametrize("analyze", [False, True])
async def test_frontier_history_setup_preserves_every_row_with_bounded_inserts(start, end, analyze):
    connection = SimpleNamespace(execute=AsyncMock())
    await retain(SimpleNamespace(_connection=connection), start, end, analyze)
    calls = connection.execute.await_args_list
    inserts = [call.args for call in calls if call.args[0].startswith("INSERT INTO")]
    assert HISTORY_INSERT_ROWS == 10_000
    assert inserts[0][1] == start and inserts[-1][2] == end
    assert sum(last - first + 1 for _, first, last in inserts) == end - start + 1
    for index, (query, first, last) in enumerate(inserts):
        assert 1 <= last - first + 1 <= HISTORY_INSERT_ROWS
        assert "repeat('x',512)" in query
        if index:
            assert first == inserts[index - 1][2] + 1
    assert calls[len(inserts)].args[1] == end
    assert [call.args[0] for call in calls[len(inserts) + 1 :]] == (
        ["ANALYZE deltallm_accounting_events", "ANALYZE deltallm_accounting_projection_checkpoints"]
        if analyze
        else []
    )


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


def test_capture_records_compilation_without_retaining_private_jit_fields():
    owner = AccountingPlanCapture(None)
    owner.capture(
        None,
        message({"Query Text": "private", "Plan": {}, "JIT": {"Functions": 7, "private": 1}}),
    )
    assert owner.plans[0].jit_functions == 7
    assert owner.plans[0].safe_report()["jit_functions"] == 7
    assert "private" not in json.dumps(owner.plans[0].safe_report())


@pytest.mark.parametrize("jit", [None, [], {"Functions": True}, {"Functions": -1}])
def test_capture_rejects_invalid_compilation_evidence(jit):
    owner = AccountingPlanCapture(None)
    owner.capture(None, message({"Query Text": "safe", "Plan": {}, "JIT": jit}))
    assert owner.plans == []
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
    module.asyncpg.connect.assert_awaited_once_with(
        "postgresql://localhost/disposable", timeout=5, command_timeout=5
    )
    commands = [call.args[0] for call in connection.execute.await_args_list]
    assert ("SET jit=off" in commands) == (planner == "alternate_join")
    assert any("plan_cache_mode=" in command for command in commands)
    assert any("enable_nestloop=off" in command for command in commands) == (
        planner == "alternate_join"
    )
    connection.close.assert_awaited_once_with(timeout=5)


@pytest.mark.parametrize("failure", ["sequential", "rows", "filtered", "bitmap"])
@pytest.mark.parametrize(
    "relation",
    [
        "deltallm_accounting_budget_windows",
        "deltallm_accounting_grants",
        "deltallm_accounting_grant_windows",
    ],
)
def test_allocator_bound_rejects_history_work_even_when_the_result_is_small(failure, relation):
    node = {
        "Node Type": "Index Scan",
        "Relation Name": relation,
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


def window_node(**fields):
    return {
        "Node Type": "Index Scan",
        "Relation Name": "deltallm_accounting_budget_windows",
        "Actual Loops": 1,
        "Actual Rows": 9,
        **fields,
    }


@pytest.mark.parametrize("node_type", ["Bitmap Heap Scan", "Seq Scan"])
def test_unused_window_branch_must_have_zero_rows_and_zero_buffer_work(node_type):
    unused = window_node(**{"Node Type": node_type, "Actual Loops": 0, "Actual Rows": 0})
    active = window_node()
    value = {"Node Type": "Append", "Actual Loops": 1, "Plans": [unused, active]}
    assert assert_bounded_window_plan(value, maximum_rows=9) == [active]


@pytest.mark.parametrize(
    "field", ["Actual Rows", "Rows Removed by Filter", "Shared Hit Blocks", "Temp Written Blocks"]
)
def test_unused_window_branch_cannot_hide_actual_work(field):
    unused = window_node(**{"Actual Loops": 0, "Actual Rows": 0, field: 1})
    value = {"Node Type": "Append", "Actual Loops": 1, "Plans": [unused, window_node()]}
    with pytest.raises(AssertionError):
        assert_bounded_window_plan(value, maximum_rows=9)


@pytest.mark.parametrize("fault", ["bitmap", "sequential", "rows", "filtered", "sorted"])
def test_executed_window_branch_keeps_the_original_history_limits(fault):
    active = window_node()
    if fault in {"bitmap", "sequential"}:
        active["Node Type"] = "Bitmap Heap Scan" if fault == "bitmap" else "Seq Scan"
    elif fault == "rows":
        active["Actual Rows"] = 50000
    elif fault == "filtered":
        active["Rows Removed by Filter"] = 50000
    else:
        active = {"Node Type": "Sort", "Actual Loops": 1, "Plans": [active]}
    with pytest.raises(AssertionError):
        assert_bounded_window_plan(active, maximum_rows=9)
