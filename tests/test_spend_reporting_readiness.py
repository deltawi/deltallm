from __future__ import annotations

from datetime import UTC, datetime

import pytest

from scripts.check_spend_reporting_v2_readiness import (
    REQUIRED_COLUMNS,
    REQUIRED_BOOLEAN_DEFAULT_FALSE_COLUMNS,
    REQUIRED_INDEXES,
    REQUIRED_INDEX_COLUMNS,
    REQUIRED_MIGRATIONS,
    REQUIRED_TRIGGERS,
    build_readiness_report,
    collect_readiness,
)


def _complete_report() -> dict[str, object]:
    finished_at = datetime(2026, 8, 11, tzinfo=UTC)
    return build_readiness_report(
        migration_rows=[
            {"migration_name": name, "finished_at": finished_at, "rolled_back_at": None}
            for name in REQUIRED_MIGRATIONS
        ],
        column_rows=[
            {
                "table_name": value.split(".", 1)[0],
                "column_name": value.split(".", 1)[1],
                "data_type": "boolean" if value in REQUIRED_BOOLEAN_DEFAULT_FALSE_COLUMNS else "text",
                "is_nullable": "NO" if value in REQUIRED_BOOLEAN_DEFAULT_FALSE_COLUMNS else "YES",
                "column_default": "false" if value in REQUIRED_BOOLEAN_DEFAULT_FALSE_COLUMNS else None,
            }
            for value in REQUIRED_COLUMNS
        ],
        index_rows=[
            {
                "index_name": name,
                "indisvalid": True,
                "indisready": True,
                "index_definition": "CREATE INDEX ON public.table ("
                + ", ".join(f'"{column}"' for column in REQUIRED_INDEX_COLUMNS[name])
                + ")",
            }
            for name in REQUIRED_INDEXES
        ],
        trigger_rows=[
            {"trigger_name": name, "tgenabled": "O"}
            for name in REQUIRED_TRIGGERS
        ],
    )


def test_readiness_report_requires_every_database_compatibility_object() -> None:
    report = _complete_report()

    assert report["ready"] is True
    assert report["missing"] == {
        "migrations": [],
        "columns": [],
        "valid_indexes": [],
        "enabled_triggers": [],
    }


def test_readiness_report_rejects_failed_migrations_and_invalid_objects() -> None:
    report = build_readiness_report(
        migration_rows=[{
            "migration_name": "20260810120000_spend_log_cursor_indexes",
            "finished_at": None,
            "rolled_back_at": None,
        }],
        column_rows=[],
        index_rows=[{
            "index_name": "deltallm_spendlog_events_time_id_idx",
            "indisvalid": False,
            "indisready": True,
            "index_definition": 'CREATE INDEX ON public.table ("start_time", "id")',
        }],
        trigger_rows=[{
            "trigger_name": "deltallm_spend_event_owner_snapshot",
            "tgenabled": "D",
        }],
    )

    assert report["ready"] is False
    assert report["note"] == (
        "Database compatibility is incomplete. Keep spend_reporting_v2_enabled false."
    )
    assert report["missing"]["migrations"] == sorted(REQUIRED_MIGRATIONS)
    assert report["missing"]["columns"] == sorted(REQUIRED_COLUMNS)
    assert report["missing"]["valid_indexes"] == sorted(REQUIRED_INDEXES)
    assert report["missing"]["enabled_triggers"] == sorted(REQUIRED_TRIGGERS)


def test_readiness_report_rejects_a_valid_index_with_the_wrong_column_order() -> None:
    report = build_readiness_report(
        migration_rows=[],
        column_rows=[],
        index_rows=[{
            "index_name": "deltallm_spendlog_events_time_id_idx",
            "indisvalid": True,
            "indisready": True,
            "index_definition": 'CREATE INDEX ON public.table ("id", "start_time")',
        }],
        trigger_rows=[],
    )

    assert "deltallm_spendlog_events_time_id_idx" in report["missing"]["valid_indexes"]


def test_readiness_report_rejects_an_unsafe_snapshot_completeness_column() -> None:
    report = _complete_report()
    unsafe_columns = [
        {
            "table_name": value.split(".", 1)[0],
            "column_name": value.split(".", 1)[1],
            "data_type": "boolean" if value in REQUIRED_BOOLEAN_DEFAULT_FALSE_COLUMNS else "text",
            "is_nullable": "YES" if value in REQUIRED_BOOLEAN_DEFAULT_FALSE_COLUMNS else "YES",
            "column_default": "true" if value in REQUIRED_BOOLEAN_DEFAULT_FALSE_COLUMNS else None,
        }
        for value in REQUIRED_COLUMNS
    ]
    rebuilt = build_readiness_report(
        migration_rows=[
            {"migration_name": name, "finished_at": datetime.now(tz=UTC), "rolled_back_at": None}
            for name in REQUIRED_MIGRATIONS
        ],
        column_rows=unsafe_columns,
        index_rows=[],
        trigger_rows=[],
    )

    assert report["ready"] is True
    assert rebuilt["missing"]["columns"] == sorted(REQUIRED_BOOLEAN_DEFAULT_FALSE_COLUMNS)


@pytest.mark.asyncio
async def test_readiness_queries_are_fixed_and_read_only() -> None:
    class FakeDB:
        def __init__(self) -> None:
            self.queries: list[str] = []

        async def query_raw(self, query: str):
            self.queries.append(query)
            return []

    db = FakeDB()

    report = await collect_readiness(db)

    assert report["ready"] is False
    assert len(db.queries) == 4
    assert all(query.lstrip().upper().startswith("SELECT") for query in db.queries)
