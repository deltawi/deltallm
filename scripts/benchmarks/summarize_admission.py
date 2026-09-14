"""Recompute comparisons and coalescing opportunities from saved raw samples."""

import argparse
from dataclasses import replace
import gzip
import json
from pathlib import Path

from scripts.benchmarks.measure_admission import Sample, coalescing_opportunity


def summarize(directory):
    profiles = []
    for folder in sorted(directory.iterdir()):
        if not folder.is_dir() or not (folder / "summary.json").exists():
            continue
        summary = json.loads((folder / "summary.json").read_text())
        samples = [
            Sample(**json.loads(line))
            for line in gzip.decompress((folder / "samples.jsonl.gz").read_bytes()).splitlines()
        ]
        profiles.append(
            {
                "name": folder.name,
                "offered": len(samples),
                "accepted": summary["accepted_calls"],
                "errors": summary["errors"],
                "failed_before_sql": sum(s.status != "accepted" and s.calls == 0 for s in samples),
                "accepted_p95_ms": summary["latency_seconds_accepted"]["p95"] * 1000,
                "queue_wait_p95_ms": summary["phases_seconds"]["queue_wait"]["p95"] * 1000,
                "occupancy_estimate_mean_ms": summary["phases_seconds"]["lock_occupancy_estimate"][
                    "mean"
                ]
                * 1000,
                "events_per_commit": summary["events_per_commit"],
                "offered_coalescing": coalescing_opportunity(
                    samples, events_per_call=summary["profile"]["batch_size"]
                ),
                "observed_coalescing": coalescing_opportunity(
                    [replace(s, scheduled=s.started) for s in samples],
                    events_per_call=summary["profile"]["batch_size"],
                ),
            }
        )
    (directory / "comparison.json").write_text(json.dumps(profiles, indent=2) + "\n")
    for p in profiles:
        print(
            f"{p['name']}: {p['accepted']}/{p['offered']}; p95 {p['accepted_p95_ms']:.2f} ms; "
            f"lock wait p95 {p['queue_wait_p95_ms']:.3f} ms; "
            f"observed coalescing {p['observed_coalescing']['optimistic_events_per_commit']:.3f}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    summarize(parser.parse_args().directory)
