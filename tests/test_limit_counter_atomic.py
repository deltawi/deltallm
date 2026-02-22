from __future__ import annotations

import pytest

from src.models.errors import RateLimitError
from src.services.limit_counter import RateLimitCheck


@pytest.mark.asyncio
async def test_atomic_rate_limit_does_not_partially_increment(test_app):
    limiter = test_app.state.limit_counter
    redis = test_app.state.redis
    window_id = limiter._window_id(60)  # noqa: SLF001 - test-only inspection

    failing_key = f"ratelimit:key_rpm:key-1:{window_id}"
    unaffected_key = f"ratelimit:org_rpm:org-1:{window_id}"
    redis.store[failing_key] = 1

    checks = [
        RateLimitCheck(scope="org_rpm", entity_id="org-1", limit=5, amount=1),
        RateLimitCheck(scope="key_rpm", entity_id="key-1", limit=1, amount=1),
    ]

    with pytest.raises(RateLimitError):
        await limiter.check_rate_limits_atomic(checks)

    assert redis.store.get(unaffected_key) is None
    assert redis.store.get(failing_key) == 1
