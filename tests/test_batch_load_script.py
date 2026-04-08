from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "measure_embedding_batch_load.py"
_SCRIPT_SPEC = importlib.util.spec_from_file_location("measure_embedding_batch_load", _SCRIPT_PATH)
assert _SCRIPT_SPEC is not None and _SCRIPT_SPEC.loader is not None
_SCRIPT_MODULE = importlib.util.module_from_spec(_SCRIPT_SPEC)
sys.modules[_SCRIPT_SPEC.name] = _SCRIPT_MODULE
_SCRIPT_SPEC.loader.exec_module(_SCRIPT_MODULE)


def test_validate_database_url_rejects_non_local_host_without_force() -> None:
    with pytest.raises(RuntimeError, match="non-local database host"):
        _SCRIPT_MODULE.validate_database_url(
            "postgresql://postgres:postgres@db.internal:5432/deltallm",
            force=False,
        )


def test_validate_database_url_rejects_non_deltallm_database_without_force() -> None:
    with pytest.raises(RuntimeError, match="outside the deltallm local/dev naming pattern"):
        _SCRIPT_MODULE.validate_database_url(
            "postgresql://postgres:postgres@localhost:5432/analytics",
            force=False,
        )


def test_validate_database_url_allows_local_deltallm_database() -> None:
    _SCRIPT_MODULE.validate_database_url(
        "postgresql://postgres:postgres@localhost:5432/deltallm",
        force=False,
    )


@pytest.mark.asyncio
async def test_cleanup_run_data_scopes_deletes_by_api_key() -> None:
    class _FakeDB:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def execute_raw(self, sql: str, *params):
            self.calls.append((sql, params))

    db = _FakeDB()

    await _SCRIPT_MODULE.cleanup_run_data(db, created_by_api_key="load-script-1")

    assert len(db.calls) == 3
    for sql, params in db.calls:
        assert params == ("load-script-1",)
        assert "created_by_api_key = $1" in sql


@pytest.mark.asyncio
async def test_run_measurement_preserves_schema_failure_without_running_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakePrisma:
        def __init__(self, datasource):  # noqa: ANN001
            self.datasource = datasource
            self.connected = False
            self.disconnected = False

        async def connect(self) -> None:
            self.connected = True

        async def disconnect(self) -> None:
            self.disconnected = True

    cleanup_calls = {"count": 0}

    async def _fail_ensure_batch_schema(db) -> None:  # noqa: ANN001
        raise RuntimeError("schema missing")

    async def _cleanup_run_data(db, *, created_by_api_key: str) -> None:  # noqa: ANN001
        del created_by_api_key
        cleanup_calls["count"] += 1

    monkeypatch.setattr(_SCRIPT_MODULE, "Prisma", _FakePrisma)
    monkeypatch.setattr(_SCRIPT_MODULE, "ensure_batch_schema", _fail_ensure_batch_schema)
    monkeypatch.setattr(_SCRIPT_MODULE, "cleanup_run_data", _cleanup_run_data)

    args = SimpleNamespace(
        database_url="postgresql://postgres:postgres@localhost:5432/deltallm",
        force=False,
    )

    with pytest.raises(RuntimeError, match="schema missing"):
        await _SCRIPT_MODULE.run_measurement(args)

    assert cleanup_calls["count"] == 0
