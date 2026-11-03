"""Pure pass/fail rules for the accounting v2 soak and acceptance runs.

They take scraped metric samples and database snapshots and return plain
dictionaries, so the rules are unit-tested without a cluster.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal
import re

from tests.performance.native_qualification_economics import BUDGET_SCOPE_TYPES

Samples = Mapping[str, list[tuple[dict[str, str], float]]]
Scrape = Mapping[str, Mapping[str, object]]

# In-memory state that must return to its idle level after load ends: queues,
# retained bytes and in-flight slots. A leak shows as a value that stays above
# the level measured before load (the 2026-11-03 receipt store never drained).
IDLE_GAUGES: tuple[str, ...] = (
    "deltallm_accounting_queue_depth",
    "deltallm_accounting_queue_retained_bytes",
    "deltallm_batch_model_in_flight_items",
)
# Store fill gauges (receipts, cursors, permits) are picked up by name, so a
# new gauge is checked without editing this file: today the local receipt
# count, bytes and fill ratio. Counters such as the receipt release total only
# grow, so they are left out.
IDLE_GAUGE_PATTERN = re.compile(
    r"^deltallm_accounting_\w*(receipt|cursor)\w*(?<!_total)(?<!_created)$"
)
# The accounting refusal that means a bounded store or queue was full. A full
# local receipt store on a batch pod is counted here (2026-11-03 soak: about
# 10,000 per run). Any increase during a soak is a failure: the load is sized
# below every configured bound.
CAPACITY_FAILURE_REASONS = frozenset({"queue_full"})


def _idle_gauge(name: str) -> bool:
    return name in IDLE_GAUGES or IDLE_GAUGE_PATTERN.match(name) is not None


def _key(labels: Mapping[str, str]) -> str:
    return ",".join(f"{name}={labels[name]}" for name in sorted(labels))


def idle_gauges(scrape: Scrape) -> dict[tuple[str, str, str], float]:
    """Return {(pod, sample name, labels): value} for every tracked idle gauge."""
    result = {}
    for pod, entry in scrape.items():
        samples: Samples = entry["samples"]  # type: ignore[assignment]
        for name, values in samples.items():
            if _idle_gauge(name):
                for labels, value in values:
                    result[(pod, name, _key(labels))] = float(value)
    return result


def gauges_returned_to_idle(baseline: Scrape, current: Scrape) -> dict[str, object]:
    """Every tracked gauge is at or below its pre-load value on every surviving pod.

    A pod that did not exist at baseline (a replacement after pod loss) is held
    to zero, the value a fresh process starts from.
    """
    before = idle_gauges(baseline)
    after = idle_gauges(current)
    above = [
        {
            "pod": pod,
            "metric": name,
            "labels": labels,
            "baseline": before.get((pod, name, labels), 0.0),
            "value": value,
        }
        for (pod, name, labels), value in sorted(after.items())
        if value > before.get((pod, name, labels), 0.0)
    ]
    return {"passed": not above, "tracked": len(after), "above_baseline": above[:64]}


def counter_total(scrape: Scrape, name: str, **labels: str) -> dict[str, float]:
    """Sum one counter per pod over samples whose labels include ``labels``."""
    totals: dict[str, float] = {}
    for pod, entry in scrape.items():
        samples: Samples = entry["samples"]  # type: ignore[assignment]
        totals[pod] = sum(
            value
            for sample_labels, value in samples.get(name, [])
            if all(sample_labels.get(key) == wanted for key, wanted in labels.items())
        )
    return totals


def counter_by_label(scrape: Scrape, name: str, label: str) -> dict[str, float]:
    """Sum one counter across pods, grouped by one label."""
    totals: dict[str, float] = {}
    for entry in scrape.values():
        samples: Samples = entry["samples"]  # type: ignore[assignment]
        for labels, value in samples.get(name, []):
            key = labels.get(label, "")
            totals[key] = totals.get(key, 0.0) + value
    return totals


def counter_delta(before: Mapping[str, float], after: Mapping[str, float]) -> dict[str, float]:
    """Per-key increase; a key missing before (a new pod or label) counts from zero."""
    return {
        key: value - before.get(key, 0.0)
        for key, value in sorted(after.items())
        if value - before.get(key, 0.0) > 0
    }


def capacity_failures(before: Scrape, after: Scrape) -> dict[str, object]:
    """Accounting refusals for full stores or queues, by pod and reason, during a window."""
    increases: dict[str, float] = {}
    for reason in sorted(CAPACITY_FAILURE_REASONS):
        delta = counter_delta(
            counter_total(before, "deltallm_accounting_failures_total", reason=reason),
            counter_total(after, "deltallm_accounting_failures_total", reason=reason),
        )
        increases.update({f"{pod}:{reason}": value for pod, value in delta.items()})
    all_reasons = counter_delta(
        counter_by_label(before, "deltallm_accounting_failures_total", "reason"),
        counter_by_label(after, "deltallm_accounting_failures_total", "reason"),
    )
    return {
        "passed": not increases,
        "capacity_refusals": increases,
        "all_failures_by_reason": all_reasons,
    }


def batch_outcome(
    jobs: Iterable[Mapping[str, object]],
    *,
    expected_items: int,
    elapsed_seconds: float,
    min_items_per_second: float,
) -> dict[str, object]:
    """All jobs completed, no item failed, and throughput met the target."""
    jobs = list(jobs)
    completed = sum(int(job["request_counts"]["completed"]) for job in jobs)  # type: ignore[index]
    failed = sum(int(job["request_counts"]["failed"]) for job in jobs)  # type: ignore[index]
    statuses = sorted({str(job["status"]) for job in jobs})
    throughput = completed / elapsed_seconds if elapsed_seconds > 0 else 0.0
    failures = []
    if statuses != ["completed"]:
        failures.append("job_not_completed")
    if failed:
        failures.append("items_failed")
    if completed != expected_items:
        failures.append("item_count_mismatch")
    if throughput < min_items_per_second:
        failures.append("throughput_below_target")
    return {
        "passed": not failures,
        "failures": failures,
        "jobs": len(jobs),
        "statuses": statuses,
        "completed_items": completed,
        "failed_items": failed,
        "expected_items": expected_items,
        "elapsed_seconds": elapsed_seconds,
        "items_per_second": throughput,
        "min_items_per_second": min_items_per_second,
    }


def api_window(
    report: Mapping[str, object], *, max_p95_seconds: float, min_success_ratio: float = 0.999
) -> dict[str, object]:
    """One API soak window: no 5xx or transport errors, success ratio and p95 met."""
    statuses: Mapping[str, int] = report.get("status_counts", {})  # type: ignore[assignment]
    errors: Mapping[str, int] = report.get("error_counts", {})  # type: ignore[assignment]
    server_errors = sum(
        int(count) for status, count in statuses.items() if str(status).startswith("5")
    )
    transport_errors = sum(int(count) for count in errors.values())
    target = int(report["target_count"])  # type: ignore[arg-type]
    success = int(report["success_count"])  # type: ignore[arg-type]
    ratio = success / target if target else 0.0
    p95 = report["latency_seconds"].get("p95")  # type: ignore[union-attr]
    failures = []
    if server_errors:
        failures.append("server_errors")
    if transport_errors:
        failures.append("transport_errors")
    if ratio < min_success_ratio:
        failures.append("success_ratio_below_target")
    if p95 is None or p95 > max_p95_seconds:
        failures.append("p95_above_target")
    return {
        "passed": not failures,
        "failures": failures,
        "server_errors": server_errors,
        "transport_errors": transport_errors,
        "success_ratio": ratio,
        "p95_seconds": p95,
        "max_p95_seconds": max_p95_seconds,
    }


def ledger_matches(
    before: Mapping[str, object],
    after: Mapping[str, object],
    *,
    charged: int,
    duplicates: int,
    scopes: Iterable[Mapping[str, str]],
    allowed_extra: int = 0,
) -> dict[str, object]:
    """Native usage facts match successful work once, and budgets hold no open money.

    ``charged`` is the work the clients saw succeed. ``allowed_extra`` covers
    requests in flight when a pod was killed: the gateway may have charged a
    provider answer the client never received, never more than those.
    """
    facts = int(after["facts"]) - int(before["facts"])  # type: ignore[arg-type]
    total = Decimal(str(after["fact_charge"]))
    scopes = list(scopes)
    scope_ok = {scope["scope_type"] for scope in scopes} == BUDGET_SCOPE_TYPES and all(
        Decimal(scope["committed"]) == total
        and Decimal(scope["reserved"]) == 0
        and Decimal(scope["provisional"]) == 0
        for scope in scopes
    )
    failures = []
    if not charged <= facts <= charged + allowed_extra:
        failures.append("fact_count_mismatch")
    if duplicates:
        failures.append("duplicate_charges")
    if not scope_ok:
        failures.append("budget_scopes_do_not_match_facts")
    if int(after["unsafe_windows"]):  # type: ignore[arg-type]
        failures.append("unsafe_budget_windows")
    if int(after["legacy_spend_rows"]):  # type: ignore[arg-type]
        failures.append("legacy_spend_rows_written")
    return {
        "passed": not failures,
        "failures": failures,
        "new_facts": facts,
        "charged_requests": charged,
        "allowed_extra": allowed_extra,
        "duplicate_operations": duplicates,
        "scopes": scopes,
    }
