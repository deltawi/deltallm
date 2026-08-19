from __future__ import annotations

import pytest

from scripts import verify_migration_paths
from scripts.verify_migration_paths import database_url_for


def test_database_url_for_replaces_only_database_path() -> None:
    result = database_url_for(
        "postgresql://user:password@db.example:5432/postgres?sslmode=require",
        "deltallm_migration_verify_abc123_fresh",
    )

    assert result == (
        "postgresql://user:password@db.example:5432/"
        "deltallm_migration_verify_abc123_fresh?sslmode=require"
    )


@pytest.mark.parametrize(
    "database_name",
    (
        "postgres",
        "deltallm_migration_verify_valid;DROP DATABASE postgres",
        "deltallm_migration_verify_VALID",
    ),
)
def test_database_url_for_rejects_unsafe_database_names(database_name: str) -> None:
    with pytest.raises(ValueError, match="unsafe temporary database name"):
        database_url_for("postgresql://localhost/postgres", database_name)


@pytest.mark.parametrize(
    "database_url",
    (
        "mysql://localhost/database",
        "postgresql:///database",
        "not-a-url",
    ),
)
def test_database_url_for_requires_hosted_postgresql_url(database_url: str) -> None:
    with pytest.raises(ValueError, match="PostgreSQL URL with a host"):
        database_url_for(
            database_url,
            "deltallm_migration_verify_abc123_upgrade",
        )


def test_drop_database_uses_non_transactional_statements(monkeypatch: pytest.MonkeyPatch) -> None:
    statements: list[str] = []

    def capture_execute(*_args: object, sql: str, **_kwargs: object) -> None:
        statements.append(sql)

    monkeypatch.setattr(verify_migration_paths, "_db_execute", capture_execute)

    verify_migration_paths._drop_database(  # noqa: SLF001
        "prisma",
        "postgresql://localhost/postgres",
        "deltallm_migration_verify_abc123_upgrade",
    )

    assert len(statements) == 2
    assert statements[0].startswith("SELECT pg_terminate_backend")
    assert statements[1] == ('DROP DATABASE IF EXISTS "deltallm_migration_verify_abc123_upgrade";')
