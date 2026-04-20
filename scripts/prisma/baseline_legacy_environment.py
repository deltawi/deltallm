from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence, TextIO

from prisma import Prisma

DEFAULT_SCHEMA_PATH = Path("./prisma/schema.prisma")
DEFAULT_MIGRATIONS_DIR = Path("./prisma/migrations")
DEFAULT_DATABASE_URL_ENV = "DATABASE_URL"
DEFAULT_SHADOW_DATABASE_URL_ENV = "SHADOW_DATABASE_URL"


class BaselineHelperError(RuntimeError):
    pass


@dataclass(frozen=True)
class MigrationHistoryEntry:
    migration_name: str
    finished: bool
    rolled_back: bool


@dataclass(frozen=True)
class InspectionResult:
    database_name: str
    migrations_table_exists: bool
    public_tables: tuple[str, ...]
    migration_history: tuple[MigrationHistoryEntry, ...]
    repo_migration_names: tuple[str, ...]


@dataclass(frozen=True)
class InspectionClassification:
    kind: str
    summary: str
    recommended_action: str
    eligible_for_baseline: bool


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def combined_output(self) -> str:
        return "\n".join(part for part in (self.stdout, self.stderr) if part).strip()


@dataclass(frozen=True)
class BaselinePlan:
    inspection: InspectionResult
    classification: InspectionClassification
    status_result: CommandResult
    repo_migration_names: tuple[str, ...]
    pending_migration_names: tuple[str, ...]
    diff_path: Path | None
    diff_path_was_temporary: bool


def history_names_from_inspection(result: InspectionResult) -> tuple[str, ...]:
    return tuple(entry.migration_name for entry in result.migration_history)


def inspection_result_to_payload(result: InspectionResult) -> dict[str, Any]:
    return {
        "database_name": result.database_name,
        "migrations_table_exists": result.migrations_table_exists,
        "public_tables": list(result.public_tables),
        "migration_history": [
            {
                "migration_name": entry.migration_name,
                "finished": entry.finished,
                "rolled_back": entry.rolled_back,
            }
            for entry in result.migration_history
        ],
        "repo_migration_names": list(result.repo_migration_names),
    }


