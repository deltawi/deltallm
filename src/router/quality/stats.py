from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, fields
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Literal

BUCKET_SECONDS: Final = 300
WINDOW_BUCKETS: Final = 3
WINDOW_SECONDS: Final = BUCKET_SECONDS * WINDOW_BUCKETS
# Deployment IDs come from the configured registry. The cap only stops a bug or a
# misconfiguration from growing process memory without a limit.
MAX_TRACKED_DEPLOYMENTS: Final = 4096

QualitySource = Literal["cluster", "local"]


class QualityOutcome(StrEnum):
    OK = "ok"
    PROVIDER_ERROR = "provider_error"
    EMPTY_ANSWER = "empty_answer"
    BAD_TOOL_CALL = "bad_tool_call"
    BAD_JSON = "bad_json"


@dataclass(slots=True)
class QualityCounts:
    """Integer counters, so a Redis HINCRBY can add them without float drift."""

    calls: int = 0
    provider_errors: int = 0
    empty_answers: int = 0
    bad_tool_calls: int = 0
    bad_json: int = 0
    first_token_samples: int = 0
    first_token_ms: int = 0
    output_tokens: int = 0
    output_ms: int = 0

    def add(self, other: QualityCounts) -> None:
        for name in COUNT_FIELDS:
            setattr(self, name, getattr(self, name) + getattr(other, name))

    def nonzero_fields(self) -> dict[str, int]:
        return {name: value for name in COUNT_FIELDS if (value := getattr(self, name))}


COUNT_FIELDS: Final = tuple(field.name for field in fields(QualityCounts))

_OUTCOME_FIELDS: Final = {
    QualityOutcome.PROVIDER_ERROR: "provider_errors",
    QualityOutcome.EMPTY_ANSWER: "empty_answers",
    QualityOutcome.BAD_TOOL_CALL: "bad_tool_calls",
    QualityOutcome.BAD_JSON: "bad_json",
}


@dataclass(frozen=True, slots=True)
class DeploymentQuality:
    calls: int = 0
    provider_errors: int = 0
    empty_answers: int = 0
    bad_tool_calls: int = 0
    bad_json: int = 0
    first_token_samples: int = 0
    first_token_ms: int = 0
    output_tokens: int = 0
    output_ms: int = 0

    @classmethod
    def from_counts(cls, counts: QualityCounts) -> DeploymentQuality:
        return cls(**{name: getattr(counts, name) for name in COUNT_FIELDS})

    @property
    def failed_calls(self) -> int:
        return self.provider_errors + self.empty_answers + self.bad_tool_calls + self.bad_json

    @property
    def failure_rate(self) -> float | None:
        return min(1.0, self.failed_calls / self.calls) if self.calls else None

    @property
    def avg_first_token_ms(self) -> float | None:
        if not self.first_token_samples:
            return None
        return self.first_token_ms / self.first_token_samples

    @property
    def output_tokens_per_second(self) -> float | None:
        if self.output_ms <= 0 or self.output_tokens <= 0:
            return None
        return self.output_tokens * 1000 / self.output_ms


@dataclass(frozen=True, slots=True)
class QualitySnapshot:
    """Immutable per-deployment totals for the last window, read without awaiting."""

    deployments: Mapping[str, DeploymentQuality]
    source: QualitySource
    window_seconds: int = WINDOW_SECONDS

    def get(self, deployment_id: str) -> DeploymentQuality | None:
        return self.deployments.get(deployment_id)


EMPTY_LOCAL_SNAPSHOT: Final = QualitySnapshot(MappingProxyType({}), "local")

_Buckets = dict[int, dict[str, QualityCounts]]


class DeploymentQualityTracker:
    """Process-local quality counters with a published window snapshot.

    Recording is a dict update on the request path. Routing and the admin API read
    the snapshot that the sync loop publishes, so neither one awaits Redis.
    """

    __slots__ = ("_clock", "_local", "_pending", "_snapshot")

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._local: _Buckets = {}
        self._pending: _Buckets = {}
        self._snapshot = EMPTY_LOCAL_SNAPSHOT

    def snapshot(self) -> QualitySnapshot:
        return self._snapshot

    def record(
        self,
        deployment_id: str,
        outcome: QualityOutcome | None,
        *,
        first_token_ms: float | None = None,
        output_tokens: int | None = None,
        output_ms: float | None = None,
    ) -> None:
        """Add one finished call (outcome set) and any timing samples it produced."""

        bucket = self.current_bucket()
        local = self._counts(self._local, bucket, deployment_id)
        if local is None:
            return
        pending = self._counts(self._pending, bucket, deployment_id)
        for counts in (local, pending):
            if counts is None:
                continue
            if outcome is not None:
                counts.calls += 1
                failure_field = _OUTCOME_FIELDS.get(outcome)
                if failure_field is not None:
                    setattr(counts, failure_field, getattr(counts, failure_field) + 1)
            if first_token_ms is not None and first_token_ms >= 0:
                counts.first_token_samples += 1
                counts.first_token_ms += round(first_token_ms)
            if output_tokens and output_ms and output_tokens > 0 and output_ms > 0:
                counts.output_tokens += output_tokens
                counts.output_ms += round(output_ms)

    def take_pending(self) -> _Buckets:
        pending, self._pending = self._pending, {}
        return pending

    def publish(self, snapshot: QualitySnapshot) -> None:
        self._snapshot = snapshot

    def local_snapshot(self) -> QualitySnapshot:
        oldest = self.current_bucket() - WINDOW_BUCKETS + 1
        for bucket in [bucket for bucket in self._local if bucket < oldest]:
            del self._local[bucket]
        return snapshot_from_buckets(self._local.values(), source="local")

    def current_bucket(self) -> int:
        return int(self._clock() // BUCKET_SECONDS)

    @staticmethod
    def _counts(buckets: _Buckets, bucket: int, deployment_id: str) -> QualityCounts | None:
        by_deployment = buckets.setdefault(bucket, {})
        counts = by_deployment.get(deployment_id)
        if counts is None:
            if len(by_deployment) >= MAX_TRACKED_DEPLOYMENTS:
                return None
            counts = by_deployment[deployment_id] = QualityCounts()
        return counts


def snapshot_from_buckets(
    buckets: Iterable[Mapping[str, QualityCounts]], *, source: QualitySource
) -> QualitySnapshot:
    totals: dict[str, QualityCounts] = {}
    for by_deployment in buckets:
        for deployment_id, counts in by_deployment.items():
            totals.setdefault(deployment_id, QualityCounts()).add(counts)
    return QualitySnapshot(
        MappingProxyType(
            {key: DeploymentQuality.from_counts(value) for key, value in totals.items()}
        ),
        source,
    )
