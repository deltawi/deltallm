"""Recompute the published historical campaign from sanitized numeric samples."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path


def summarize(directory: Path) -> dict[str, object]:
    manifest = json.loads((directory / "manifest.json").read_text())
    stages = []
    causes: Counter[str] = Counter()
    for stage in manifest["stages"]:
        samples = [
            json.loads(line) for line in (directory / stage["samples"]).read_text().splitlines()
        ]
        successes = [
            row
            for row in samples
            if row["status_code"] is not None and 200 <= row["status_code"] < 300
        ]
        latency = sorted(row["latency_seconds"] for row in successes)
        errors = Counter(row["error_code"] for row in samples if row["error_code"] is not None)
        causes.update(errors)
        stages.append(
            {
                "client_concurrency": stage["client_concurrency"],
                "started": len(samples),
                "success": len(successes),
                "success_percent": 100 * len(successes) / len(samples),
                "successful_rps_including_drain": len(successes)
                / (stage["arrival_window_seconds"] + stage["drain_window_seconds"]),
                "success_only_p95_seconds": latency[max(0, math.ceil(len(latency) * 0.95) - 1)],
                "failure_causes": dict(sorted(errors.items())),
            }
        )
    return {
        "workload": "historical-closed-loop-mixed-provider",
        "measured_deployment_revision": None,
        "per_pod_capacity_certified": False,
        "stages": stages,
        "failure_causes_total": dict(sorted(causes.items())),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.directory), indent=2, sort_keys=True))