def inspection_result_from_payload(payload: dict[str, Any]) -> InspectionResult:
    try:
        return InspectionResult(
            database_name=str(payload["database_name"]),
            migrations_table_exists=bool(payload["migrations_table_exists"]),
            public_tables=tuple(str(name) for name in payload["public_tables"]),
            migration_history=tuple(
                MigrationHistoryEntry(
                    migration_name=str(entry["migration_name"]),
                    finished=bool(entry["finished"]),
                    rolled_back=bool(entry["rolled_back"]),
                )
                for entry in payload["migration_history"]
            ),
            repo_migration_names=tuple(str(name) for name in payload["repo_migration_names"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BaselineHelperError("Internal Prisma inspection worker returned an invalid payload.") from exc


def load_repo_migration_names(migrations_dir: Path) -> tuple[str, ...]:
    normalized_dir = migrations_dir.resolve()
    if not normalized_dir.is_dir():
        raise BaselineHelperError(f"Migrations directory does not exist: {normalized_dir}")

    migration_names = tuple(
        entry.name
        for entry in sorted(normalized_dir.iterdir())
        if entry.is_dir() and (entry / "migration.sql").is_file()
    )
    if not migration_names:
        raise BaselineHelperError(f"No Prisma migrations were found in {normalized_dir}")
    return migration_names


def detect_schema_provider(schema_path: Path) -> str:
    try:
        schema_text = schema_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BaselineHelperError(f"Failed to read Prisma schema from {schema_path}: {exc}") from exc
    match = re.search(r"datasource\s+\w+\s*\{[^}]*provider\s*=\s*\"([^\"]+)\"", schema_text, re.DOTALL)
    if not match:
        raise BaselineHelperError(f"Could not determine the datasource provider from {schema_path}")
    return match.group(1).strip()


def prepare_migrations_dir_for_diff(migrations_dir: Path, *, schema_path: Path) -> tuple[Path, Path | None]:
    lock_file = migrations_dir / "migration_lock.toml"
    if lock_file.is_file():
        return migrations_dir, None

    provider = detect_schema_provider(schema_path)
    temp_root = Path(tempfile.mkdtemp(prefix="deltallm-issue94-migrations-"))
    prepared_dir = temp_root / "migrations"
    shutil.copytree(migrations_dir, prepared_dir)
    (prepared_dir / "migration_lock.toml").write_text(f'provider = "{provider}"\n', encoding="utf-8")
    return prepared_dir, temp_root


def prepare_filtered_migrations_dir(
    migrations_dir: Path,
    *,
    migration_names: Sequence[str],
    schema_path: Path,
) -> tuple[Path, Path | None]:
    selected_names = tuple(migration_names)
    missing_names = [name for name in selected_names if not ((migrations_dir / name) / "migration.sql").is_file()]
    if missing_names:
        raise BaselineHelperError(
            "The recorded Prisma migration history references migrations that are not present in this repository: "
            + ", ".join(missing_names)
        )

    if not selected_names:
        raise BaselineHelperError("Cannot prepare a filtered Prisma migration directory without recorded migrations.")

    provider = detect_schema_provider(schema_path)
    temp_root = Path(tempfile.mkdtemp(prefix="deltallm-issue94-prefix-migrations-"))
    prepared_dir = temp_root / "migrations"
    prepared_dir.mkdir(parents=True, exist_ok=True)
    lock_file = migrations_dir / "migration_lock.toml"
    if lock_file.is_file():
        shutil.copy2(lock_file, prepared_dir / "migration_lock.toml")
    else:
        (prepared_dir / "migration_lock.toml").write_text(f'provider = "{provider}"\n', encoding="utf-8")

    for migration_name in selected_names:
        shutil.copytree(migrations_dir / migration_name, prepared_dir / migration_name)
    return prepared_dir, temp_root


def run_migration_diff(
    *,
    migrations_dir: Path,
    schema_path: Path,
    shadow_database_url: str,
    database_url: str,
    output_path: Path,
    compare_to: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> CommandResult:
    if compare_to not in {"datasource", "datamodel"}:
        raise BaselineHelperError(f"Unsupported Prisma diff target: {compare_to}")

    command = [
        "migrate",
        "diff",
        "--from-migrations",
        str(migrations_dir),
    ]
    if compare_to == "datasource":
        command.extend(["--to-schema-datasource", str(schema_path)])
    else:
        command.extend(["--to-schema-datamodel", str(schema_path)])
    command.extend(
        [
            "--shadow-database-url",
            shadow_database_url,
            "--script",
            "--exit-code",
            "--output",
            str(output_path),
        ]
    )
    return run_prisma_command(
        command,
        database_url=database_url,
        runner=runner,
    )


def normalize_ignorable_diff_result(result: CommandResult, *, output_path: Path) -> CommandResult:
    if result.returncode == 2 and is_ignorable_prisma_diff(output_path):
        return CommandResult(
            command=result.command,
            returncode=0,
            stdout=result.stdout,
            stderr=result.stderr,
        )
    return result


def find_matching_repo_prefix(
    *,
    migrations_dir: Path,
    repo_migration_names: Sequence[str],
    schema_path: Path,
    shadow_database_url: str,
    database_url: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[str, ...] | None:
    repo_history_names = tuple(repo_migration_names)
    for prefix_length in range(len(repo_history_names) - 1, 0, -1):
        prefix_migration_names = repo_history_names[:prefix_length]
        prefix_migrations_dir, prefix_root = prepare_filtered_migrations_dir(
            migrations_dir,
            migration_names=prefix_migration_names,
            schema_path=schema_path,
        )
        prefix_diff_path, prefix_diff_path_was_temporary = prepare_diff_output_path(None)
        try:
            prefix_diff_result = run_migration_diff(
                migrations_dir=prefix_migrations_dir,
                schema_path=schema_path,
                shadow_database_url=shadow_database_url,
                database_url=database_url,
                output_path=prefix_diff_path,
                compare_to="datasource",
                runner=runner,
            )
            prefix_diff_result = normalize_ignorable_diff_result(
                prefix_diff_result,
                output_path=prefix_diff_path,
            )
            if prefix_diff_result.returncode == 0:
                return prefix_migration_names
            if prefix_diff_result.returncode != 2:
                raise BaselineHelperError(
                    (
                        "Failed to compare the live database against a historical Prisma migration prefix: "
                        f"{prefix_diff_result.combined_output or 'prisma migrate diff exited with an error'}"
                    )
                )
        finally:
            cleanup_temporary_path(prefix_diff_path, was_temporary=prefix_diff_path_was_temporary)
            shutil.rmtree(prefix_root, ignore_errors=True)
    return None


async def inspect_database_state(
    *,
    database_url: str,
    repo_migration_names: Sequence[str],
    prisma_factory: Callable[..., Any] = Prisma,
) -> InspectionResult:
    normalized_database_url = require_value(database_url, "DATABASE_URL")
    client: Any | None = None
    connected = False
    try:
        try:
            client = prisma_factory(datasource={"url": normalized_database_url})
        except Exception as exc:  # pragma: no cover - covered via tests with fake client types
            raise BaselineHelperError(
                "Failed to initialize the Prisma database client. Check that the Prisma runtime is installed "
                "correctly and that DATABASE_URL is valid for this schema."
            ) from exc
        try:
            await client.connect()
            connected = True
        except Exception as exc:  # pragma: no cover - covered via tests with fake client types
            raise BaselineHelperError(
                "Failed to connect to the target database. Check DATABASE_URL credentials, network reachability, "
                "and that the database is accepting connections."
            ) from exc
        database_name_rows = await client.query_raw("SELECT current_database()::text AS database_name")
        public_table_rows = await client.query_raw(
            """
            SELECT tablename::text AS table_name
            FROM pg_catalog.pg_tables
            WHERE schemaname = 'public'
              AND tablename <> '_prisma_migrations'
            ORDER BY tablename
            """
        )
        migrations_table_rows = await client.query_raw(
            "SELECT to_regclass('public._prisma_migrations')::text AS table_name"
        )
        migrations_table_exists = bool(
            migrations_table_rows and dict(migrations_table_rows[0]).get("table_name")
        )
        migration_history_rows: list[MigrationHistoryEntry] = []
        if migrations_table_exists:
            history_rows = await client.query_raw(
                """
                SELECT
                    migration_name::text AS migration_name,
                    (finished_at IS NOT NULL) AS finished,
                    (rolled_back_at IS NOT NULL) AS rolled_back
                FROM _prisma_migrations
                ORDER BY migration_name
                """
            )
            for row in history_rows:
                payload = dict(row)
                migration_history_rows.append(
                    MigrationHistoryEntry(
                        migration_name=str(payload.get("migration_name", "")).strip(),
                        finished=bool(payload.get("finished")),
                        rolled_back=bool(payload.get("rolled_back")),
                    )
                )
        database_name = ""
        if database_name_rows:
            database_name = str(dict(database_name_rows[0]).get("database_name", "")).strip()
        return InspectionResult(
            database_name=database_name or "<unknown>",
            migrations_table_exists=migrations_table_exists,
            public_tables=tuple(
                str(dict(row).get("table_name", "")).strip()
                for row in public_table_rows
                if str(dict(row).get("table_name", "")).strip()
            ),
            migration_history=tuple(migration_history_rows),
            repo_migration_names=tuple(repo_migration_names),
        )
    except BaselineHelperError:
        raise
    except Exception as exc:  # pragma: no cover - covered via tests with fake client types
        raise BaselineHelperError(
            "Failed to inspect the target database schema and Prisma migration history. Check DATABASE_URL "
            "privileges and database reachability."
        ) from exc
    finally:
        if connected and client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass


def extract_last_non_empty_line(text: str) -> str | None:
    for line in reversed(text.splitlines()):
        normalized = line.strip()
        if normalized:
            return normalized
    return None


def load_sql_diff_statements(path: Path) -> tuple[str, ...]:
    try:
        sql_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BaselineHelperError(f"Failed to read Prisma diff output from {path}: {exc}") from exc

    statements: list[str] = []
    for chunk in sql_text.split(";"):
        lines = [
            line.strip()
            for line in chunk.splitlines()
            if line.strip() and not line.strip().startswith("--")
        ]
        if not lines:
            continue
        statements.append(" ".join(lines) + ";")
    return tuple(statements)


def is_ignorable_prisma_diff(path: Path) -> bool:
    if not path.exists():
        return False
    statements = load_sql_diff_statements(path)
    if not statements:
        return True
    return all(
        re.fullmatch(r'CREATE EXTENSION IF NOT EXISTS "[^"]+"(?: .*)?;', statement, flags=re.IGNORECASE)
        for statement in statements
    )


def run_inspection_worker(
    *,
    database_url: str,
    schema_path: Path,
    migrations_dir: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> InspectionResult:
    command = (
        sys.executable,
        str(Path(__file__).resolve()),
        "_inspect-json",
        "--database-url",
        require_value(database_url, "DATABASE_URL"),
        "--schema",
        str(schema_path.resolve()),
        "--migrations-dir",
        str(migrations_dir.resolve()),
    )
    try:
        completed = runner(
            list(command),
            capture_output=True,
            text=True,
            check=False,
            env=os.environ.copy(),
        )
    except OSError as exc:
        raise BaselineHelperError(
            f"Failed to launch the internal Prisma inspection worker. Check that Python can execute {Path(__file__).name}: {exc}"
        ) from exc

    if completed.returncode != 0:
        worker_message = extract_last_non_empty_line(completed.stderr) or extract_last_non_empty_line(completed.stdout)
        raise BaselineHelperError(worker_message or "The internal Prisma inspection worker failed.")

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise BaselineHelperError("Internal Prisma inspection worker returned invalid JSON output.") from exc
    return inspection_result_from_payload(payload)


async def load_inspection_result(
    *,
    database_url: str,
    schema_path: Path,
    migrations_dir: Path,
    prisma_factory: Callable[..., Any] = Prisma,
) -> InspectionResult:
    repo_migration_names = load_repo_migration_names(migrations_dir)
    if prisma_factory is not Prisma:
        return await inspect_database_state(
            database_url=database_url,
            repo_migration_names=repo_migration_names,
            prisma_factory=prisma_factory,
        )
    return run_inspection_worker(
        database_url=database_url,
        schema_path=schema_path,
        migrations_dir=migrations_dir,
    )


def classify_inspection(result: InspectionResult, *, schema_path: Path) -> InspectionClassification:
    history = result.migration_history
    repo_migration_names = result.repo_migration_names
    schema_hint = f"uv run prisma migrate deploy --schema={schema_path}"

    if not history:
        if result.public_tables:
            return InspectionClassification(
                kind="legacy_unbaselined",
                summary="Database has existing public tables but no recorded Prisma migration history.",
                recommended_action=(
                    "Run the issue 94 baseline flow with `inspect`, `plan`, and `apply --yes` before "
                    f"returning to `{schema_hint}`."
                ),
                eligible_for_baseline=True,
            )
        return InspectionClassification(
            kind="fresh_empty",
            summary="Database has no public tables and no recorded Prisma migration history.",
            recommended_action=f"Use `{schema_hint}` instead of the legacy baseline flow.",
            eligible_for_baseline=False,
        )

    if any((not entry.finished) or entry.rolled_back for entry in history):
        return InspectionClassification(
            kind="unexpected_history",
            summary="Database already has failed, unfinished, or rolled-back Prisma migration records.",
            recommended_action="Stop and investigate the existing Prisma migration history manually.",
            eligible_for_baseline=False,
        )

    history_names = history_names_from_inspection(result)
    if any(name not in repo_migration_names for name in history_names):
        return InspectionClassification(
            kind="unexpected_history",
            summary="Database contains Prisma migration records that are not present in this repository.",
            recommended_action="Stop and reconcile the migration history manually before deploying.",
            eligible_for_baseline=False,
        )

    if history_names == repo_migration_names:
        return InspectionClassification(
            kind="already_baselined",
            summary="Database already has the full Prisma migration history recorded.",
            recommended_action=f"Use `{schema_hint}` for normal deployment verification.",
            eligible_for_baseline=False,
        )

    if history_names == tuple(repo_migration_names[: len(history_names)]):
        pending_count = len(repo_migration_names) - len(history_names)
        return InspectionClassification(
            kind="partial_history_prefix",
            summary=(
                "Database has a repo-prefix Prisma migration history, but the full checked-in chain is not "
                f"recorded yet ({pending_count} repo migration(s) remain unrecorded)."
            ),
            recommended_action=(
                "Run the issue 94 `plan` step to determine whether this is a resumable baseline or a normal "
                f"`{schema_hint}` environment."
            ),
            eligible_for_baseline=False,
        )

    return InspectionClassification(
        kind="unexpected_history",
        summary="Database has Prisma migration records, but they do not match the repo migration prefix order.",
        recommended_action="Stop and investigate the migration history manually before baselining or deploying.",
        eligible_for_baseline=False,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect, plan, and apply the manual baseline flow for legacy DeltaLLM databases."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command_name in ("inspect", "plan", "apply"):
        command_parser = subparsers.add_parser(command_name)
        command_parser.add_argument("--database-url", default=os.getenv(DEFAULT_DATABASE_URL_ENV, ""))
        command_parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA_PATH)
        command_parser.add_argument("--migrations-dir", type=Path, default=DEFAULT_MIGRATIONS_DIR)
        if command_name in {"plan", "apply"}:
            command_parser.add_argument(
                "--shadow-database-url",
                default=os.getenv(DEFAULT_SHADOW_DATABASE_URL_ENV, ""),
            )
            command_parser.add_argument("--output-diff", type=Path, default=None)
        if command_name == "apply":
            command_parser.add_argument("--yes", action="store_true")

    internal_parser = subparsers.add_parser("_inspect-json", help=argparse.SUPPRESS)
    internal_parser.add_argument("--database-url", default=os.getenv(DEFAULT_DATABASE_URL_ENV, ""))
    internal_parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA_PATH)
    internal_parser.add_argument("--migrations-dir", type=Path, default=DEFAULT_MIGRATIONS_DIR)

    return parser


async def run_inspect_command(
    args: argparse.Namespace,
    *,
    stdout: TextIO | None = None,
    prisma_factory: Callable[..., Any] = Prisma,
) -> None:
    out_stream = stdout if stdout is not None else sys.stdout
    schema_path = args.schema.resolve()
    inspection = await load_inspection_result(
        database_url=args.database_url,
        schema_path=schema_path,
        migrations_dir=args.migrations_dir.resolve(),
        prisma_factory=prisma_factory,
    )
    classification = classify_inspection(inspection, schema_path=schema_path)

    print(f"Database: {inspection.database_name}", file=out_stream)
    print(
        f"Public tables (excluding _prisma_migrations): {len(inspection.public_tables)}",
        file=out_stream,
    )
    if inspection.public_tables:
        print("Table names:", file=out_stream)
        for table_name in inspection.public_tables:
            print(f"  - {table_name}", file=out_stream)
    history_state = "missing"
    if inspection.migrations_table_exists:
        history_state = "empty" if not inspection.migration_history else "present"
    print(f"_prisma_migrations: {history_state}", file=out_stream)
    if inspection.migration_history:
        print(f"Recorded migration rows: {len(inspection.migration_history)}", file=out_stream)
        for entry in inspection.migration_history:
            row_state = "applied"
            if entry.rolled_back:
                row_state = "rolled_back"
            elif not entry.finished:
                row_state = "unfinished"
            print(f"  - {entry.migration_name} ({row_state})", file=out_stream)
    print(f"Repo migrations: {len(inspection.repo_migration_names)}", file=out_stream)
    print(f"Classification: {classification.kind}", file=out_stream)
    print(f"Summary: {classification.summary}", file=out_stream)
    print(f"Recommended next step: {classification.recommended_action}", file=out_stream)


async def build_baseline_plan(
    args: argparse.Namespace,
    *,
    prisma_factory: Callable[..., Any] = Prisma,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> BaselinePlan:
    schema_path = args.schema.resolve()
    migrations_dir = args.migrations_dir.resolve()
    repo_migration_names = load_repo_migration_names(migrations_dir)
    repo_history_names = tuple(repo_migration_names)
    inspection = await load_inspection_result(
        database_url=args.database_url,
        schema_path=schema_path,
        migrations_dir=migrations_dir,
        prisma_factory=prisma_factory,
    )
    classification = classify_inspection(inspection, schema_path=schema_path)
    recorded_history_names = history_names_from_inspection(inspection)
    status_result = run_prisma_command(
        ["migrate", "status", "--schema", str(schema_path)],
        database_url=args.database_url,
        runner=runner,
    )

    if classification.kind == "fresh_empty":
        raise BaselineHelperError(
            f"{classification.summary} {classification.recommended_action}"
        )
    if classification.kind == "already_baselined":
        raise BaselineHelperError(
            f"{classification.summary} {classification.recommended_action}"
        )
    if classification.kind not in {"legacy_unbaselined", "partial_history_prefix"}:
        raise BaselineHelperError(
            f"{classification.summary} {classification.recommended_action}"
        )

    shadow_database_url = require_value(
        args.shadow_database_url,
        "SHADOW_DATABASE_URL (or --shadow-database-url)",
    )
    diff_migrations_dir, prepared_root = prepare_migrations_dir_for_diff(
        migrations_dir,
        schema_path=schema_path,
    )
    diff_path, diff_path_was_temporary = prepare_diff_output_path(args.output_diff)
    prefix_diff_root: Path | None = None
    repo_diff_path: Path | None = None
    repo_diff_path_was_temporary = False
    try:
        diff_result = run_migration_diff(
            migrations_dir=diff_migrations_dir,
            schema_path=schema_path,
            shadow_database_url=shadow_database_url,
            database_url=args.database_url,
            output_path=diff_path,
            compare_to="datasource",
            runner=runner,
        )
        diff_result = normalize_ignorable_diff_result(diff_result, output_path=diff_path)

        if diff_result.returncode == 2:
            repo_diff_path, repo_diff_path_was_temporary = prepare_diff_output_path(None)
            repo_diff_result = run_migration_diff(
                migrations_dir=diff_migrations_dir,
                schema_path=schema_path,
                shadow_database_url=shadow_database_url,
                database_url=args.database_url,
                output_path=repo_diff_path,
                compare_to="datamodel",
                runner=runner,
            )
            if repo_diff_path is not None:
                repo_diff_result = normalize_ignorable_diff_result(
                    repo_diff_result,
                    output_path=repo_diff_path,
                )
            if repo_diff_result.returncode == 2:
                cleanup_temporary_path(diff_path, was_temporary=diff_path_was_temporary)
                raise BaselineHelperError(
                    (
                        "Refusing to baseline because the checked-in Prisma migrations do not match "
                        f"{schema_path}. Reconcile the repo migration chain before using this helper."
                    )
                )
            if repo_diff_result.returncode != 0:
                raise BaselineHelperError(
                    (
                        "Failed to compare the checked-in Prisma migration chain against the Prisma schema: "
                        f"{repo_diff_result.combined_output or 'prisma migrate diff exited with an error'}"
                    )
                )
            if classification.kind == "partial_history_prefix":
                prefix_diff_migrations_dir, prefix_diff_root = prepare_filtered_migrations_dir(
                    migrations_dir,
                    migration_names=recorded_history_names,
                    schema_path=schema_path,
                )
                prefix_diff_result = run_migration_diff(
                    migrations_dir=prefix_diff_migrations_dir,
                    schema_path=schema_path,
                    shadow_database_url=shadow_database_url,
                    database_url=args.database_url,
                    output_path=diff_path,
                    compare_to="datasource",
                    runner=runner,
                )
                if prefix_diff_result.returncode == 0:
                    cleanup_temporary_path(diff_path, was_temporary=diff_path_was_temporary)
                    raise BaselineHelperError(
                        (
                            "Database already has a valid partial Prisma migration history with pending repo "
                            f"migrations. Use `uv run prisma migrate deploy --schema={schema_path}` instead of "
                            "the issue 94 baseline helper."
                        )
                    )
                if prefix_diff_result.returncode == 2:
                    raise BaselineHelperError(
                        (
                            "Refusing to resume baselining because the database has partial Prisma migration "
                            "history and does not match either the recorded migration prefix or the full "
                            f"checked-in migration chain. Review the generated SQL diff at {diff_path} and "
                            "investigate manually before making any changes."
                        )
                    )
                raise BaselineHelperError(
                    (
                        "Failed to compare the database against the recorded Prisma migration prefix: "
                        f"{prefix_diff_result.combined_output or 'prisma migrate diff exited with an error'}"
                    )
                )
            if classification.kind == "legacy_unbaselined":
                matched_repo_prefix = find_matching_repo_prefix(
                    migrations_dir=migrations_dir,
                    repo_migration_names=repo_history_names,
                    schema_path=schema_path,
                    shadow_database_url=shadow_database_url,
                    database_url=args.database_url,
                    runner=runner,
                )
                if matched_repo_prefix is not None:
                    planned_migration_names = matched_repo_prefix
                    if diff_path_was_temporary and diff_path.exists():
                        cleanup_temporary_path(diff_path, was_temporary=True)
                        planned_diff_path = None
                    else:
                        planned_diff_path = diff_path
                    return BaselinePlan(
                        inspection=inspection,
                        classification=classification,
                        status_result=status_result,
                        repo_migration_names=repo_history_names,
                        pending_migration_names=planned_migration_names,
                        diff_path=planned_diff_path,
                        diff_path_was_temporary=diff_path_was_temporary,
                    )
            raise BaselineHelperError(
                (
                    "Refusing to baseline because the live database differs from the checked-in migration chain. "
                    f"Review the generated SQL diff at {diff_path} before making any changes."
                )
            )
        if diff_result.returncode != 0:
            raise BaselineHelperError(
                (
                    "Failed to compare the live database against the checked-in migration chain: "
                    f"{diff_result.combined_output or 'prisma migrate diff exited with an error'}"
                )
            )
        if diff_path_was_temporary and diff_path.exists():
            cleanup_temporary_path(diff_path, was_temporary=True)
            planned_diff_path: Path | None = None
        else:
            planned_diff_path = diff_path
        if classification.kind == "partial_history_prefix":
            planned_migration_names = repo_history_names[len(recorded_history_names) :]
        else:
            planned_migration_names = repo_history_names
    finally:
        cleanup_temporary_path(repo_diff_path, was_temporary=repo_diff_path_was_temporary)
        if prefix_diff_root is not None:
            shutil.rmtree(prefix_diff_root, ignore_errors=True)
        if prepared_root is not None:
            shutil.rmtree(prepared_root, ignore_errors=True)

    return BaselinePlan(
        inspection=inspection,
        classification=classification,
        status_result=status_result,
        repo_migration_names=repo_history_names,
        pending_migration_names=planned_migration_names,
        diff_path=planned_diff_path,
        diff_path_was_temporary=diff_path_was_temporary,
    )


async def run_plan_command(
    args: argparse.Namespace,
    *,
    stdout: TextIO | None = None,
    prisma_factory: Callable[..., Any] = Prisma,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    out_stream = stdout if stdout is not None else sys.stdout
    plan = await build_baseline_plan(args, prisma_factory=prisma_factory, runner=runner)
    recorded_count = len(history_names_from_inspection(plan.inspection))

    print(f"Database: {plan.inspection.database_name}", file=out_stream)
    print(f"Classification: {plan.classification.kind}", file=out_stream)
    print(f"Summary: {plan.classification.summary}", file=out_stream)
    print(
        f"`prisma migrate status` exit code: {plan.status_result.returncode}",
        file=out_stream,
    )
    print_command_output(plan.status_result, stdout=out_stream, stderr=out_stream)
    if (
        plan.classification.kind == "legacy_unbaselined"
        and plan.pending_migration_names != plan.repo_migration_names
    ):
        print(
            "Live database matches a historical repo migration prefix with no recorded Prisma history.",
            file=out_stream,
        )
        print(
            "Would record this matching repo-prefix migration history as applied in lexical order:",
            file=out_stream,
        )
    else:
        print("No schema drift detected between the live database and the checked-in migration chain.", file=out_stream)
        if recorded_count > 0:
            print("Would record these remaining migrations as applied in lexical order:", file=out_stream)
        else:
            print("Would record these migrations as applied in lexical order:", file=out_stream)
    for migration_name in plan.pending_migration_names:
        print(f"  - {migration_name}", file=out_stream)
    print(
        f"Would then run: uv run prisma migrate deploy --schema={args.schema.resolve()}",
        file=out_stream,
    )


async def run_apply_command(
    args: argparse.Namespace,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    prisma_factory: Callable[..., Any] = Prisma,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    if not args.yes:
        raise BaselineHelperError("`apply` requires `--yes` because it writes Prisma migration history.")

    out_stream = stdout if stdout is not None else sys.stdout
    err_stream = stderr if stderr is not None else sys.stderr
    plan = await build_baseline_plan(args, prisma_factory=prisma_factory, runner=runner)
    schema_path = args.schema.resolve()

    print(
        f"Safety checks passed for {plan.inspection.database_name}. Recording migration history...",
        file=out_stream,
    )
    for migration_name in plan.pending_migration_names:
        print(
            f"Running: uv run prisma migrate resolve --applied {migration_name} --schema={schema_path}",
            file=out_stream,
        )
        resolve_result = run_prisma_command(
            ["migrate", "resolve", "--applied", migration_name, "--schema", str(schema_path)],
            database_url=args.database_url,
            runner=runner,
        )
        print_command_output(resolve_result, stdout=out_stream, stderr=err_stream)
        if resolve_result.returncode != 0:
            raise BaselineHelperError(
                (
                    f"Failed to record migration {migration_name} as applied: "
                    f"{resolve_result.combined_output or 'prisma migrate resolve exited with an error'}. "
                    "Rerun `plan` before retrying so the helper can verify whether it is safe to resume."
                )
            )

    print(f"Running: uv run prisma migrate deploy --schema={schema_path}", file=out_stream)
    deploy_result = run_prisma_command(
        ["migrate", "deploy", "--schema", str(schema_path)],
        database_url=args.database_url,
        runner=runner,
    )
    print_command_output(deploy_result, stdout=out_stream, stderr=err_stream)
    if deploy_result.returncode != 0:
        raise BaselineHelperError(
            (
                "Post-baseline `prisma migrate deploy` failed: "
                f"{deploy_result.combined_output or 'prisma migrate deploy exited with an error'}. "
                "Rerun `plan` before retrying so the helper can verify whether it is safe to resume."
            )
        )

    print(f"Running: uv run prisma migrate status --schema={schema_path}", file=out_stream)
    final_status_result = run_prisma_command(
        ["migrate", "status", "--schema", str(schema_path)],
        database_url=args.database_url,
        runner=runner,
    )
    print_command_output(final_status_result, stdout=out_stream, stderr=err_stream)
    if final_status_result.returncode != 0:
        raise BaselineHelperError(
            (
                "Post-baseline `prisma migrate status` did not report a healthy state: "
                f"{final_status_result.combined_output or 'prisma migrate status exited with an error'}. "
                "Rerun `plan` before retrying so the helper can verify whether it is safe to resume."
            )
        )

    post_inspection = await load_inspection_result(
        database_url=args.database_url,
        schema_path=schema_path,
        migrations_dir=args.migrations_dir.resolve(),
        prisma_factory=prisma_factory,
    )
    post_classification = classify_inspection(post_inspection, schema_path=schema_path)
    print(f"Post-baseline classification: {post_classification.kind}", file=out_stream)
    print(f"Summary: {post_classification.summary}", file=out_stream)
    if post_classification.kind != "already_baselined":
        raise BaselineHelperError(
            (
                "Post-baseline validation did not end in an `already_baselined` state: "
                f"{post_classification.kind}. {post_classification.summary} "
                "Rerun `plan` and investigate before retrying."
            )
        )


async def run_internal_inspect_json_command(
    args: argparse.Namespace,
    *,
    stdout: TextIO | None = None,
    prisma_factory: Callable[..., Any] = Prisma,
) -> None:
    out_stream = stdout if stdout is not None else sys.stdout
    migrations_dir = args.migrations_dir.resolve()
    inspection = await inspect_database_state(
        database_url=args.database_url,
        repo_migration_names=load_repo_migration_names(migrations_dir),
        prisma_factory=prisma_factory,
    )
    print(json.dumps(inspection_result_to_payload(inspection)), file=out_stream)


async def run_command(
    args: argparse.Namespace,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    prisma_factory: Callable[..., Any] = Prisma,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    if args.command == "inspect":
        await run_inspect_command(args, stdout=stdout, prisma_factory=prisma_factory)
        return
    if args.command == "plan":
        await run_plan_command(args, stdout=stdout, prisma_factory=prisma_factory, runner=runner)
        return
    if args.command == "apply":
        await run_apply_command(
            args,
            stdout=stdout,
            stderr=stderr,
            prisma_factory=prisma_factory,
            runner=runner,
        )
        return
    if args.command == "_inspect-json":
        await run_internal_inspect_json_command(args, stdout=stdout, prisma_factory=prisma_factory)
        return
    raise BaselineHelperError(f"Unsupported command: {args.command}")


def run_prisma_command(
    arguments: Sequence[str],
    *,
    database_url: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> CommandResult:
    normalized_database_url = require_value(database_url, "DATABASE_URL")
    command = ("uv", "run", "prisma", *arguments)
    try:
        completed = runner(
            list(command),
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, DEFAULT_DATABASE_URL_ENV: normalized_database_url},
        )
    except OSError as exc:
        raise BaselineHelperError(
            f"Failed to launch `{' '.join(command)}`. Check that uv and the Prisma CLI are installed and runnable in this environment: {exc}"
        ) from exc
    return CommandResult(
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def print_command_output(result: CommandResult, *, stdout: TextIO, stderr: TextIO) -> None:
    if result.stdout.strip():
        print(result.stdout.strip(), file=stdout)
    if result.stderr.strip():
        print(result.stderr.strip(), file=stderr)


def prepare_diff_output_path(output_diff: Path | None) -> tuple[Path, bool]:
    if output_diff is not None:
        normalized_path = output_diff.resolve()
        normalized_path.parent.mkdir(parents=True, exist_ok=True)
        return normalized_path, False
    handle = tempfile.NamedTemporaryFile(
        prefix="deltallm-issue94-baseline-",
        suffix=".sql",
        delete=False,
    )
    handle.close()
    return Path(handle.name), True


def cleanup_temporary_path(path: Path | None, *, was_temporary: bool) -> None:
    if not was_temporary or path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        return


def require_value(value: object, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise BaselineHelperError(f"{label} is required")
    return normalized


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        asyncio.run(run_command(args))
    except BaselineHelperError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
