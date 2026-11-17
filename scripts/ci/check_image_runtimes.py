"""Fail when an image's Node.js or CPython runtime is behind a security release.

Trivy finds Debian, pip and npm packages, but not the Node.js binary copied from
the official node image or the CPython build under /usr/local, so neither shows
up in its results. This check reads the runtime versions from the image (or
from --node/--python) and compares them with the upstream release feeds:

- Node.js: https://nodejs.org/dist/index.json marks each release that carries
  security fixes. A newer release in the same major with ``security: true``
  means the image is exposed.
- CPython: https://peps.python.org/api/release-cycle.json says whether the
  branch is in security-only mode; every release on such a branch is a security
  release, listed by https://www.python.org/api/v2/downloads/release/.

A security release younger than --grace-days only warns: the official images
and the 7-day Dependabot cooldown need time before the base image can move.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import subprocess
import sys
import urllib.request

NODE_INDEX = "https://nodejs.org/dist/index.json"
PYTHON_CYCLE = "https://peps.python.org/api/release-cycle.json"
PYTHON_RELEASES = "https://www.python.org/api/v2/downloads/release/?is_published=true"


@dataclass(frozen=True)
class Behind:
    runtime: str
    current: str
    fixed_in: str
    released: date


def parse_version(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.lstrip("v").split("."))


def fetch_json(url: str) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": "deltallm-ci"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def node_security_releases_after(current: str, index: list[dict]) -> list[Behind]:
    ours = parse_version(current)
    found = []
    for release in index:
        if not all(part.isdigit() for part in release["version"].lstrip("v").split(".")):
            continue  # pre-releases such as v26.0.0-alpha
        version = parse_version(release["version"])
        if version[0] == ours[0] and version > ours and release.get("security"):
            found.append(
                Behind("Node.js", current, release["version"].lstrip("v"), date.fromisoformat(release["date"]))
            )
    return found


def python_security_releases_after(current: str, cycle: dict, releases: list[dict]) -> list[Behind]:
    ours = parse_version(current)
    branch = f"{ours[0]}.{ours[1]}"
    status = cycle.get(branch, {}).get("status")
    if status != "security":
        # In bugfix mode not every release carries security fixes; Dependabot
        # proposes the patch bump and pip-audit/Trivy cover the rest.
        return []
    found = []
    for release in releases:
        name = release["name"]
        if not name.startswith(f"Python {branch}.") or release.get("pre_release"):
            continue
        number = name.removeprefix("Python ")
        if not all(part.isdigit() for part in number.split(".")):
            continue
        if parse_version(number) > ours:
            released = datetime.fromisoformat(release["release_date"].replace("Z", "+00:00")).date()
            found.append(Behind("CPython", current, number, released))
    return found


def image_versions(image: str) -> tuple[str, str]:
    def run(*command: str) -> str:
        return subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", command[0], image, *command[1:]],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        ).stdout.strip()

    node = run("node", "-p", "process.versions.node")
    python = run("python", "-c", "import platform; print(platform.python_version())")
    return node, python


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image", nargs="?", help="image to read runtime versions from")
    parser.add_argument("--node", help="Node.js version instead of reading the image")
    parser.add_argument("--python", help="CPython version instead of reading the image")
    parser.add_argument("--grace-days", type=int, default=14)
    args = parser.parse_args()

    if args.image:
        node, python = image_versions(args.image)
    elif args.node and args.python:
        node, python = args.node, args.python
    else:
        parser.error("pass an image, or both --node and --python")

    behind = node_security_releases_after(node, fetch_json(NODE_INDEX))  # type: ignore[arg-type]
    behind += python_security_releases_after(
        python,
        fetch_json(PYTHON_CYCLE),  # type: ignore[arg-type]
        fetch_json(PYTHON_RELEASES),  # type: ignore[arg-type]
    )

    print(f"Node.js {node}, CPython {python}")
    today = datetime.now(timezone.utc).date()
    failing = False
    for item in sorted(behind, key=lambda b: (b.runtime, b.released)):
        age = (today - item.released).days
        overdue = age > args.grace_days
        failing |= overdue
        level = "error" if overdue else "warning"
        print(
            f"::{level}::{item.runtime} {item.current} is behind security release "
            f"{item.fixed_in} ({item.released}, {age} days ago)"
        )
    if not behind:
        print("Both runtimes are at or past their latest security release.")
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
