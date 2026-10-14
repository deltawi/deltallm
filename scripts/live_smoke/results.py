"""Check results, the printed summary, and the optional JSON report."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import StrEnum
import json
from pathlib import Path
from threading import Lock
from typing import TextIO

REDACTED = "[redacted]"


class Status(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    SKIP = "skip"


@dataclass(frozen=True)
class CheckResult:
    stage: str
    name: str
    status: Status
    detail: str = ""
    mode: str | None = None
    latency_ms: int | None = None


class Recorder:
    """Thread-safe result list. Each result is printed when it is recorded.

    Secrets given to ``protect`` are replaced in each result before it is kept,
    so neither the printed lines nor the JSON report can contain them.
    """

    def __init__(self, out: TextIO) -> None:
        self._out = out
        self._lock = Lock()
        self._secrets: list[str] = []
        self.results: list[CheckResult] = []

    def protect(self, secret: str) -> None:
        if secret:
            with self._lock:
                self._secrets.append(secret)

    def _redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        return text

    def add(self, result: CheckResult) -> CheckResult:
        with self._lock:
            result = replace(
                result, name=self._redact(result.name), detail=self._redact(result.detail)
            )
            self.results.append(result)
            label = result.name if result.mode is None else f"{result.name} [{result.mode}]"
            suffix = f" ({result.latency_ms} ms)" if result.latency_ms is not None else ""
            detail = f": {result.detail}" if result.detail else ""
            print(
                f"  {result.status.value.upper():4} {result.stage}: {label}{suffix}{detail}",
                file=self._out,
                flush=True,
            )
        return result

    def failed(self) -> list[CheckResult]:
        return [item for item in self.results if item.status is Status.FAIL]

    def write_summary(self) -> None:
        counts = {status: 0 for status in Status}
        for item in self.results:
            counts[item.status] += 1
        models = [item for item in self.results if item.stage == "model"]
        print("\nModel results", file=self._out)
        if not models:
            print("  (none)", file=self._out)
        width = max((len(item.name) for item in models), default=0)
        mode_width = max((len(item.mode or "-") for item in models), default=0)
        for item in sorted(models, key=lambda value: (value.name, value.detail)):
            mode = item.mode or "-"
            detail = f"  {item.detail}" if item.detail else ""
            print(
                f"  {item.status.value.upper():4}  {item.name:<{width}}  {mode:<{mode_width}}{detail}",
                file=self._out,
            )
        print(
            "\nTotal: " + ", ".join(f"{counts[status]} {status.value}" for status in Status),
            file=self._out,
        )
        failures = self.failed()
        if failures:
            print("Result: FAILED", file=self._out)
            for item in failures:
                print(f"  - {item.stage}: {item.name}: {item.detail}", file=self._out)
        else:
            print("Result: PASSED", file=self._out)
        self._out.flush()

    def write_json(self, path: Path, *, base_url: str) -> None:
        payload = {
            "base_url": base_url,
            "passed": not self.failed(),
            "results": [{**asdict(item), "status": item.status.value} for item in self.results],
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
