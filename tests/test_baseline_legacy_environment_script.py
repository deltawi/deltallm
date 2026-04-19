from __future__ import annotations

import importlib.util
from io import StringIO
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "prisma" / "baseline_legacy_environment.py"
_SCRIPT_SPEC = importlib.util.spec_from_file_location("baseline_legacy_environment", _SCRIPT_PATH)
assert _SCRIPT_SPEC is not None and _SCRIPT_SPEC.loader is not None
_SCRIPT_MODULE = importlib.util.module_from_spec(_SCRIPT_SPEC)
sys.modules[_SCRIPT_SPEC.name] = _SCRIPT_MODULE
_SCRIPT_SPEC.loader.exec_module(_SCRIPT_MODULE)


def _inspection(
    *,
    database_name: str = "deltallm",
    migrations_table_exists: bool = False,
    public_tables: tuple[str, ...] = ("deltallm_usertable",),
    migration_history: tuple[object, ...] = (),
    repo_migration_names: tuple[str, ...] = ("20260301090000_core_schema_baseline",),
):
    return _SCRIPT_MODULE.InspectionResult(
        database_name=database_name,
        migrations_table_exists=migrations_table_exists,
        public_tables=public_tables,
        migration_history=migration_history,
        repo_migration_names=repo_migration_names,
    )


def _history_entry(name: str, *, finished: bool = True, rolled_back: bool = False):
    return _SCRIPT_MODULE.MigrationHistoryEntry(
        migration_name=name,
        finished=finished,
        rolled_back=rolled_back,
    )


