from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

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


def test_upgrade_fixture_supports_already_applied_routing_invariants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statements: list[str] = []

    def capture_execute(*_args: object, sql: str, **_kwargs: object) -> None:
        statements.append(sql)

    monkeypatch.setattr(verify_migration_paths, "_db_execute", capture_execute)

    verify_migration_paths._seed_upgrade_fixture(  # noqa: SLF001
        "prisma",
        "postgresql://localhost/upgrade",
        verify_migration_paths.CURRENT_SCHEMA,
    )

    assert len(statements) == 1
    sql = statements[0]
    assert "deltallm_routepolicy_one_published_per_group" in sql
    assert "THEN 'published'" in sql
    assert "ELSE 'archived'" in sql
    assert "to_regclass('public.deltallm_routeruntimestate')" in sql
    assert "route_groups_initialized = TRUE" in sql


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


def test_migration_verifier_checks_exact_reservations_and_deletion_guards(monkeypatch):
    statements = []
    monkeypatch.setattr(
        verify_migration_paths, "_db_execute", lambda *args, sql, **kwargs: statements.append(sql)
    )
    verify_migration_paths._verify_operation_reservations(
        "prisma", "postgresql://localhost/upgrade"
    )
    assert len(statements) == 1
    for invariant in (
        "reserved_spend_exact",
        "numeric_precision=38",
        "numeric_scale=18",
        "deltallm_recover_operation",
        "deltallm_recover_operation_isolated",
        "recovery_blocked_at",
        "recovery_error_code",
        "pg_get_constraintdef",
        "pg_get_expr",
        "deltallm_key_hold_delete_guard",
        "pending_count=0",
    ):
        assert invariant in statements[0]


@pytest.fixture
def selector_upgrade_verifier(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    verifier = verify_migration_paths
    base_schema = tmp_path / "base" / "schema.prisma"
    base_schema.parent.mkdir()
    base_schema.touch()
    state = SimpleNamespace(validated=False, installed_checks=0, final_checks=0)
    steps: list[str] = []
    created, dropped = Mock(), Mock()
    validation = Path("migrations") / verifier.SELECTOR_VALIDATION_MIGRATION / "migration.sql"

    def extract(base_ref: str, _destination: Path) -> Path:
        return base_schema if base_ref == "upgrade-base" else verifier.CURRENT_SCHEMA

    def migrate(_prisma: str, *, schema: Path, database_url: str) -> None:
        if database_url.endswith("_upgrade"):
            steps.append(
                "base"
                if schema == base_schema
                else ("current" if schema == verifier.CURRENT_SCHEMA else "staged")
            )
            # Deploy never undoes an already-applied validation migration.
            state.validated |= (schema.parent / validation).is_file()

    def execute(_prisma: str, *, schema: Path, database_url: str, sql: str) -> None:
        if "Batch checkpoint installation must commit before validation" in sql:
            assert not state.validated, "intermediate check cannot follow applied validation"
            state.installed_checks += 1
            steps.append("installed-check")
        if "Batch checkpoint validation did not finish" in sql:
            assert state.validated
            state.final_checks += 1
            steps.append("final-check")

    monkeypatch.setattr(verifier, "_extract_prisma_at_ref", extract)
    monkeypatch.setattr(verifier, "_migrate", migrate)
    monkeypatch.setattr(verifier, "_db_execute", execute)
    monkeypatch.setattr(verifier, "_create_database", created)
    monkeypatch.setattr(verifier, "_drop_database", dropped)
    return SimpleNamespace(
        schema=base_schema,
        validation=validation,
        state=state,
        steps=steps,
        created=created,
        dropped=dropped,
    )


@pytest.mark.parametrize("base_state", ["before-install", "installed", "validated"])
def test_upgrade_verifier_handles_selector_migration_already_in_base(
    selector_upgrade_verifier, base_state: str
) -> None:
    h = selector_upgrade_verifier
    if base_state != "before-install":
        install = (
            h.schema.parent / "migrations/20260909000100_batch_selector_checkpoint/migration.sql"
        )
        install.parent.mkdir(parents=True)
        install.touch()
    if base_state == "validated":
        validation = h.schema.parent / h.validation
        validation.parent.mkdir(parents=True)
        validation.touch()

    verify_migration_paths.verify_migration_paths(
        admin_url="postgresql://localhost/admin", base_ref="upgrade-base", prisma="unused"
    )

    intermediate = [] if base_state == "validated" else ["staged", "installed-check"]
    assert h.steps == ["base", *intermediate, "current", "final-check"]
    assert h.state.installed_checks == int(base_state != "validated")
    assert h.state.final_checks == 1
    assert h.created.call_count == h.dropped.call_count == 3
    assert h.dropped.call_args_list == list(reversed(h.created.call_args_list))


def test_upgrade_verifier_preserves_intermediate_failure_and_cleans_databases(
    selector_upgrade_verifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    verifier, h = verify_migration_paths, selector_upgrade_verifier
    execute = verifier._db_execute

    def fail_install_check(*args: object, sql: str, **kwargs: object) -> None:
        if "Batch checkpoint installation must commit before validation" in sql:
            raise RuntimeError("invalid staged installation")
        execute(*args, sql=sql, **kwargs)

    monkeypatch.setattr(verifier, "_db_execute", fail_install_check)
    with pytest.raises(RuntimeError, match="invalid staged installation"):
        verifier.verify_migration_paths(
            admin_url="postgresql://localhost/admin", base_ref="upgrade-base", prisma="unused"
        )
    assert h.state.final_checks == 0
    assert h.dropped.call_args_list == list(reversed(h.created.call_args_list))
