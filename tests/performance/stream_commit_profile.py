"""Isolate the stream-completion fix with the existing fixed-provider profile.

Run from the repository root. No live provider or credential is used. The five
changed modules come from the pinned pre-fix revision for --before; all other
code and dependencies use the current worktree in both measurements.
"""

import argparse
import asyncio
import importlib.abc
import importlib.util
import logging
from pathlib import Path
import subprocess
import sys

BASELINE = "d41b66afe43154181f1c21f02b1ac9b372c2714a"
MODULES = frozenset(
    (
        "src.chat.stream_response",
        "src.chat.stream_usage",
        "src.chat.telemetry",
        "src.routers.chat",
        "src.telemetry.request_failures",
    )
)


class BeforeLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in MODULES:
            return importlib.util.spec_from_loader(fullname, self)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        relative = module.__name__.replace(".", "/") + ".py"
        source = subprocess.run(
            ["git", "show", f"{BASELINE}:{relative}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        # This is pinned, trusted repository source, never provider/client input.
        exec(compile(source, relative, "exec"), module.__dict__)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.before:
        sys.meta_path.insert(0, BeforeLoader())
    from tests.performance.realtime_selector_profile import measure

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("src.services.key_service").setLevel(logging.WARNING)
    for selector in (False, True):
        await measure(args.output_dir, selector=selector, streaming=True, independent=True)


if __name__ == "__main__":
    asyncio.run(main())
