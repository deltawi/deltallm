from __future__ import annotations

import importlib
import subprocess
import sys

import pytest


def _completed_process(
    *, returncode: int, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["prisma", "migrate", "deploy"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _prisma_bootstrap_module():
    return importlib.import_module("src.prisma_bootstrap")


def test_prisma_bootstrap_module_does_not_import_src_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for module_name in [
        name for name in sys.modules if name == "src.bootstrap" or name.startswith("src.bootstrap.")
    ]:
        monkeypatch.delitem(sys.modules, module_name, raising=False)
    monkeypatch.delitem(sys.modules, "src.prisma_bootstrap", raising=False)

    module = importlib.import_module("src.prisma_bootstrap")

    assert module is not None
    assert "src.bootstrap" not in sys.modules
    assert not any(name.startswith("src.bootstrap.") for name in sys.modules)


def test_classify_prisma_failure_marks_connectivity_errors_retryable() -> None:
    module = _prisma_bootstrap_module()

    assert (
        module.classify_prisma_failure("Error: P1001: Can't reach database server")
        == "retryable_connectivity"
    )
    assert (
        module.classify_prisma_failure("Database system is starting up") == "retryable_connectivity"
    )
    assert module.classify_prisma_failure("migration failed because type already exists") == "fatal"


def test_run_prisma_bootstrap_retries_retryable_connectivity_errors_then_succeeds(
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


def test_run_prisma_bootstrap_recovers_known_model_identity_failure_before_deploy(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _prisma_bootstrap_module()
    calls: list[list[str]] = []
    results = iter(
        [
            _completed_process(returncode=0, stdout="Recovery preflight completed"),
            _completed_process(returncode=0, stdout="Migration marked as rolled back"),
            _completed_process(returncode=0, stdout="Prisma migrate deploy completed"),
        ]
    )

    def fake_runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return next(results)

    module.run_prisma_bootstrap(
        schema_path="./prisma/schema.prisma",
        recover_model_api_identity=True,
        runner=fake_runner,
    )

    captured = capsys.readouterr()
    assert calls == [
        [
            "prisma",
            "db",
            "execute",
            "--schema",
            "./prisma/schema.prisma",
            "--file",
            str(module.MODEL_API_IDENTITY_RECOVERY_SQL),
        ],
        [
            "prisma",
            "migrate",
            "resolve",
            "--rolled-back",
            module.MODEL_API_IDENTITY_MIGRATION,
            "--schema",
            "./prisma/schema.prisma",
        ],
        ["prisma", "migrate", "deploy", "--schema", "./prisma/schema.prisma"],
    ]
    assert "Recovery preflight completed" in captured.out
    assert "Migration marked as rolled back" in captured.out
    assert "Prisma migrate deploy completed" in captured.out


def test_model_identity_recovery_stops_before_resolve_when_preflight_fails(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _prisma_bootstrap_module()
    calls: list[list[str]] = []

    def fake_runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return _completed_process(returncode=1, stderr="unexpected migration state")

    with pytest.raises(module.PrismaBootstrapError, match="recovery preflight failed"):
        module.run_prisma_bootstrap(
            recover_model_api_identity=True,
            runner=fake_runner,
        )

    captured = capsys.readouterr()
    assert len(calls) == 1
    assert calls[0][:3] == ["prisma", "db", "execute"]
    assert "unexpected migration state" in captured.err


def test_model_identity_recovery_stops_before_deploy_when_resolve_fails() -> None:
    module = _prisma_bootstrap_module()
    calls: list[list[str]] = []
    results = iter(
        [
            _completed_process(returncode=0),
            _completed_process(returncode=1, stderr="migration is not failed"),
        ]
    )

    def fake_runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return next(results)

    with pytest.raises(module.PrismaBootstrapError, match="could not mark"):
        module.run_prisma_bootstrap(
            recover_model_api_identity=True,
            runner=fake_runner,
        )

    assert len(calls) == 2
    assert calls[1][:3] == ["prisma", "migrate", "resolve"]


def test_run_prisma_bootstrap_raises_immediately_on_fatal_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _prisma_bootstrap_module()
    calls: list[list[str]] = []

    def fake_runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return _completed_process(returncode=1, stderr="ERROR: relation already exists")

    with pytest.raises(module.PrismaBootstrapError, match="non-retryable"):
        module.run_prisma_bootstrap(
            max_attempts=5,
            runner=fake_runner,
            sleeper=lambda _: None,
        )

    captured = capsys.readouterr()
    assert calls == [["prisma", "migrate", "deploy", "--schema", "./prisma/schema.prisma"]]
    assert "relation already exists" in captured.err


def test_run_prisma_bootstrap_raises_after_retry_budget_exhausted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _prisma_bootstrap_module()
    sleeps: list[float] = []

    def fake_runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        del command
        return _completed_process(
            returncode=1, stderr="Error: P1002: Timed out while connecting to database"
        )

    with pytest.raises(
        module.PrismaBootstrapError, match="did not succeed after 2 attempts"
    ) as exc_info:
        module.run_prisma_bootstrap(
            max_attempts=2,
            sleep_seconds=1.5,
            runner=fake_runner,
            sleeper=sleeps.append,
        )

    captured = capsys.readouterr()
    assert exc_info.value.retryable is True
    assert sleeps == [1.5]
    assert "Waiting for database before Prisma migrate deploy... (1/2)" in captured.err
