from __future__ import annotations

import importlib
import subprocess
import sys

import pytest


def _completed_process(
    *,
    returncode: int,
    stdout: str = "",
    stderr: str = "",
    args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=args or ["prisma", "migrate", "deploy"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _prisma_bootstrap_module():
    return importlib.import_module("src.prisma_bootstrap")


def test_prisma_bootstrap_module_does_not_import_src_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    for module_name in [name for name in sys.modules if name == "src.bootstrap" or name.startswith("src.bootstrap.")]:
        monkeypatch.delitem(sys.modules, module_name, raising=False)
    monkeypatch.delitem(sys.modules, "src.prisma_bootstrap", raising=False)

    module = importlib.import_module("src.prisma_bootstrap")

    assert module is not None
    assert "src.bootstrap" not in sys.modules
    assert not any(name.startswith("src.bootstrap.") for name in sys.modules)


def test_classify_prisma_failure_marks_connectivity_errors_retryable() -> None:
    module = _prisma_bootstrap_module()

    assert module.classify_prisma_failure("Error: P1001: Can't reach database server") == "retryable_connectivity"
    assert module.classify_prisma_failure("Database system is starting up") == "retryable_connectivity"
    assert module.classify_prisma_failure("migration failed because type already exists") == "fatal"


def test_normalize_prisma_bootstrap_mode_rejects_invalid_mode() -> None:
    module = _prisma_bootstrap_module()

    with pytest.raises(ValueError, match="Prisma bootstrap mode must be one of"):
        module.normalize_prisma_bootstrap_mode("migrate")


def test_run_prisma_bootstrap_skip_mode_returns_without_running_commands() -> None:
    module = _prisma_bootstrap_module()
    calls: list[list[str]] = []

    def fake_runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return _completed_process(returncode=0)

    module.run_prisma_bootstrap(
        mode="skip",
        runner=fake_runner,
        sleeper=lambda _: None,
    )

    assert calls == []


def test_run_prisma_bootstrap_deploy_mode_retries_retryable_connectivity_errors_then_succeeds(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _prisma_bootstrap_module()
    calls: list[list[str]] = []
    sleeps: list[float] = []
    results = iter(
        [
            _completed_process(returncode=1, stderr="Error: P1001: Can't reach database server"),
            _completed_process(returncode=0, stdout="Prisma migrate deploy completed"),
        ]
    )

    def fake_runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return next(results)

    module.run_prisma_bootstrap(
        mode="deploy",
        schema_path="./prisma/schema.prisma",
        max_attempts=3,
        sleep_seconds=0.25,
        runner=fake_runner,
        sleeper=sleeps.append,
    )

    captured = capsys.readouterr()
    assert calls == [
        ["prisma", "migrate", "deploy", "--schema", "./prisma/schema.prisma"],
        ["prisma", "migrate", "deploy", "--schema", "./prisma/schema.prisma"],
    ]
    assert sleeps == [0.25]
    assert "Waiting for database before Prisma migrate deploy... (1/3)" in captured.err
    assert "Prisma migrate deploy completed" in captured.out


def test_run_prisma_bootstrap_verify_mode_uses_migrate_status(capsys: pytest.CaptureFixture[str]) -> None:
    module = _prisma_bootstrap_module()
    calls: list[list[str]] = []

    def fake_runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return _completed_process(
            returncode=0,
            stdout="Database schema is up to date",
            args=command,
        )

    module.run_prisma_bootstrap(
        mode="verify",
        schema_path="./prisma/schema.prisma",
        runner=fake_runner,
        sleeper=lambda _: None,
    )

    captured = capsys.readouterr()
    assert calls == [["prisma", "migrate", "status", "--schema", "./prisma/schema.prisma"]]
    assert "Database schema is up to date" in captured.out


def test_run_prisma_bootstrap_raises_immediately_on_fatal_error(capsys: pytest.CaptureFixture[str]) -> None:
    module = _prisma_bootstrap_module()
    calls: list[list[str]] = []

    def fake_runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return _completed_process(returncode=1, stderr="ERROR: relation already exists")

    with pytest.raises(module.PrismaBootstrapError, match="non-retryable"):
        module.run_prisma_bootstrap(
            mode="deploy",
            max_attempts=5,
            runner=fake_runner,
            sleeper=lambda _: None,
        )

    captured = capsys.readouterr()
    assert calls == [["prisma", "migrate", "deploy", "--schema", "./prisma/schema.prisma"]]
    assert "relation already exists" in captured.err


def test_run_prisma_bootstrap_raises_after_retry_budget_exhausted(capsys: pytest.CaptureFixture[str]) -> None:
    module = _prisma_bootstrap_module()
    sleeps: list[float] = []

    def fake_runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        del command
        return _completed_process(returncode=1, stderr="Error: P1002: Timed out while connecting to database")

    with pytest.raises(module.PrismaBootstrapError, match="did not succeed after 2 attempts") as exc_info:
        module.run_prisma_bootstrap(
            mode="deploy",
            max_attempts=2,
            sleep_seconds=1.5,
            runner=fake_runner,
            sleeper=sleeps.append,
        )

    captured = capsys.readouterr()
    assert exc_info.value.retryable is True
    assert sleeps == [1.5]
    assert "Waiting for database before Prisma migrate deploy... (1/2)" in captured.err


def test_run_prisma_bootstrap_verify_mode_retries_connectivity_errors(capsys: pytest.CaptureFixture[str]) -> None:
    module = _prisma_bootstrap_module()
    calls: list[list[str]] = []
    sleeps: list[float] = []
    results = iter(
        [
            _completed_process(returncode=1, stderr="Error: P1001: Can't reach database server"),
            _completed_process(returncode=0, stdout="No pending migrations to apply"),
        ]
    )

    def fake_runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return next(results)

    module.run_prisma_bootstrap(
        mode="verify",
        max_attempts=3,
        sleep_seconds=0.5,
        runner=fake_runner,
        sleeper=sleeps.append,
    )

    captured = capsys.readouterr()
    assert calls == [
        ["prisma", "migrate", "status", "--schema", "./prisma/schema.prisma"],
        ["prisma", "migrate", "status", "--schema", "./prisma/schema.prisma"],
    ]
    assert sleeps == [0.5]
    assert "Waiting for database before Prisma migrate status... (1/3)" in captured.err


def test_run_prisma_bootstrap_verify_mode_fails_on_pending_or_broken_state(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _prisma_bootstrap_module()

    def fake_runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        del command
        return _completed_process(returncode=1, stderr="Following migration have not yet been applied")

    with pytest.raises(module.PrismaBootstrapError, match="Prisma migrate status failed with a non-retryable error"):
        module.run_prisma_bootstrap(
            mode="verify",
            runner=fake_runner,
            sleeper=lambda _: None,
        )

    captured = capsys.readouterr()
    assert "Following migration have not yet been applied" in captured.err
