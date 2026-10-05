"""Measure fixed journal worker actions without financial or tenant labels."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from time import perf_counter

from prometheus_client import Counter, Histogram

from src.metrics.accounting import ACCOUNTING_LATENCY_BUCKETS
from src.metrics.prometheus import get_prometheus_registry


class JournalAction(StrEnum):
    CLAIM = "claim"
    MATERIALIZE = "materialize"
    FAILURE = "failure"


class JournalActionOutcome(StrEnum):
    SUCCESS = "success"
    EMPTY = "empty"
    STALE = "stale"
    ERROR = "error"
    CANCELLED = "cancelled"


_actions = Counter(
    "deltallm_accounting_journal_worker_actions_total",
    "Bounded journal worker actions by fixed action and outcome",
    ["action", "outcome"],
    registry=get_prometheus_registry(),
)
_seconds = Histogram(
    "deltallm_accounting_journal_worker_action_seconds",
    "Journal worker action time including bounded database recovery",
    ["action", "outcome"],
    buckets=ACCOUNTING_LATENCY_BUCKETS,
    registry=get_prometheus_registry(),
)


@dataclass(slots=True)
class JournalObservation:
    outcome: JournalActionOutcome = JournalActionOutcome.SUCCESS


@contextmanager
def journal_action(action: JournalAction) -> Iterator[JournalObservation]:
    observed = JournalObservation()
    started = perf_counter()
    try:
        yield observed
    except asyncio.CancelledError:
        observed.outcome = JournalActionOutcome.CANCELLED
        raise
    except Exception:
        observed.outcome = JournalActionOutcome.ERROR
        raise
    finally:
        labels = {"action": action.value, "outcome": observed.outcome.value}
        _actions.labels(**labels).inc()
        _seconds.labels(**labels).observe(max(0, perf_counter() - started))
