"""Export saved qualification results without changing their pass decisions."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shutil

RATES = (50, 100, 200, 500)


def checksum(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def summarize_stage(path: Path, root: Path) -> dict[str, object]:
    report = json.loads(path.read_text())
    rate = report["target_rate_rps"]
    if rate not in RATES:
        raise ValueError("Evidence must use a supported qualification rate")
    campaign = path.relative_to(root).parts[0]
    manifest_path = root / campaign / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    raw = path.parent / Path(report["raw"]).name
    if not raw.is_file() or raw.is_symlink():
        raise ValueError("A stage requires its original local raw samples")
    queue = report["latency_and_queue"]
    latency = report["latency_seconds"]
    drain = report["accounting_drain"]
    reconciliation = report["accounting_reconciliation"]
    return {
        "campaign": campaign,
        "stage": path.parent.name,
        "original_report": path.relative_to(root).as_posix(),
        "original_report_sha256": checksum(path),
        "raw_samples": raw.relative_to(root).as_posix(),
        "raw_samples_sha256": checksum(raw),
        "started_at": report["started_at"],
        "phase": report["phase"],
        "purpose": manifest.get("purpose"),
        "candidate_commit": manifest.get("candidate_commit"),
        "candidate_source_sha256": manifest.get("candidate_source_sha256"),
        "image_digest": manifest.get("candidate_image_id"),
        "dirty_checkout": manifest.get("dirty_checkout"),
        "release_eligible": manifest.get("release_eligible", False),
        "docker_environment": manifest.get("docker_environment"),
        "generator_commit": report.get("generator_commit"),
        "workload": report["workload"],
        "rate_rps": rate,
        "duration_seconds": report["arrival_window_seconds"],
        "target_count": report["target_count"],
        "success_count": report["success_count"],
        "status_counts": report["status_counts"],
        "error_counts": report["error_counts"],
        "generator_dropped_count": report["generator_dropped_count"],
        "mean_ms": None if latency["mean"] is None else latency["mean"] * 1000,
        "p95_ms": None if latency["p95"] is None else latency["p95"] * 1000,
        "p99_ms": None if latency["p99"] is None else latency["p99"] * 1000,
        "queue_slope_per_second": queue["in_flight_slope_per_second"],
        "queue_passed": queue["queue_passed"],
        "latency_passed": report["latency_passed"],
        "throughput_passed": report["throughput_passed"],
        "economic_passed": report["economic_passed"],
        "accounting_reconciliation_passed": reconciliation["passed"],
        "accounting_drain_passed": drain["passed"],
        "accounting_drain_seconds": drain["seconds"],
        "passed": report["passed"],
    }


def summarize_preparation(path: Path, root: Path) -> dict[str, object]:
    report = json.loads(path.read_text())
    rate, run_id = report["target_rate_rps"], report["run_id"]
    if rate not in RATES or not isinstance(run_id, str) or not re.fullmatch("[0-9a-f]{32}", run_id):
        raise ValueError("Preparation evidence requires a supported rate and run identity")
    raw = path.parent / "warmup" / f"gateway-load-{run_id}.jsonl.gz"
    if not raw.is_file() or raw.is_symlink():
        raise ValueError("Preparation evidence requires its original raw samples")
    return {
        "campaign": path.relative_to(root).parts[0],
        "stage": path.parent.name,
        "purpose": "same-rate warm-up; not a measured qualification stage",
        "original_report": path.relative_to(root).as_posix(),
        "original_report_sha256": checksum(path),
        "raw_samples": raw.relative_to(root).as_posix(),
        "raw_samples_sha256": checksum(raw),
        "started_at": report["started_at"],
        "rate_rps": rate,
        "duration_seconds": report["duration_seconds"],
        "target_count": report["target_count"],
        "success_count": report["success_count"],
        "status_counts": report["status_counts"],
        "error_counts": report["error_counts"],
        "generator_dropped_count": report["generator_dropped_count"],
        "accounting_reconciliation_passed": report["accounting_reconciliation"]["passed"],
        "accounting_drain_passed": report["accounting_drain"]["passed"],
        "passed": report["passed"],
    }


def markdown(rows: list[dict[str, object]]) -> str:
    lines = [
        "# Saved qualification stages from 50 to 500 RPS",
        "",
        "These are individual stages, not one release certificate. Keep each result",
        "with its campaign, source, image, duration, and diagnostic purpose.",
        "The JSON index includes original file checksums and the main gate decisions.",
        "",
        "| Campaign and stage | RPS | Seconds | Success / target | p95 ms | p99 ms | Active request slope / s | Result |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        p95 = "unknown" if row["p95_ms"] is None else f"{row['p95_ms']:.2f}"
        p99 = "unknown" if row["p99_ms"] is None else f"{row['p99_ms']:.2f}"
        lines.append(
            f"| `{row['campaign']}/{row['stage']}` | {row['rate_rps']} | "
            f"{row['duration_seconds']:g} | {row['success_count']} / {row['target_count']} | "
            f"{p95} | {p99} | {row['queue_slope_per_second']:+.6f} | "
            f"{'PASS' if row['passed'] else 'FAIL'} |"
        )
    return "\n".join(lines) + "\n"


def retained_files(directory: str, names: list[str]) -> set[str]:
    ignored = {
        name
        for name in names
        if name.startswith("command-") or name == "__pycache__" or name.endswith(".pyc")
    }
    # The host already retained the same raw generator stream in the stage root.
    if Path(directory).name == "generator":
        ignored.update(name for name in names if name.endswith(".jsonl.gz"))
    return ignored


def verify_files(root: Path, inventory: list[dict[str, object]]) -> None:
    for record in inventory:
        relative = Path(record["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Evidence paths must remain inside the bundle")
        path = root / relative
        if path.is_symlink() or not path.is_file() or checksum(path) != record["sha256"]:
            raise ValueError(f"Evidence checksum does not match: {relative}")


def export_evidence(source: Path, output: Path, retain: tuple[str, ...]) -> dict[str, object]:
    source, output = source.resolve(), output.resolve()
    if not source.is_dir() or output.exists() or output.is_relative_to(source):
        raise ValueError("Use an existing source and a fresh output outside the source")
    paths = sorted(source.rglob("qualification.json"))
    if not 1 <= len(paths) <= 512:
        raise ValueError("Expected between one and 512 saved qualification stages")
    rows = []
    for path in paths:
        report = json.loads(path.read_text())
        if report.get("measurement_started") is False:
            warmup = path.parent / "warmup.json"
            if (
                report["passed"] is not False
                or not warmup.is_file()
                or json.loads(warmup.read_text()) != report["warmup"]
            ):
                raise ValueError("An unmeasured stage must preserve its failed preparation")
        else:
            rows.append(summarize_stage(path, source))
    rows.sort(key=lambda row: (row["started_at"], row["campaign"], row["stage"]))
    preparations = [
        summarize_preparation(path, source) for path in sorted(source.rglob("warmup.json"))
    ]
    preparations.sort(key=lambda row: (row["started_at"], row["campaign"], row["stage"]))
    for name in retain:
        if Path(name).name != name or not (source / name).is_dir():
            raise ValueError("Retained campaign names must identify existing source directories")
    output.mkdir(parents=True)
    for name in retain:
        shutil.copytree(source / name, output / "evidence" / name, ignore=retained_files)
    counts = Counter(row["rate_rps"] for row in rows)
    index = {
        "schema_version": 1,
        "scope": "All saved clean-integration stages; original results unchanged",
        "stage_count": len(rows),
        "counts_by_rate": {str(rate): counts[rate] for rate in RATES},
        "retained_campaigns": list(retain),
        "stages": rows,
        "preparation_count": len(preparations),
        "preparations": preparations,
    }
    (output / "all-runs.json").write_text(json.dumps(index, indent=2) + "\n")
    preparation_text = ""
    if preparations:
        lines = [
            "",
            "## Same-rate warm-ups",
            "",
            "These are preparation windows, not measured qualification stages.",
            "A failed warm-up stops later measurement. Original reports and raw checksums",
            "remain in the JSON index, including attempts with no measured stage.",
            "",
            "| Campaign and stage | RPS | Success / target | Warm-up result |",
            "| --- | ---: | ---: | --- |",
        ]
        for row in preparations:
            lines.append(
                f"| `{row['campaign']}/{row['stage']}` | {row['rate_rps']} | "
                f"{row['success_count']} / {row['target_count']} | "
                f"{'PASS' if row['passed'] else 'FAIL'} |"
            )
        preparation_text = "\n".join(lines) + "\n"
    (output / "all-runs.md").write_text(markdown(rows) + preparation_text)
    inventory = [
        {
            "path": path.relative_to(output).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": checksum(path),
        }
        for path in sorted(output.rglob("*"))
        if path.is_file()
    ]
    verify_files(output, inventory)
    (output / "checksums.json").write_text(json.dumps(inventory, indent=2) + "\n")
    return index


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--retain", nargs="*", default=[])
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        verify_files(args.output, json.loads((args.output / "checksums.json").read_text()))
        print("Every indexed evidence file matches its checksum.")
    elif args.source is None:
        parser.error("Export requires --source")
    else:
        index = export_evidence(args.source, args.output, tuple(args.retain))
        print(json.dumps({"stages": index["stage_count"], "counts": index["counts_by_rate"]}))


if __name__ == "__main__":
    main()