def _completed_process(*, returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["uv", "run", "prisma"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _create_migrations_dir(
    root: Path,
    names: tuple[str, ...] = ("20260301090000_core_schema_baseline",),
) -> Path:
    migrations_dir = root / "migrations"
    for name in names:
        migration_dir = migrations_dir / name
        migration_dir.mkdir(parents=True)
        (migration_dir / "migration.sql").write_text("-- migration", encoding="utf-8")
    return migrations_dir


def test_classify_inspection_reports_legacy_unbaselined_for_existing_tables_without_history() -> None:
    inspection = _inspection(migrations_table_exists=False, public_tables=("deltallm_usertable",))

    classification = _SCRIPT_MODULE.classify_inspection(
        inspection,
        schema_path=Path("./prisma/schema.prisma"),
    )

    assert classification.kind == "legacy_unbaselined"
    assert classification.eligible_for_baseline is True


def test_classify_inspection_reports_legacy_unbaselined_for_empty_migrations_table() -> None:
    inspection = _inspection(
        migrations_table_exists=True,
        public_tables=("deltallm_usertable",),
        migration_history=(),
    )

    classification = _SCRIPT_MODULE.classify_inspection(
        inspection,
        schema_path=Path("./prisma/schema.prisma"),
    )

    assert classification.kind == "legacy_unbaselined"
    assert classification.eligible_for_baseline is True


def test_classify_inspection_reports_already_baselined_for_repo_prefix_history() -> None:
    repo_migrations = (
        "20260301090000_core_schema_baseline",
        "20260302133000_audit_log_foundation",
    )
    inspection = _inspection(
        migrations_table_exists=True,
        migration_history=(
            _history_entry("20260301090000_core_schema_baseline"),
        ),
        repo_migration_names=repo_migrations,
    )

    classification = _SCRIPT_MODULE.classify_inspection(
        inspection,
        schema_path=Path("./prisma/schema.prisma"),
    )

    assert classification.kind == "partial_history_prefix"
    assert classification.eligible_for_baseline is False


def test_classify_inspection_reports_already_baselined_for_full_repo_history() -> None:
    repo_migrations = (
        "20260301090000_core_schema_baseline",
        "20260302133000_audit_log_foundation",
    )
    inspection = _inspection(
        migrations_table_exists=True,
        migration_history=(
            _history_entry("20260301090000_core_schema_baseline"),
            _history_entry("20260302133000_audit_log_foundation"),
        ),
        repo_migration_names=repo_migrations,
    )

    classification = _SCRIPT_MODULE.classify_inspection(
        inspection,
        schema_path=Path("./prisma/schema.prisma"),
    )

    assert classification.kind == "already_baselined"
    assert classification.eligible_for_baseline is False


def test_classify_inspection_reports_unexpected_history_for_failed_migration_rows() -> None:
    inspection = _inspection(
        migrations_table_exists=True,
        migration_history=(
            _history_entry("20260301090000_core_schema_baseline", finished=False),
        ),
    )

    classification = _SCRIPT_MODULE.classify_inspection(
        inspection,
        schema_path=Path("./prisma/schema.prisma"),
    )

    assert classification.kind == "unexpected_history"
    assert classification.eligible_for_baseline is False


def test_prepare_migrations_dir_for_diff_adds_lock_file_when_missing(tmp_path: Path) -> None:
    schema_path = tmp_path / "schema.prisma"
    schema_path.write_text(
        """
        datasource db {
          provider = "postgresql"
          url      = env("DATABASE_URL")
        }
        """,
        encoding="utf-8",
    )
    migrations_dir = _create_migrations_dir(tmp_path)

    prepared_dir, prepared_root = _SCRIPT_MODULE.prepare_migrations_dir_for_diff(
        migrations_dir,
        schema_path=schema_path,
    )

    try:
        assert prepared_root is not None
        assert prepared_dir != migrations_dir
        assert (prepared_dir / "migration_lock.toml").read_text(encoding="utf-8").strip() == 'provider = "postgresql"'
        assert (prepared_dir / "20260301090000_core_schema_baseline" / "migration.sql").is_file()
    finally:
        if prepared_root is not None:
            __import__("shutil").rmtree(prepared_root, ignore_errors=True)


@pytest.mark.asyncio
async def test_build_baseline_plan_succeeds_for_legacy_unbaselined_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    migration_names = (
        "20260301090000_core_schema_baseline",
        "20260302133000_audit_log_foundation",
    )
    migrations_dir = _create_migrations_dir(tmp_path, migration_names)

    inspection = _inspection(
        public_tables=("deltallm_usertable",),
        repo_migration_names=migration_names,
    )

    async def _fake_inspect(**kwargs):  # noqa: ANN003
        return inspection

    results = iter(
        [
            _completed_process(returncode=1, stderr="P3005 legacy database"),
            _completed_process(returncode=0, stdout="No difference detected"),
        ]
    )

    def _fake_runner(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:  # noqa: ANN003
        del command, kwargs
        return next(results)

    monkeypatch.setattr(_SCRIPT_MODULE, "load_inspection_result", _fake_inspect)

    args = SimpleNamespace(
        database_url="postgresql://postgres:postgres@localhost:5432/deltallm",
        shadow_database_url="postgresql://postgres:postgres@localhost:5432/deltallm_shadow",
        schema=Path("./prisma/schema.prisma"),
        migrations_dir=migrations_dir,
        output_diff=None,
    )

    plan = await _SCRIPT_MODULE.build_baseline_plan(args, runner=_fake_runner)

    assert plan.classification.kind == "legacy_unbaselined"
    assert plan.repo_migration_names == migration_names
    assert plan.pending_migration_names == migration_names
    assert plan.status_result.returncode == 1
    assert plan.diff_path is None


@pytest.mark.asyncio
async def test_build_baseline_plan_resumes_from_remaining_migrations_for_clean_partial_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    migration_names = (
        "20260301090000_core_schema_baseline",
        "20260302133000_audit_log_foundation",
    )
    migrations_dir = _create_migrations_dir(tmp_path, migration_names)

    inspection = _inspection(
        migrations_table_exists=True,
        public_tables=("deltallm_usertable",),
        migration_history=(
            _history_entry("20260301090000_core_schema_baseline"),
        ),
        repo_migration_names=migration_names,
    )

    async def _fake_inspect(**kwargs):  # noqa: ANN003
        return inspection

    results = iter(
        [
            _completed_process(returncode=1, stderr="Pending migration history"),
            _completed_process(returncode=0, stdout="No difference detected"),
        ]
    )

    def _fake_runner(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:  # noqa: ANN003
        del command, kwargs
        return next(results)

    monkeypatch.setattr(_SCRIPT_MODULE, "load_inspection_result", _fake_inspect)

    args = SimpleNamespace(
        database_url="postgresql://postgres:postgres@localhost:5432/deltallm",
        shadow_database_url="postgresql://postgres:postgres@localhost:5432/deltallm_shadow",
        schema=Path("./prisma/schema.prisma"),
        migrations_dir=migrations_dir,
        output_diff=None,
    )

    plan = await _SCRIPT_MODULE.build_baseline_plan(args, runner=_fake_runner)

    assert plan.classification.kind == "partial_history_prefix"
    assert plan.repo_migration_names == migration_names
    assert plan.pending_migration_names == ("20260302133000_audit_log_foundation",)
    assert plan.status_result.returncode == 1


@pytest.mark.asyncio
async def test_build_baseline_plan_refuses_when_live_database_differs_from_repo_chain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    migrations_dir = _create_migrations_dir(tmp_path)

    inspection = _inspection(
        public_tables=("deltallm_usertable",),
        repo_migration_names=("20260301090000_core_schema_baseline",),
    )

    async def _fake_inspect(**kwargs):  # noqa: ANN003
        return inspection

    results = iter(
        [
            _completed_process(returncode=1, stderr="P3005 legacy database"),
            _completed_process(returncode=2),
            _completed_process(returncode=0),
        ]
    )

    def _fake_runner(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:  # noqa: ANN003
        del command, kwargs
        return next(results)

    monkeypatch.setattr(_SCRIPT_MODULE, "load_inspection_result", _fake_inspect)

    args = SimpleNamespace(
        database_url="postgresql://postgres:postgres@localhost:5432/deltallm",
        shadow_database_url="postgresql://postgres:postgres@localhost:5432/deltallm_shadow",
        schema=Path("./prisma/schema.prisma"),
        migrations_dir=migrations_dir,
        output_diff=tmp_path / "diff.sql",
    )

    with pytest.raises(_SCRIPT_MODULE.BaselineHelperError, match="Refusing to baseline because the live database differs"):
        await _SCRIPT_MODULE.build_baseline_plan(args, runner=_fake_runner)


@pytest.mark.asyncio
async def test_build_baseline_plan_reports_repo_migration_mismatch_before_blaming_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    migrations_dir = _create_migrations_dir(tmp_path)

    inspection = _inspection(
        public_tables=("deltallm_usertable",),
        repo_migration_names=("20260301090000_core_schema_baseline",),
    )

    async def _fake_inspect(**kwargs):  # noqa: ANN003
        return inspection

    results = iter(
        [
            _completed_process(returncode=1, stderr="P3005 legacy database"),
            _completed_process(returncode=2),
            _completed_process(returncode=2),
        ]
    )

    def _fake_runner(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:  # noqa: ANN003
        del command, kwargs
        return next(results)

    monkeypatch.setattr(_SCRIPT_MODULE, "load_inspection_result", _fake_inspect)

    args = SimpleNamespace(
        database_url="postgresql://postgres:postgres@localhost:5432/deltallm",
        shadow_database_url="postgresql://postgres:postgres@localhost:5432/deltallm_shadow",
        schema=Path("./prisma/schema.prisma"),
        migrations_dir=migrations_dir,
        output_diff=tmp_path / "diff.sql",
    )

    with pytest.raises(_SCRIPT_MODULE.BaselineHelperError, match="checked-in Prisma migrations do not match"):
        await _SCRIPT_MODULE.build_baseline_plan(args, runner=_fake_runner)


@pytest.mark.asyncio
async def test_build_baseline_plan_refuses_when_database_has_partial_history_that_should_use_migrate_deploy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    migration_names = (
        "20260301090000_core_schema_baseline",
        "20260302133000_audit_log_foundation",
    )
    migrations_dir = _create_migrations_dir(tmp_path, migration_names)

    inspection = _inspection(
        migrations_table_exists=True,
        migration_history=(
            _history_entry("20260301090000_core_schema_baseline"),
        ),
        repo_migration_names=migration_names,
    )

    async def _fake_inspect(**kwargs):  # noqa: ANN003
        return inspection

    results = iter(
        [
            _completed_process(returncode=1, stderr="Pending migration history"),
            _completed_process(returncode=2),
            _completed_process(returncode=0),
            _completed_process(returncode=0),
        ]
    )

    def _fake_runner(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:  # noqa: ANN003
        del command, kwargs
        return next(results)

    monkeypatch.setattr(_SCRIPT_MODULE, "load_inspection_result", _fake_inspect)

    args = SimpleNamespace(
        database_url="postgresql://postgres:postgres@localhost:5432/deltallm",
        shadow_database_url="postgresql://postgres:postgres@localhost:5432/deltallm_shadow",
        schema=Path("./prisma/schema.prisma"),
        migrations_dir=migrations_dir,
        output_diff=None,
    )

    with pytest.raises(_SCRIPT_MODULE.BaselineHelperError, match="Use `uv run prisma migrate deploy"):
        await _SCRIPT_MODULE.build_baseline_plan(args, runner=_fake_runner)


@pytest.mark.asyncio
async def test_build_baseline_plan_refuses_when_partial_history_has_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    migration_names = (
        "20260301090000_core_schema_baseline",
        "20260302133000_audit_log_foundation",
    )
    migrations_dir = _create_migrations_dir(tmp_path, migration_names)

    inspection = _inspection(
        migrations_table_exists=True,
        migration_history=(
            _history_entry("20260301090000_core_schema_baseline"),
        ),
        repo_migration_names=migration_names,
    )

    async def _fake_inspect(**kwargs):  # noqa: ANN003
        return inspection

    results = iter(
        [
            _completed_process(returncode=1, stderr="Pending migration history"),
            _completed_process(returncode=2),
            _completed_process(returncode=0),
            _completed_process(returncode=2),
        ]
    )

    def _fake_runner(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:  # noqa: ANN003
        del command, kwargs
        return next(results)

    monkeypatch.setattr(_SCRIPT_MODULE, "load_inspection_result", _fake_inspect)

    args = SimpleNamespace(
        database_url="postgresql://postgres:postgres@localhost:5432/deltallm",
        shadow_database_url="postgresql://postgres:postgres@localhost:5432/deltallm_shadow",
        schema=Path("./prisma/schema.prisma"),
        migrations_dir=migrations_dir,
        output_diff=tmp_path / "prefix-drift.sql",
    )

    with pytest.raises(_SCRIPT_MODULE.BaselineHelperError, match="does not match either the recorded migration prefix or the full checked-in migration chain"):
        await _SCRIPT_MODULE.build_baseline_plan(args, runner=_fake_runner)


@pytest.mark.asyncio
async def test_build_baseline_plan_refuses_when_database_has_failed_migration_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    migrations_dir = _create_migrations_dir(tmp_path)

    inspection = _inspection(
        migrations_table_exists=True,
        migration_history=(
            _history_entry("20260301090000_core_schema_baseline", finished=False),
        ),
        repo_migration_names=("20260301090000_core_schema_baseline",),
    )

    async def _fake_inspect(**kwargs):  # noqa: ANN003
        return inspection

    def _fake_runner(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:  # noqa: ANN003
        del command, kwargs
        return _completed_process(returncode=1, stderr="status unavailable")

    monkeypatch.setattr(_SCRIPT_MODULE, "load_inspection_result", _fake_inspect)

    args = SimpleNamespace(
        database_url="postgresql://postgres:postgres@localhost:5432/deltallm",
        shadow_database_url="postgresql://postgres:postgres@localhost:5432/deltallm_shadow",
        schema=Path("./prisma/schema.prisma"),
        migrations_dir=migrations_dir,
        output_diff=None,
    )

    with pytest.raises(_SCRIPT_MODULE.BaselineHelperError, match="failed, unfinished, or rolled-back Prisma migration records"):
        await _SCRIPT_MODULE.build_baseline_plan(args, runner=_fake_runner)


@pytest.mark.asyncio
async def test_build_baseline_plan_refuses_when_database_is_fresh_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    migrations_dir = _create_migrations_dir(tmp_path)

    inspection = _inspection(
        migrations_table_exists=False,
        public_tables=(),
        migration_history=(),
        repo_migration_names=("20260301090000_core_schema_baseline",),
    )

    async def _fake_inspect(**kwargs):  # noqa: ANN003
        return inspection

    def _fake_runner(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:  # noqa: ANN003
        del command, kwargs
        return _completed_process(returncode=0, stdout="Database is empty")

    monkeypatch.setattr(_SCRIPT_MODULE, "load_inspection_result", _fake_inspect)

    args = SimpleNamespace(
        database_url="postgresql://postgres:postgres@localhost:5432/deltallm",
        shadow_database_url="postgresql://postgres:postgres@localhost:5432/deltallm_shadow",
        schema=Path("./prisma/schema.prisma"),
        migrations_dir=migrations_dir,
        output_diff=None,
    )

    with pytest.raises(_SCRIPT_MODULE.BaselineHelperError, match="Database has no public tables and no recorded Prisma migration history"):
        await _SCRIPT_MODULE.build_baseline_plan(args, runner=_fake_runner)


@pytest.mark.asyncio
async def test_inspect_database_state_wraps_connection_failures() -> None:
    class _FailingConnectPrisma:
        def __init__(self, **kwargs):  # noqa: ANN003
            del kwargs

        async def connect(self) -> None:
            raise RuntimeError("connection refused")

        async def disconnect(self) -> None:
            raise AssertionError("disconnect should not be called after a failed connect")

    with pytest.raises(_SCRIPT_MODULE.BaselineHelperError, match="Failed to connect to the target database"):
        await _SCRIPT_MODULE.inspect_database_state(
            database_url="postgresql://postgres:postgres@localhost:5432/deltallm",
            repo_migration_names=("20260301090000_core_schema_baseline",),
            prisma_factory=_FailingConnectPrisma,
        )


@pytest.mark.asyncio
async def test_inspect_database_state_wraps_client_initialization_failures() -> None:
    class _FailingInitPrisma:
        def __init__(self, **kwargs):  # noqa: ANN003
            del kwargs
            raise RuntimeError("runtime not initialized")

    with pytest.raises(_SCRIPT_MODULE.BaselineHelperError, match="Failed to initialize the Prisma database client"):
        await _SCRIPT_MODULE.inspect_database_state(
            database_url="postgresql://postgres:postgres@localhost:5432/deltallm",
            repo_migration_names=("20260301090000_core_schema_baseline",),
            prisma_factory=_FailingInitPrisma,
        )


@pytest.mark.asyncio
async def test_inspect_database_state_wraps_query_failures_and_disconnects() -> None:
    state = {"disconnected": False}

    class _FailingQueryPrisma:
        def __init__(self, **kwargs):  # noqa: ANN003
            del kwargs

        async def connect(self) -> None:
            return None

        async def query_raw(self, query: str):  # noqa: ANN001
            del query
            raise RuntimeError("permission denied")

        async def disconnect(self) -> None:
            state["disconnected"] = True

    with pytest.raises(_SCRIPT_MODULE.BaselineHelperError, match="Failed to inspect the target database schema"):
        await _SCRIPT_MODULE.inspect_database_state(
            database_url="postgresql://postgres:postgres@localhost:5432/deltallm",
            repo_migration_names=("20260301090000_core_schema_baseline",),
            prisma_factory=_FailingQueryPrisma,
        )

    assert state["disconnected"] is True


def test_run_prisma_command_wraps_launch_failures() -> None:
    def _failing_runner(*args, **kwargs):  # noqa: ANN002, ANN003
        del args, kwargs
        raise FileNotFoundError("uv not found")

    with pytest.raises(_SCRIPT_MODULE.BaselineHelperError, match="Failed to launch `uv run prisma migrate status --schema"):
        _SCRIPT_MODULE.run_prisma_command(
            ["migrate", "status", "--schema", str(Path("./prisma/schema.prisma").resolve())],
            database_url="postgresql://postgres:postgres@localhost:5432/deltallm",
            runner=_failing_runner,
        )


@pytest.mark.asyncio
async def test_run_apply_command_reruns_safety_checks_before_mutating_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan = _SCRIPT_MODULE.BaselinePlan(
        inspection=_inspection(
            repo_migration_names=("20260301090000_core_schema_baseline", "20260302133000_audit_log_foundation"),
        ),
        classification=_SCRIPT_MODULE.InspectionClassification(
            kind="legacy_unbaselined",
            summary="legacy",
            recommended_action="plan then apply",
            eligible_for_baseline=True,
        ),
        status_result=_SCRIPT_MODULE.CommandResult(
            command=("uv", "run", "prisma", "migrate", "status"),
            returncode=1,
            stdout="legacy",
            stderr="P3005",
        ),
        repo_migration_names=("20260301090000_core_schema_baseline", "20260302133000_audit_log_foundation"),
        pending_migration_names=("20260301090000_core_schema_baseline", "20260302133000_audit_log_foundation"),
        diff_path=None,
        diff_path_was_temporary=True,
    )
    plan_calls = {"count": 0}
    commands: list[tuple[str, ...]] = []

    async def _fake_plan(args, **kwargs):  # noqa: ANN003
        plan_calls["count"] += 1
        return plan

    async def _fake_inspect(**kwargs):  # noqa: ANN003
        return _inspection(
            migrations_table_exists=True,
            migration_history=(
                _history_entry("20260301090000_core_schema_baseline"),
                _history_entry("20260302133000_audit_log_foundation"),
            ),
            repo_migration_names=plan.repo_migration_names,
        )

    def _fake_runner(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:  # noqa: ANN003
        del kwargs
        commands.append(tuple(command))
        return _completed_process(returncode=0, stdout="ok")

    monkeypatch.setattr(_SCRIPT_MODULE, "build_baseline_plan", _fake_plan)
    monkeypatch.setattr(_SCRIPT_MODULE, "load_inspection_result", _fake_inspect)

    args = SimpleNamespace(
        yes=True,
        database_url="postgresql://postgres:postgres@localhost:5432/deltallm",
        shadow_database_url="postgresql://postgres:postgres@localhost:5432/deltallm_shadow",
        schema=Path("./prisma/schema.prisma"),
        migrations_dir=tmp_path / "migrations",
        output_diff=None,
    )

    await _SCRIPT_MODULE.run_apply_command(args, runner=_fake_runner)

    assert plan_calls["count"] == 1
    assert commands == [
        ("uv", "run", "prisma", "migrate", "resolve", "--applied", "20260301090000_core_schema_baseline", "--schema", str(Path("./prisma/schema.prisma").resolve())),
        ("uv", "run", "prisma", "migrate", "resolve", "--applied", "20260302133000_audit_log_foundation", "--schema", str(Path("./prisma/schema.prisma").resolve())),
        ("uv", "run", "prisma", "migrate", "deploy", "--schema", str(Path("./prisma/schema.prisma").resolve())),
        ("uv", "run", "prisma", "migrate", "status", "--schema", str(Path("./prisma/schema.prisma").resolve())),
    ]


@pytest.mark.asyncio
async def test_run_apply_command_uses_full_repo_chain_for_post_inspection_after_resumed_baseline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan = _SCRIPT_MODULE.BaselinePlan(
        inspection=_inspection(
            migrations_table_exists=True,
            migration_history=(
                _history_entry("20260301090000_core_schema_baseline"),
            ),
            repo_migration_names=("20260301090000_core_schema_baseline", "20260302133000_audit_log_foundation"),
        ),
        classification=_SCRIPT_MODULE.InspectionClassification(
            kind="partial_history_prefix",
            summary="resumable",
            recommended_action="resume",
            eligible_for_baseline=False,
        ),
        status_result=_SCRIPT_MODULE.CommandResult(
            command=("uv", "run", "prisma", "migrate", "status"),
            returncode=1,
            stdout="legacy",
            stderr="pending",
        ),
        repo_migration_names=("20260301090000_core_schema_baseline", "20260302133000_audit_log_foundation"),
        pending_migration_names=("20260302133000_audit_log_foundation",),
        diff_path=None,
        diff_path_was_temporary=True,
    )
    inspect_calls: list[tuple[str, ...]] = []
    commands: list[tuple[str, ...]] = []

    async def _fake_plan(args, **kwargs):  # noqa: ANN003
        del args, kwargs
        return plan

    async def _fake_inspect(**kwargs):  # noqa: ANN003
        inspect_calls.append(Path(kwargs["migrations_dir"]).resolve())
        return _inspection(
            migrations_table_exists=True,
            migration_history=(
                _history_entry("20260301090000_core_schema_baseline"),
                _history_entry("20260302133000_audit_log_foundation"),
            ),
            repo_migration_names=plan.repo_migration_names,
        )

    def _fake_runner(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:  # noqa: ANN003
        del kwargs
        commands.append(tuple(command))
        return _completed_process(returncode=0, stdout="ok")

    monkeypatch.setattr(_SCRIPT_MODULE, "build_baseline_plan", _fake_plan)
    monkeypatch.setattr(_SCRIPT_MODULE, "load_inspection_result", _fake_inspect)

    args = SimpleNamespace(
        yes=True,
        database_url="postgresql://postgres:postgres@localhost:5432/deltallm",
        shadow_database_url="postgresql://postgres:postgres@localhost:5432/deltallm_shadow",
        schema=Path("./prisma/schema.prisma"),
        migrations_dir=tmp_path / "migrations",
        output_diff=None,
    )

    await _SCRIPT_MODULE.run_apply_command(args, runner=_fake_runner)

    assert commands == [
        ("uv", "run", "prisma", "migrate", "resolve", "--applied", "20260302133000_audit_log_foundation", "--schema", str(Path("./prisma/schema.prisma").resolve())),
        ("uv", "run", "prisma", "migrate", "deploy", "--schema", str(Path("./prisma/schema.prisma").resolve())),
        ("uv", "run", "prisma", "migrate", "status", "--schema", str(Path("./prisma/schema.prisma").resolve())),
    ]
    assert inspect_calls == [args.migrations_dir.resolve()]


@pytest.mark.asyncio
async def test_run_inspect_command_reports_already_baselined_after_successful_baseline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    migration_names = (
        "20260301090000_core_schema_baseline",
        "20260302133000_audit_log_foundation",
    )
    migrations_dir = _create_migrations_dir(tmp_path, migration_names)

    inspection = _inspection(
        migrations_table_exists=True,
        migration_history=(
            _history_entry("20260301090000_core_schema_baseline"),
            _history_entry("20260302133000_audit_log_foundation"),
        ),
        repo_migration_names=migration_names,
    )

    async def _fake_inspect(**kwargs):  # noqa: ANN003
        return inspection

    monkeypatch.setattr(_SCRIPT_MODULE, "load_inspection_result", _fake_inspect)

    args = SimpleNamespace(
        database_url="postgresql://postgres:postgres@localhost:5432/deltallm",
        schema=Path("./prisma/schema.prisma"),
        migrations_dir=migrations_dir,
    )
    stdout = StringIO()

    await _SCRIPT_MODULE.run_inspect_command(args, stdout=stdout)

    output = stdout.getvalue()
    assert "Classification: already_baselined" in output
    assert "Recommended next step:" in output
    assert "uv run prisma migrate deploy" in output


def test_main_routes_inspect_command(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"inspect": 0}

    async def _fake_inspect(args, **kwargs):  # noqa: ANN001, ANN003
        del args, kwargs
        called["inspect"] += 1

    monkeypatch.setattr(_SCRIPT_MODULE, "run_inspect_command", _fake_inspect)

    exit_code = _SCRIPT_MODULE.main(
        ["inspect", "--database-url", "postgresql://postgres:postgres@localhost:5432/deltallm"]
    )

    assert exit_code == 0
    assert called["inspect"] == 1


def test_main_routes_plan_command(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"plan": 0}

    async def _fake_plan(args, **kwargs):  # noqa: ANN001, ANN003
        del args, kwargs
        called["plan"] += 1

    monkeypatch.setattr(_SCRIPT_MODULE, "run_plan_command", _fake_plan)

    exit_code = _SCRIPT_MODULE.main(
        [
            "plan",
            "--database-url",
            "postgresql://postgres:postgres@localhost:5432/deltallm",
            "--shadow-database-url",
            "postgresql://postgres:postgres@localhost:5432/deltallm_shadow",
        ]
    )

    assert exit_code == 0
    assert called["plan"] == 1


def test_main_routes_apply_command(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"apply": 0}

    async def _fake_apply(args, **kwargs):  # noqa: ANN001, ANN003
        del args, kwargs
        called["apply"] += 1

    monkeypatch.setattr(_SCRIPT_MODULE, "run_apply_command", _fake_apply)

    exit_code = _SCRIPT_MODULE.main(
        [
            "apply",
            "--database-url",
            "postgresql://postgres:postgres@localhost:5432/deltallm",
            "--shadow-database-url",
            "postgresql://postgres:postgres@localhost:5432/deltallm_shadow",
            "--yes",
        ]
    )

    assert exit_code == 0
    assert called["apply"] == 1


def test_main_prints_clean_operator_error_for_inspect_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def _fake_inspect_state(**kwargs):  # noqa: ANN003
        del kwargs
        raise _SCRIPT_MODULE.BaselineHelperError("Database inspection failed cleanly")

    monkeypatch.setattr(_SCRIPT_MODULE, "load_inspection_result", _fake_inspect_state)

    exit_code = _SCRIPT_MODULE.main(
        ["inspect", "--database-url", "postgresql://postgres:postgres@localhost:5432/deltallm"]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err.strip() == "Database inspection failed cleanly"
    assert "Traceback" not in captured.err


def test_run_inspection_worker_surfaces_only_the_last_operator_error_line(tmp_path: Path) -> None:
    def _fake_runner(*args, **kwargs):  # noqa: ANN002, ANN003
        del args, kwargs
        return _completed_process(
            returncode=1,
            stderr='{"error_code":"P1001","message":"raw prisma blob"}\nFailed to connect to the target database.',
        )

    with pytest.raises(_SCRIPT_MODULE.BaselineHelperError, match="^Failed to connect to the target database\\.$"):
        _SCRIPT_MODULE.run_inspection_worker(
            database_url="postgresql://postgres:postgres@localhost:5432/deltallm",
            schema_path=Path("./prisma/schema.prisma"),
            migrations_dir=tmp_path,
            runner=_fake_runner,
        )


def test_main_apply_requires_yes() -> None:
    exit_code = _SCRIPT_MODULE.main(
        [
            "apply",
            "--database-url",
            "postgresql://postgres:postgres@localhost:5432/deltallm",
            "--shadow-database-url",
            "postgresql://postgres:postgres@localhost:5432/deltallm_shadow",
        ]
    )

    assert exit_code == 1
