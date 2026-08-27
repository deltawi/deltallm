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


def test_default_base_ref_prefers_environment_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIGRATION_TEST_BASE_REF", "v0.1.35")

    assert verify_migration_paths._default_base_ref() == "v0.1.35"  # noqa: SLF001


def test_default_base_ref_selects_latest_stable_tag_on_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MIGRATION_TEST_BASE_REF", raising=False)

    class Result:
        stdout = "v0.2.0-rc.1\nv0.1.37\nv0.1.36\nexperimental\n"

    def fake_run(command: list[str], **_kwargs: object) -> Result:
        assert command == [
            "git",
            "tag",
            "--merged",
            "origin/main",
            "--sort=-version:refname",
        ]
        return Result()

    monkeypatch.setattr(verify_migration_paths.subprocess, "run", fake_run)

    assert verify_migration_paths._default_base_ref() == "v0.1.37"  # noqa: SLF001


def test_default_base_ref_ignores_blank_environment_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIGRATION_TEST_BASE_REF", "   ")

    class Result:
        stdout = "v0.1.37\n"

    monkeypatch.setattr(
        verify_migration_paths.subprocess,
        "run",
        lambda *_args, **_kwargs: Result(),
    )

    assert verify_migration_paths._default_base_ref() == "v0.1.37"  # noqa: SLF001


def test_default_base_ref_fails_without_stable_main_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MIGRATION_TEST_BASE_REF", raising=False)

    class Result:
        stdout = "v0.2.0-rc.1\nexperimental\n"

    monkeypatch.setattr(
        verify_migration_paths.subprocess,
        "run",
        lambda *_args, **_kwargs: Result(),
    )

    with pytest.raises(RuntimeError, match="no stable release tag reachable from origin/main"):
        verify_migration_paths._default_base_ref()  # noqa: SLF001
