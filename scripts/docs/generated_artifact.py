"""Shared write-or-check behavior for deterministic documentation artifacts."""

from __future__ import annotations

from difflib import unified_diff
from pathlib import Path


def write_or_check(path: Path, content: str, *, check: bool) -> bool:
    """Write generated content, or report drift without modifying the file."""

    normalized = content.rstrip() + "\n"
    if not check:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(normalized, encoding="utf-8")
        print(f"Generated {path}")
        return True

    if not path.is_file():
        print(f"ERROR: generated artifact is missing: {path}")
        return False

    current = path.read_text(encoding="utf-8")
    if current == normalized:
        print(f"Generated artifact is current: {path}")
        return True

    print(f"ERROR: generated artifact is stale: {path}")
    diff = unified_diff(
        current.splitlines(),
        normalized.splitlines(),
        fromfile=str(path),
        tofile=f"{path} (generated)",
        lineterm="",
    )
    for index, line in enumerate(diff):
        if index >= 200:
            print("... diff truncated ...")
            break
        print(line)
    return False
