from __future__ import annotations

import argparse
import subprocess
import sys
import time
from collections.abc import Callable
from typing import TextIO

DEFAULT_PRISMA_BOOTSTRAP_ATTEMPTS = 30
DEFAULT_PRISMA_BOOTSTRAP_SLEEP_SECONDS = 2.0
DEFAULT_PRISMA_SCHEMA_PATH = "./prisma/schema.prisma"

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


def _emit_command_output(*, stdout: str, stderr: str, out: TextIO, err: TextIO) -> None:
    if stdout:
        print(stdout, file=out, end="" if stdout.endswith("\n") else "\n")
    if stderr:
        print(stderr, file=err, end="" if stderr.endswith("\n") else "\n")


def run_prisma_bootstrap(
    *,
    schema_path: str = DEFAULT_PRISMA_SCHEMA_PATH,
    max_attempts: int = DEFAULT_PRISMA_BOOTSTRAP_ATTEMPTS,
    sleep_seconds: float = DEFAULT_PRISMA_BOOTSTRAP_SLEEP_SECONDS,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    sleeper: Callable[[float], None] = time.sleep,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> None:
    command = ["prisma", "migrate", "deploy", "--schema", schema_path]
    attempts = max(1, int(max_attempts))
    delay = max(0.0, float(sleep_seconds))
    out_stream = stdout if stdout is not None else sys.stdout
    err_stream = stderr if stderr is not None else sys.stderr

    for attempt in range(1, attempts + 1):
        try:
            result = runner(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            raise PrismaBootstrapError(f"Failed to execute Prisma bootstrap command: {exc}", retryable=False) from exc
        if result.returncode == 0:
            _emit_command_output(stdout=result.stdout, stderr=result.stderr, out=out_stream, err=err_stream)
            return

        combined_output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
        failure_type = classify_prisma_failure(combined_output)
        if failure_type == "retryable_connectivity" and attempt < attempts:
            _emit_command_output(stdout=result.stdout, stderr=result.stderr, out=out_stream, err=err_stream)
            print(
                f"Waiting for database before Prisma migrate deploy... ({attempt}/{attempts})",
                file=err_stream,
            )
            sleeper(delay)
            continue

        _emit_command_output(stdout=result.stdout, stderr=result.stderr, out=out_stream, err=err_stream)
        if failure_type == "retryable_connectivity":
            raise PrismaBootstrapError(
                f"Prisma migrate deploy did not succeed after {attempts} attempts",
                retryable=True,
            )
        raise PrismaBootstrapError("Prisma migrate deploy failed with a non-retryable error", retryable=False)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Prisma migrate deploy with connectivity retries.")
    parser.add_argument("--schema", default=DEFAULT_PRISMA_SCHEMA_PATH)
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_PRISMA_BOOTSTRAP_ATTEMPTS)
    parser.add_argument("--sleep-seconds", type=float, default=DEFAULT_PRISMA_BOOTSTRAP_SLEEP_SECONDS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        run_prisma_bootstrap(
            schema_path=args.schema,
            max_attempts=args.max_attempts,
            sleep_seconds=args.sleep_seconds,
        )
    except PrismaBootstrapError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
