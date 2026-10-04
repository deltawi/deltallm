"""Read-only Linux process evidence for the operator capacity preflight."""

from __future__ import annotations

import argparse
import hashlib
from itertools import islice
import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from src.deployment_capacity_report import CapacityReport, Role

MAX_PROCESSES = 32
MAX_DESCRIPTORS = 1_000_000


class ProcessObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    pid: int = Field(gt=0)
    descriptors: int = Field(ge=0, le=MAX_DESCRIPTORS)
    soft_limit: int | None = Field(default=None, gt=0)
    resident_kib: int = Field(ge=0)


class CapacityObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_sha256: str
    role: Role
    processes: tuple[ProcessObservation, ...]
    node_file_max: int = Field(gt=0)
    memory_bytes: int = Field(ge=0)
    cpu_usage_usec: int = Field(ge=0)
    cpu_throttled_periods: int = Field(ge=0)


def bounded_text(path: Path, limit: int = 16384) -> str:
    with path.open() as source:
        text = source.read(limit + 1)
    if len(text) > limit:
        raise RuntimeError("Process evidence exceeds its allocation")
    return text


def observe_processes(pid: int, proc_root: Path) -> tuple[ProcessObservation, ...]:
    pending = [pid]
    seen: set[int] = set()
    results: list[ProcessObservation] = []
    while pending:
        current = pending.pop()
        if current in seen or len(seen) >= MAX_PROCESSES:
            raise RuntimeError("Managed process tree exceeds its allocation")
        seen.add(current)
        root = proc_root / str(current)
        with os.scandir(root / "fd") as entries:
            descriptors = sum(1 for _ in islice(entries, MAX_DESCRIPTORS + 1))
        if descriptors > MAX_DESCRIPTORS:
            raise RuntimeError("Process descriptor count exceeds the observation bound")
        soft_limit = None
        found_limit = False
        for line in bounded_text(root / "limits").splitlines():
            if line.startswith("Max open files"):
                raw = line.split()[3]
                soft_limit = None if raw == "unlimited" else int(raw)
                found_limit = True
        if not found_limit:
            raise RuntimeError("Process descriptor limit is unavailable")
        status = dict(
            line.split(":", 1) for line in bounded_text(root / "status").splitlines() if ":" in line
        )
        if "VmRSS" not in status:
            raise RuntimeError("Process memory evidence is unavailable")
        resident = int(status["VmRSS"].split()[0])
        results.append(
            ProcessObservation(
                pid=current, descriptors=descriptors, soft_limit=soft_limit, resident_kib=resident
            )
        )
        children = bounded_text(root / "task" / str(current) / "children", 4096).split()
        pending.extend(int(child) for child in children)
    return tuple(results)


def observe_capacity(
    report_path: Path,
    *,
    role: Role,
    pid: int = 1,
    proc_root: Path = Path("/proc"),
    cgroup_root: Path = Path("/sys/fs/cgroup"),
) -> CapacityObservation:
    report = CapacityReport.read(report_path)
    allocation = report.roles[role]
    processes = observe_processes(pid, proc_root)
    if len(processes) != allocation.file_descriptors.engine_processes + 1:
        raise RuntimeError("Live process inventory differs from the declared Prisma allocations")
    for process in processes:
        required = (
            allocation.file_descriptors.python
            if process.pid == pid
            else allocation.file_descriptors.engine
        )
        if process.soft_limit is not None and process.soft_limit < required:
            raise RuntimeError("Live file descriptor limit is below the declared allocation")
        if process.descriptors > required:
            raise RuntimeError("Live file descriptors exceed the declared allocation")
    node_maximum = int(bounded_text(proc_root / "sys/fs/file-max", 128))
    if node_maximum < report.file_descriptors.node_peak:
        raise RuntimeError("Kernel file descriptor budget is below the declared peak")
    cpu = dict(line.split() for line in bounded_text(cgroup_root / "cpu.stat").splitlines())
    return CapacityObservation(
        contract_sha256=hashlib.sha256(report.model_dump_json(by_alias=True).encode()).hexdigest(),
        role=role,
        processes=processes,
        node_file_max=node_maximum,
        memory_bytes=int(bounded_text(cgroup_root / "memory.current", 128)),
        cpu_usage_usec=int(cpu["usage_usec"]),
        cpu_throttled_periods=int(cpu["nr_throttled"]),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("/app/capacity/report.json"))
    parser.add_argument("--role", choices=("api", "batchWorker"), default="api")
    args = parser.parse_args()
    try:
        result = observe_capacity(args.report, role=args.role)
    except (OSError, KeyError, ValueError, RuntimeError):
        # Never print arbitrary mounted content or environment on a failed preflight.
        print(json.dumps({"status": "unavailable", "reason": "capacity_preflight_failed"}))
        raise SystemExit(1) from None
    print(result.model_dump_json())


if __name__ == "__main__":
    main()
