from __future__ import annotations

from datetime import UTC, datetime, timedelta
import random

import pytest

from src.batch.webhooks.retry import (
    batch_webhook_status_is_retryable,
    batch_webhook_status_is_success,
    calculate_batch_webhook_retry_delay,
    parse_batch_webhook_retry_after,
)


@pytest.mark.parametrize("status", [200, 201, 204, 299])
def test_webhook_success_statuses(status: int) -> None:
    assert batch_webhook_status_is_success(status) is True


@pytest.mark.parametrize("status", [408, 425, 429, 500, 503, 599])
def test_webhook_retryable_statuses(status: int) -> None:
    assert batch_webhook_status_is_retryable(status) is True


@pytest.mark.parametrize("status", [199, 300, 302, 400, 404, 422, 600])
def test_webhook_permanent_statuses(status: int) -> None:
    assert batch_webhook_status_is_success(status) is False
    assert batch_webhook_status_is_retryable(status) is False


def test_retry_after_accepts_delta_and_http_date_and_caps() -> None:
    now = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    future = now + timedelta(seconds=90)

    assert parse_batch_webhook_retry_after("12", now=now, maximum_seconds=60) == 12
    assert (
        parse_batch_webhook_retry_after(
            future.strftime("%a, %d %b %Y %H:%M:%S GMT"),
            now=now,
            maximum_seconds=60,
        )
        == 60
    )
    assert parse_batch_webhook_retry_after("invalid", now=now, maximum_seconds=60) is None


def test_retry_delay_is_exponential_jittered_and_respects_retry_after() -> None:
    now = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    delay = calculate_batch_webhook_retry_delay(
        attempt_count=3,
        initial_seconds=5,
        maximum_seconds=60,
        retry_after="30",
        now=now,
        random_source=random.Random(1),
    )
    assert delay == 30

    capped = calculate_batch_webhook_retry_delay(
        attempt_count=20,
        initial_seconds=5,
        maximum_seconds=60,
        retry_after="120",
        now=now,
        random_source=random.Random(1),
    )
    assert capped == 60
