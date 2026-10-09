from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TextIO

from src.migration_process import MigrationOutputLimitError, run_migration_process

DEFAULT_PRISMA_BOOTSTRAP_ATTEMPTS = 30
DEFAULT_PRISMA_BOOTSTRAP_SLEEP_SECONDS = 2.0
DEFAULT_PRISMA_BOOTSTRAP_TIMEOUT_SECONDS = 300.0
DEFAULT_PRISMA_SCHEMA_PATH = "./prisma/schema.prisma"
MODEL_API_IDENTITY_MIGRATION = "20260927150000_model_api_identity"
MODEL_API_IDENTITY_RECOVERY_SQL = (
    Path(__file__).resolve().parent / "migration_recovery" / f"{MODEL_API_IDENTITY_MIGRATION}.sql"
)

_RETRYABLE_CONNECTIVITY_MARKERS = (
    "p1001",
    "p1002",
    "can't reach database server",
    "database system is starting up",
    "connection refused",
    "connection reset by peer",
    "connection timed out",
    "could not connect to server",
    "network is unreachable",
)


class PrismaBootstrapError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = bool(retryable)


def classify_prisma_failure(output: str) -> str:
    normalized = str(output or "").lower()
    if any(marker in normalized for marker in _RETRYABLE_CONNECTIVITY_MARKERS):
        return "retryable_connectivity"
    return "fatal"


def _safe_output(output: str) -> str:
    # Prisma can echo a credentialed datasource URL or an environment value.
    output = re.sub(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s\"'<>]+", "[redacted URL]", output)
    return re.sub(r"(?i)(password|token|secret)\s*[=:]\s*[^\s,;]+", r"\1=[redacted]", output)


def _emit_command_output(*, stdout: str, stderr: str, out: TextIO, err: TextIO) -> None:
    stdout, stderr = _safe_output(stdout), _safe_output(stderr)
    if stdout:
        print(stdout, file=out, end="" if stdout.endswith("\n") else "\n")
    if stderr:
        print(stderr, file=err, end="" if stderr.endswith("\n") else "\n")


