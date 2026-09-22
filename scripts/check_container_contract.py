"""Check generated requirements and the cache-free Railway Dockerfile."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess


def railway_dockerfile() -> str:
    source = Path("Dockerfile").read_text()
    source = re.sub(r"--mount=type=cache,target=[^\s]+\s+", "", source)
    lines = source.splitlines(keepends=True)
    lines.insert(
        1,
        "# Generated from Dockerfile by scripts/check_container_contract.py --write.\n"
        "# Railway uses the same runtime without BuildKit cache mounts.\n",
    )
    return "".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    export = (
        subprocess.run(
            [
                "uv",
                "export",
                "--frozen",
                "--no-dev",
                "--no-emit-project",
                "--no-hashes",
                "--format",
                "requirements-txt",
                "--output-file",
                "requirements.txt",
            ],
            check=True,
            text=True,
            capture_output=True,
            timeout=60,
        )
        if args.write
        else subprocess.run(
            [
                "uv",
                "export",
                "--frozen",
                "--no-dev",
                "--no-emit-project",
                "--no-hashes",
                "--format",
                "requirements-txt",
            ],
            check=True,
            text=True,
            capture_output=True,
            timeout=60,
        )
    )
    railway = Path("deploy/railway/Dockerfile")
    if args.write:
        railway.write_text(railway_dockerfile())
    else:
        # uv's generated header records the command; compare package content.
        def packages(value: str) -> str:
            return "\n".join(line for line in value.splitlines() if not line.startswith("#"))

        if packages(export.stdout) != packages(Path("requirements.txt").read_text()):
            raise SystemExit("requirements.txt is stale; run this script with --write")
        if railway.read_text() != railway_dockerfile():
            raise SystemExit("Railway Dockerfile is stale; run this script with --write")


if __name__ == "__main__":
    main()
