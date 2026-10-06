"""Smoke-test the shipped non-root environment and its blocked-callback exit bound."""

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from uuid import uuid4

CHECK = """
import importlib.util, os, subprocess, sys, uvicorn
assert os.getuid() == 10001
assert uvicorn.__version__ == "0.40.0"
assert importlib.util.find_spec("pytest") is None
subprocess.run(["prisma", "-v"], check=True, timeout=30)
subprocess.run(["python", "-m", "src.server", "--help"], check=True, timeout=15)
if sys.argv[1] == "true":
    from unittest.mock import patch
    from src.guardrails.presidio_runtime import build_analyzer
    # A no-network container alone would allow a failed download with fallback.
    # Fail on the attempt itself, even if the library catches the exception.
    with patch("requests.sessions.Session.send", side_effect=RuntimeError("Unexpected network attempt")) as send:
        assert build_analyzer().analyze(text="test@example.com", language="en", entities=["EMAIL_ADDRESS"])
        send.assert_not_called()
"""

MIGRATION_CHECK = """
import os, shutil, subprocess
assert os.getuid() == 10001
assert shutil.which("prisma") == "/opt/prisma/binaries/node_modules/.bin/prisma"
subprocess.run(["prisma", "-v"], check=True, timeout=30)
"""

DATABASE_IMPORT_CHECK = """
import json, os, resource, time
assert os.getuid() == 10001
started = time.monotonic()
from prisma import Prisma
from src.bootstrap.accounting_worker_app import create_accounting_worker_app
from src.main import app
assert app is not None and callable(create_accounting_worker_app)
print(json.dumps({"import_seconds": time.monotonic() - started,
                  "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))
"""


@dataclass(frozen=True)
class ImageSmokeCheck:
    label: str
    command: list[str]
    expected: int
    memory: str


def image_smoke_checks(presidio: bool) -> tuple[ImageSmokeCheck, ...]:
    return (
        ImageSmokeCheck("runtime", ["python", "-c", CHECK, str(presidio).lower()], 0, "2g"),
        ImageSmokeCheck("database-import", ["python", "-c", DATABASE_IMPORT_CHECK], 0, "1g"),
        ImageSmokeCheck("migration-cli", ["python", "-c", MIGRATION_CHECK], 0, "1g"),
        ImageSmokeCheck("callback", ["python", "/fixture/blocked.py"], 70, "2g"),
        ImageSmokeCheck(
            "cancelled-cleanup", ["python", "/fixture/blocked.py", "--cancel-cleanup"], 70, "2g"
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--presidio", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    metadata = json.loads(subprocess.check_output(["docker", "image", "inspect", args.image]))[0]
    (args.output / "image.json").write_text(
        json.dumps(
            {
                "id": metadata["Id"],
                "architecture": metadata["Architecture"],
                "user": metadata["Config"]["User"],
                "command": metadata["Config"]["Cmd"],
            },
            indent=2,
        )
        + "\n"
    )
    assert metadata["Config"]["Cmd"] == ["python", "-m", "src.server"]
    fixture = Path("tests/performance/lifecycle_blocked_callback.py").resolve()
    for check in image_smoke_checks(args.presidio):
        name = "deltallm-pr8-smoke-" + uuid4().hex[:8]
        try:
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--name",
                    name,
                    "--network",
                    "none",
                    "--read-only",
                    "--tmpfs",
                    "/tmp:rw,size=128m",
                    "--memory",
                    check.memory,
                    "--cpus",
                    "2",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--env",
                    "PYTHONPATH=/app",
                    "--mount",
                    f"type=bind,source={fixture},target=/fixture/blocked.py,readonly",
                    args.image,
                    *check.command,
                ],
                capture_output=True,
                text=True,
                timeout=90,
            )
            (args.output / (check.label + ".log")).write_text(result.stdout + result.stderr)
            assert result.returncode == check.expected, (
                check.label,
                result.returncode,
                result.stderr[-2000:],
            )
            assert "AssertionError" not in result.stderr
            if check.expected == 70:
                assert "forced exit" in result.stderr
        finally:
            subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=15)


if __name__ == "__main__":
    main()
