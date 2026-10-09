import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.db.accounting_cutover import require_legacy_work_drained


@pytest.mark.parametrize("lane", ["realtime", "spend", "selector", "batch"])
async def test_preparation_cannot_ignore_an_unsettled_legacy_writer(lane):
    db = MagicMock(query_raw=AsyncMock(return_value=[{"lane": lane}]))
    with pytest.raises(RuntimeError, match="all legacy billing work"):
        await require_legacy_work_drained(db)
    db.query_raw.assert_awaited_once_with(
        "SELECT lane FROM deltallm_accounting_pending_legacy_work()"
    )


async def test_preparation_can_continue_after_legacy_work_settles():
    db = MagicMock(query_raw=AsyncMock(return_value=[]))
    await require_legacy_work_drained(db)
    db.query_raw.assert_awaited_once()


def test_preparation_uses_the_one_database_activation_owner():
    script = Path(__file__).resolve().parents[1] / "scripts/prepare_accounting_v2.py"
    strings = [
        node.value
        for node in ast.walk(ast.parse(script.read_text()))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    assert "SELECT deltallm_activate_accounting_protocol_locked($1)" in strings
    assert not any(
        "SET state='active'" in value or "state='active',activated_at" in value for value in strings
    )
