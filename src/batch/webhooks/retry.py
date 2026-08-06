from __future__ import annotations

import random
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


def batch_webhook_status_is_success(status_code: int) -> bool:
    return 200 <= int(status_code) <= 299


def batch_webhook_status_is_retryable(status_code: int) -> bool:
    normalized = int(status_code)
    return normalized in {408, 425, 429} or 500 <= normalized <= 599


def parse_batch_webhook_retry_after(
    value: str | None,
    *,
    now: datetime,
    maximum_seconds: float,
) -> float | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    maximum = max(0.0, float(maximum_seconds))
    try:
        seconds = float(int(normalized, 10))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(normalized)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        normalized_now = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
        seconds = (retry_at.astimezone(UTC) - normalized_now.astimezone(UTC)).total_seconds()
    if seconds < 0:
        return None
    return min(maximum, seconds)


def calculate_batch_webhook_retry_delay(
    *,
    attempt_count: int,
    initial_seconds: float,
    maximum_seconds: float,
    retry_after: str | None,
    now: datetime,
    random_source: random.Random | None = None,
) -> float:
    maximum = max(0.0, float(maximum_seconds))
    exponential = min(
        maximum,
        max(0.0, float(initial_seconds)) * (2 ** max(0, int(attempt_count) - 1)),
    )
    source = random_source or random.SystemRandom()
    jittered = source.uniform(exponential / 2, exponential) if exponential else 0.0
    retry_after_seconds = parse_batch_webhook_retry_after(
        retry_after,
        now=now,
        maximum_seconds=maximum,
    )
    if retry_after_seconds is not None:
        jittered = max(jittered, retry_after_seconds)
    return min(maximum, jittered)