def run_model_api_identity_recovery(
    *,
    schema_path: str = DEFAULT_PRISMA_SCHEMA_PATH,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_migration_process,
    timeout_seconds: float = DEFAULT_PRISMA_BOOTSTRAP_TIMEOUT_SECONDS,
    environment: Mapping[str, str] | None = None,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> None:
    """Repair the one known v0.1.48 migration failure before normal deployment.

    This path is deliberately opt-in. The SQL preflight proves that Prisma recorded
    the expected missing-pgcrypto failure and that PostgreSQL rolled back every
    schema step before migration history is changed through ``migrate resolve``.
    """

    out_stream = stdout if stdout is not None else sys.stdout
    err_stream = stderr if stderr is not None else sys.stderr
    if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 600:
        raise ValueError("migration timeout must be finite and between 0 and 600 seconds")
    deadline = (
        min(clock() + timeout_seconds, deadline)
        if deadline is not None
        else clock() + timeout_seconds
    )
    commands = (
        (
            [
                "prisma",
                "db",
                "execute",
                "--schema",
                schema_path,
                "--file",
                str(MODEL_API_IDENTITY_RECOVERY_SQL),
            ],
            "Model API identity recovery preflight failed",
        ),
        (
            [
                "prisma",
                "migrate",
                "resolve",
                "--rolled-back",
                MODEL_API_IDENTITY_MIGRATION,
                "--schema",
                schema_path,
            ],
            "Prisma could not mark the model API identity migration as rolled back",
        ),
    )
    for command, failure_message in commands:
        remaining = deadline - clock()
        if remaining <= 0:
            raise PrismaBootstrapError(
                "Prisma migration wall-time budget exhausted", retryable=False
            )
        try:
            result = runner(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=remaining,
                env=environment,
            )
        except subprocess.TimeoutExpired:
            raise PrismaBootstrapError(
                "Prisma migration wall-time budget exhausted", retryable=False
            ) from None
        except MigrationOutputLimitError:
            raise PrismaBootstrapError(
                "Prisma migration output exceeded its bound", retryable=False
            ) from None
        except OSError:
            raise PrismaBootstrapError(
                "Failed to execute model API identity recovery command",
                retryable=False,
            ) from None
        _emit_command_output(
            stdout=result.stdout,
            stderr=result.stderr,
            out=out_stream,
            err=err_stream,
        )
        if result.returncode != 0:
            raise PrismaBootstrapError(failure_message, retryable=False)


def run_prisma_bootstrap(
    *,
    schema_path: str = DEFAULT_PRISMA_SCHEMA_PATH,
    max_attempts: int = DEFAULT_PRISMA_BOOTSTRAP_ATTEMPTS,
    sleep_seconds: float = DEFAULT_PRISMA_BOOTSTRAP_SLEEP_SECONDS,
    timeout_seconds: float = DEFAULT_PRISMA_BOOTSTRAP_TIMEOUT_SECONDS,
    environment: Mapping[str, str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_migration_process,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    recover_model_api_identity: bool = False,
) -> None:
    if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 600:
        raise ValueError("migration timeout must be finite and between 0 and 600 seconds")
    deadline = clock() + timeout_seconds
    command = ["prisma", "migrate", "deploy", "--schema", schema_path]
    attempts = max(1, int(max_attempts))
    delay = max(0.0, float(sleep_seconds))
    out_stream = stdout if stdout is not None else sys.stdout
    err_stream = stderr if stderr is not None else sys.stderr

    if recover_model_api_identity:
        run_model_api_identity_recovery(
            schema_path=schema_path,
            runner=runner,
            timeout_seconds=timeout_seconds,
            deadline=deadline,
            environment=environment,
            clock=clock,
            stdout=out_stream,
            stderr=err_stream,
        )

    for attempt in range(1, attempts + 1):
        remaining = deadline - clock()
        if remaining <= 0:
            raise PrismaBootstrapError(
                "Prisma migration wall-time budget exhausted", retryable=False
            )
        try:
            result = runner(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=remaining,
                env=environment,
            )
        except subprocess.TimeoutExpired:
            raise PrismaBootstrapError(
                "Prisma migration wall-time budget exhausted", retryable=False
            ) from None
        except MigrationOutputLimitError:
            raise PrismaBootstrapError(
                "Prisma migration output exceeded its bound", retryable=False
            ) from None
        except OSError:
            raise PrismaBootstrapError(
                "Failed to execute Prisma bootstrap command", retryable=False
            ) from None
        if result.returncode == 0:
            _emit_command_output(
                stdout=result.stdout, stderr=result.stderr, out=out_stream, err=err_stream
            )
            return

        combined_output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
        failure_type = classify_prisma_failure(combined_output)
        if failure_type == "retryable_connectivity" and attempt < attempts:
            _emit_command_output(
                stdout=result.stdout, stderr=result.stderr, out=out_stream, err=err_stream
            )
            print(
                f"Waiting for database before Prisma migrate deploy... ({attempt}/{attempts})",
                file=err_stream,
            )
            sleeper(min(delay, max(0, deadline - clock())))
            continue

        _emit_command_output(
            stdout=result.stdout, stderr=result.stderr, out=out_stream, err=err_stream
        )
        if failure_type == "retryable_connectivity":
            raise PrismaBootstrapError(
                f"Prisma migrate deploy did not succeed after {attempts} attempts",
                retryable=True,
            )
        raise PrismaBootstrapError(
            f"Prisma migrate deploy failed with a non-retryable error (exit code {result.returncode})",
            retryable=False,
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Prisma migrate deploy with connectivity retries."
    )
    parser.add_argument("--schema", default=DEFAULT_PRISMA_SCHEMA_PATH)
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_PRISMA_BOOTSTRAP_ATTEMPTS)
    parser.add_argument(
        "--sleep-seconds", type=float, default=DEFAULT_PRISMA_BOOTSTRAP_SLEEP_SECONDS
    )
    parser.add_argument(
        "--timeout-seconds", type=float, default=DEFAULT_PRISMA_BOOTSTRAP_TIMEOUT_SECONDS
    )
    parser.add_argument(
        "--recover-model-api-identity",
        action="store_true",
        help=(
            "Recover only the known failed v0.1.48 model API identity migration, "
            "then run the normal migration deployment"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        run_prisma_bootstrap(
            schema_path=args.schema,
            max_attempts=args.max_attempts,
            sleep_seconds=args.sleep_seconds,
            timeout_seconds=args.timeout_seconds,
            recover_model_api_identity=args.recover_model_api_identity,
        )
    except PrismaBootstrapError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
