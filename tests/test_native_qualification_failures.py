"""Failed qualification evidence stays bounded and cannot alter a pass."""

import asyncio
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tests.performance.native_qualification_failures import (
    QualificationStageStopped,
    capture_unsettled_operations,
    record_stage_result,
)


def capture_database(rows):
    tx = SimpleNamespace(execute_raw=AsyncMock(), query_raw=AsyncMock(return_value=rows))

    @asynccontextmanager
    async def transaction(**options):
        assert options["max_wait"].total_seconds() == 1
        assert options["timeout"].total_seconds() == 4
        yield tx

    return SimpleNamespace(tx=transaction), tx


def unsettled_row():
    return {
        "operation_id": str(uuid4()),
        "accounting_state": "provisional",
        "max_scope_reserved_exact": "0.000000000000000000",
        "max_scope_provisional_exact": "1.000000000000000000",
        "journal_outcome": "uncertain",
        "journal_status": "completed",
        "uncertainty_class": "unknown",
    }


@pytest.mark.parametrize("count", [0, 1, 64, 65])
async def test_capture_is_read_only_and_marks_truncation(count):
    db, tx = capture_database([unsettled_row() for _ in range(count)])
    result = await capture_unsettled_operations(db)
    assert result["available"]
    assert len(result["operations"]) == min(count, 64)
    assert result["truncated"] is (count == 65)
    assert [call.args[0] for call in tx.execute_raw.call_args_list] == [
        "SET TRANSACTION READ ONLY",
        "SET LOCAL statement_timeout = '2000ms'",
        "SET LOCAL lock_timeout = '250ms'",
    ]
    sql, generation = tx.query_raw.call_args.args
    assert "LIMIT 65" in sql and generation == 1


@pytest.mark.parametrize(
    "change",
    [{"api_key": "private-key"}, {"uncertainty_class": "secret"}, {"operation_id": "tenant-id"}],
)
async def test_capture_rejects_private_or_untyped_rows(change):
    db, _ = capture_database([{**unsettled_row(), **change}])
    result = await capture_unsettled_operations(db)
    assert result == {
        "available": False,
        "error": "unsettled_capture_unavailable",
        "operations": None,
    }
    assert "secret" not in repr(result) and "private-key" not in repr(result)


async def test_capture_failure_remains_unknown_not_empty():
    db, tx = capture_database([])
    tx.query_raw.side_effect = RuntimeError("private database details")
    assert (await capture_unsettled_operations(db))["operations"] is None


async def test_capture_cancellation_propagates():
    db, tx = capture_database([])
    tx.query_raw.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await capture_unsettled_operations(db)


async def test_capture_overflow_remains_unknown():
    db, _ = capture_database([unsettled_row() for _ in range(66)])
    assert not (await capture_unsettled_operations(db))["available"]


@pytest.mark.parametrize("stopped", [False, True])
async def test_result_is_published_before_a_stage_stop(tmp_path, stopped):
    results = [{"rate": 200, "passed": True}]
    report = {"rate": 500, "passed": not stopped}
    failure = QualificationStageStopped("Accounting did not drain", report)
    run = AsyncMock(side_effect=failure) if stopped else AsyncMock(return_value=report)
    if stopped:
        with pytest.raises(QualificationStageStopped) as caught:
            await record_stage_result(run, output=tmp_path, proof={"passed": True}, results=results)
        assert caught.value is failure
    else:
        assert (
            await record_stage_result(run, output=tmp_path, proof={"passed": True}, results=results)
            is report
        )
    assert results[-1] is report
    assert json.loads((tmp_path / "results.json").read_text()) == {
        "generator_proof": {"passed": True},
        "runs": results,
    }


async def test_interrupted_stage_does_not_publish_a_made_up_result(tmp_path):
    with pytest.raises(asyncio.CancelledError):
        await record_stage_result(
            AsyncMock(side_effect=asyncio.CancelledError()),
            output=tmp_path,
            proof={},
            results=[],
        )
    assert not (tmp_path / "results.json").exists()
