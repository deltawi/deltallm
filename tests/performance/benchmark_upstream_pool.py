"""Compare idle-pool housekeeping CPU; this is not a gateway RPS result."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import process_time

import httpcore
import httpcore2


class IdleConnection:
    def is_closed(self) -> bool:
        return False

    def has_expired(self) -> bool:
        return False

    def is_idle(self) -> bool:
        return True

    def is_available(self) -> bool:
        return True

    def is_connected(self) -> bool:
        return True

    def can_multiplex(self) -> bool:
        return False


def benchmark(iterations: int) -> dict[str, object]:
    rows = []
    for library in (httpcore, httpcore2):
        for size in (10, 50, 100):
            pool = library.AsyncConnectionPool(
                max_connections=500, max_keepalive_connections=100
            )
            # Isolated fresh library objects only; never mutate application pools.
            pool._connections = [IdleConnection() for _ in range(size)]
            started = process_time()
            for _ in range(iterations):
                pool._assign_requests_to_connections()
            elapsed = process_time() - started
            rows.append(
                {
                    "library": library.__name__, "version": library.__version__,
                    "idle_connections": size, "iterations": iterations,
                    "cpu_seconds": elapsed,
                    "mean_cpu_microseconds": elapsed / iterations * 1e6,
                }
            )
    return {
        "kind": "isolated fake-idle-pool CPU comparison, not qualification",
        "results": rows,
        "caveat": "Fake socket checks are cheaper than real checks. No RPS gain is inferred.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=5000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 100000:
        parser.error("iterations must be from 1 to 100000")
    if args.output.exists():
        parser.error("use a fresh output file")
    result = benchmark(args.iterations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
