from __future__ import annotations

import math
import time

import pytest

from src.models.errors import RateLimitError
from src.services.limit_counter import LimitCounter, RateLimitCheck, RateLimitResult
from tests.conftest import FakeRedis


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def counter(fake_redis):
    return LimitCounter(redis_client=fake_redis, degraded_mode="fail_open")


@pytest.fixture
def counter_no_redis():
    return LimitCounter(redis_client=None, degraded_mode="fail_open")


class TestRateLimitCheckWindowSeconds:
    def test_default_window_is_60(self):
        check = RateLimitCheck(scope="key_rpm", entity_id="k1", limit=10)
        assert check.window_seconds == 60

    def test_custom_window_seconds(self):
        check = RateLimitCheck(scope="key_rph", entity_id="k1", limit=100, window_seconds=3600)
        assert check.window_seconds == 3600

    def test_day_window(self):
        check = RateLimitCheck(scope="key_rpd", entity_id="k1", limit=1000, window_seconds=86400)
        assert check.window_seconds == 86400


class TestMultiWindowAtomicRedis:
    @pytest.mark.asyncio
    async def test_mixed_minute_hour_checks_pass(self, counter):
        checks = [
            RateLimitCheck(scope="key_rpm", entity_id="k1", limit=10, amount=1, window_seconds=60),
            RateLimitCheck(scope="key_rph", entity_id="k1", limit=100, amount=1, window_seconds=3600),
        ]
        result = await counter.check_rate_limits_atomic(checks)
        assert len(result.checks) == 2
        assert len(result.current_values) == 2
        assert result.current_values[0] == 1
        assert result.current_values[1] == 1

    @pytest.mark.asyncio
    async def test_minute_limit_exceeded_hour_ok(self, counter):
        checks_fill = [
            RateLimitCheck(scope="key_rpm", entity_id="k2", limit=2, amount=2, window_seconds=60),
            RateLimitCheck(scope="key_rph", entity_id="k2", limit=100, amount=2, window_seconds=3600),
        ]
        await counter.check_rate_limits_atomic(checks_fill)

        checks_exceed = [
            RateLimitCheck(scope="key_rpm", entity_id="k2", limit=2, amount=1, window_seconds=60),
            RateLimitCheck(scope="key_rph", entity_id="k2", limit=100, amount=1, window_seconds=3600),
        ]
        with pytest.raises(RateLimitError) as exc_info:
            await counter.check_rate_limits_atomic(checks_exceed)
        assert "key_rpm" in str(exc_info.value.param)

    @pytest.mark.asyncio
    async def test_hour_limit_exceeded_minute_ok(self, counter):
        checks_fill = [
            RateLimitCheck(scope="key_rpm", entity_id="k3", limit=1000, amount=1, window_seconds=60),
            RateLimitCheck(scope="key_rph", entity_id="k3", limit=2, amount=2, window_seconds=3600),
        ]
        await counter.check_rate_limits_atomic(checks_fill)

        checks_exceed = [
            RateLimitCheck(scope="key_rpm", entity_id="k3", limit=1000, amount=1, window_seconds=60),
            RateLimitCheck(scope="key_rph", entity_id="k3", limit=2, amount=1, window_seconds=3600),
        ]
        with pytest.raises(RateLimitError) as exc_info:
            await counter.check_rate_limits_atomic(checks_exceed)
        assert "key_rph" in str(exc_info.value.param)

    @pytest.mark.asyncio
    async def test_day_limit_exceeded(self, counter):
        checks_fill = [
            RateLimitCheck(scope="key_rpd", entity_id="k4", limit=3, amount=3, window_seconds=86400),
        ]
        await counter.check_rate_limits_atomic(checks_fill)

        checks_exceed = [
            RateLimitCheck(scope="key_rpd", entity_id="k4", limit=3, amount=1, window_seconds=86400),
        ]
        with pytest.raises(RateLimitError) as exc_info:
            await counter.check_rate_limits_atomic(checks_exceed)
        assert "key_rpd" in str(exc_info.value.param)

    @pytest.mark.asyncio
    async def test_tpd_limit_exceeded(self, counter):
        checks_fill = [
            RateLimitCheck(scope="team_tpd", entity_id="t1", limit=500, amount=500, window_seconds=86400),
        ]
        await counter.check_rate_limits_atomic(checks_fill)

        checks_exceed = [
            RateLimitCheck(scope="team_tpd", entity_id="t1", limit=500, amount=1, window_seconds=86400),
        ]
        with pytest.raises(RateLimitError) as exc_info:
            await counter.check_rate_limits_atomic(checks_exceed)
        assert "team_tpd" in str(exc_info.value.param)

    @pytest.mark.asyncio
    async def test_three_windows_all_pass(self, counter):
        checks = [
            RateLimitCheck(scope="key_rpm", entity_id="k5", limit=100, amount=1, window_seconds=60),
            RateLimitCheck(scope="key_rph", entity_id="k5", limit=1000, amount=1, window_seconds=3600),
            RateLimitCheck(scope="key_rpd", entity_id="k5", limit=10000, amount=1, window_seconds=86400),
        ]
        result = await counter.check_rate_limits_atomic(checks)
        assert len(result.current_values) == 3
        for v in result.current_values:
            assert v == 1

    @pytest.mark.asyncio
    async def test_different_redis_keys_per_window(self, counter, fake_redis):
        checks = [
            RateLimitCheck(scope="key_rpm", entity_id="k6", limit=100, amount=1, window_seconds=60),
            RateLimitCheck(scope="key_rph", entity_id="k6", limit=100, amount=1, window_seconds=3600),
        ]
        await counter.check_rate_limits_atomic(checks)

        minute_id = counter._window_id(60)
        hour_id = counter._window_id(3600)
        minute_key = f"ratelimit:key_rpm:k6:{minute_id}"
        hour_key = f"ratelimit:key_rph:k6:{hour_id}"

        assert fake_redis.store.get(minute_key) == 1
        assert fake_redis.store.get(hour_key) == 1


class TestMultiWindowFallback:
    @pytest.mark.asyncio
    async def test_mixed_windows_fallback_pass(self, counter_no_redis):
        checks = [
            RateLimitCheck(scope="key_rpm", entity_id="f1", limit=10, amount=1, window_seconds=60),
            RateLimitCheck(scope="key_rph", entity_id="f1", limit=100, amount=1, window_seconds=3600),
        ]
        result = await counter_no_redis.check_rate_limits_atomic(checks)
        assert len(result.current_values) == 2
        assert result.current_values[0] == 1
        assert result.current_values[1] == 1

    @pytest.mark.asyncio
    async def test_fallback_minute_exceeded_hour_ok(self, counter_no_redis):
        checks_fill = [
            RateLimitCheck(scope="key_rpm", entity_id="f2", limit=2, amount=2, window_seconds=60),
            RateLimitCheck(scope="key_rph", entity_id="f2", limit=100, amount=2, window_seconds=3600),
        ]
        await counter_no_redis.check_rate_limits_atomic(checks_fill)

        checks_exceed = [
            RateLimitCheck(scope="key_rpm", entity_id="f2", limit=2, amount=1, window_seconds=60),
            RateLimitCheck(scope="key_rph", entity_id="f2", limit=100, amount=1, window_seconds=3600),
        ]
        with pytest.raises(RateLimitError) as exc_info:
            await counter_no_redis.check_rate_limits_atomic(checks_exceed)
        assert "key_rpm" in str(exc_info.value.param)

    @pytest.mark.asyncio
    async def test_fallback_day_limit_exceeded(self, counter_no_redis):
        checks_fill = [
            RateLimitCheck(scope="key_rpd", entity_id="f3", limit=5, amount=5, window_seconds=86400),
        ]
        await counter_no_redis.check_rate_limits_atomic(checks_fill)

        checks_exceed = [
            RateLimitCheck(scope="key_rpd", entity_id="f3", limit=5, amount=1, window_seconds=86400),
        ]
        with pytest.raises(RateLimitError) as exc_info:
            await counter_no_redis.check_rate_limits_atomic(checks_exceed)
        assert "key_rpd" in str(exc_info.value.param)

    @pytest.mark.asyncio
    async def test_fallback_hour_exceeded(self, counter_no_redis):
        checks_fill = [
            RateLimitCheck(scope="org_rph", entity_id="f4", limit=3, amount=3, window_seconds=3600),
        ]
        await counter_no_redis.check_rate_limits_atomic(checks_fill)

        checks_exceed = [
            RateLimitCheck(scope="org_rph", entity_id="f4", limit=3, amount=1, window_seconds=3600),
        ]
        with pytest.raises(RateLimitError) as exc_info:
            await counter_no_redis.check_rate_limits_atomic(checks_exceed)
        assert "org_rph" in str(exc_info.value.param)


class TestMultiWindowHeaderClassification:
    def test_rph_scope_uses_hour_reset(self):
        from src.middleware.rate_limit import _compute_rate_limit_state
        now = time.time()
        hour_reset = int((math.floor(now / 3600) + 1) * 3600)
        checks = [
            RateLimitCheck(scope="key_rph", entity_id="h1", limit=100, amount=1, window_seconds=3600),
        ]
        result = RateLimitResult(
            checks=checks,
            current_values=[50],
            window_reset_at=hour_reset,
            window_resets=[hour_reset],
        )
        state = _compute_rate_limit_state(result, checks)
        assert state.rpm_limit == 100
        assert state.rpm_remaining == 50
        assert state.rpm_scope == "key_rph"
        assert state.rpm_reset == hour_reset

    def test_mixed_minute_hour_uses_scope_specific_resets(self):
        from src.middleware.rate_limit import _compute_rate_limit_state
        now = time.time()
        minute_reset = int((math.floor(now / 60) + 1) * 60)
        hour_reset = int((math.floor(now / 3600) + 1) * 3600)
        checks = [
            RateLimitCheck(scope="key_rpm", entity_id="h5", limit=10, amount=1, window_seconds=60),
            RateLimitCheck(scope="key_rph", entity_id="h5", limit=100, amount=1, window_seconds=3600),
        ]
        result = RateLimitResult(
            checks=checks,
            current_values=[1, 90],
            window_reset_at=minute_reset,
            window_resets=[minute_reset, hour_reset],
        )
        state = _compute_rate_limit_state(result, checks)
        assert state.rpm_scope == "key_rph"
        assert state.rpm_reset == hour_reset

    def test_rpd_scope_classified_as_rpm_for_headers(self):
        from src.middleware.rate_limit import _compute_rate_limit_state
        now = time.time()
        day_reset = int((math.floor(now / 86400) + 1) * 86400)
        checks = [
            RateLimitCheck(scope="org_rpd", entity_id="h2", limit=1000, amount=1, window_seconds=86400),
        ]
        result = RateLimitResult(
            checks=checks,
            current_values=[999],
            window_reset_at=day_reset,
            window_resets=[day_reset],
        )
        state = _compute_rate_limit_state(result, checks)
        assert state.rpm_limit == 1000
        assert state.rpm_remaining == 1
        assert state.warning == "near_limit"
        assert state.rpm_reset == day_reset

    def test_tpd_scope_classified_as_tpm_for_headers(self):
        from src.middleware.rate_limit import _compute_rate_limit_state
        now = time.time()
        day_reset = int((math.floor(now / 86400) + 1) * 86400)
        checks = [
            RateLimitCheck(scope="team_tpd", entity_id="h3", limit=10000, amount=500, window_seconds=86400),
        ]
        result = RateLimitResult(
            checks=checks,
            current_values=[500],
            window_reset_at=day_reset,
            window_resets=[day_reset],
        )
        state = _compute_rate_limit_state(result, checks)
        assert state.tpm_limit == 10000
        assert state.tpm_remaining == 9500
        assert state.tpm_scope == "team_tpd"
        assert state.tpm_reset == day_reset

    def test_429_state_rph_scope_uses_hour_reset(self):
        from src.middleware.rate_limit import _build_429_state
        checks = [
            RateLimitCheck(scope="key_rph", entity_id="h4", limit=100, amount=1, window_seconds=3600),
            RateLimitCheck(scope="key_tpd", entity_id="h4", limit=50000, amount=500, window_seconds=86400),
        ]
        exc = RateLimitError(message="Rate limit exceeded", param="key_rph", code="key_rph_exceeded", retry_after=1800)
        state = _build_429_state(checks, exc, int(time.time()) + 3600)
        assert state.rpm_limit == 100
        assert state.rpm_remaining == 0
        assert state.rpm_scope == "key_rph"
        now = time.time()
        expected_hour_reset = int((math.floor(now / 3600) + 1) * 3600)
        assert abs(state.rpm_reset - expected_hour_reset) <= 1


class TestRetryAfterDailyWindow:
    @pytest.mark.asyncio
    async def test_retry_after_daily_block(self, counter):
        checks_fill = [
            RateLimitCheck(scope="key_rpd", entity_id="ra1", limit=5, amount=5, window_seconds=86400),
        ]
        await counter.check_rate_limits_atomic(checks_fill)

        checks_exceed = [
            RateLimitCheck(scope="key_rpd", entity_id="ra1", limit=5, amount=1, window_seconds=86400),
        ]
        with pytest.raises(RateLimitError) as exc_info:
            await counter.check_rate_limits_atomic(checks_exceed)
        assert exc_info.value.retry_after > 0
        assert exc_info.value.retry_after <= 86400

    @pytest.mark.asyncio
    async def test_retry_after_hourly_block(self, counter):
        checks_fill = [
            RateLimitCheck(scope="org_rph", entity_id="ra2", limit=3, amount=3, window_seconds=3600),
        ]
        await counter.check_rate_limits_atomic(checks_fill)

        checks_exceed = [
            RateLimitCheck(scope="org_rph", entity_id="ra2", limit=3, amount=1, window_seconds=3600),
        ]
        with pytest.raises(RateLimitError) as exc_info:
            await counter.check_rate_limits_atomic(checks_exceed)
        assert exc_info.value.retry_after > 0
        assert exc_info.value.retry_after <= 3600

    @pytest.mark.asyncio
    async def test_retry_after_fallback_daily_block(self, counter_no_redis):
        checks_fill = [
            RateLimitCheck(scope="team_tpd", entity_id="ra3", limit=100, amount=100, window_seconds=86400),
        ]
        await counter_no_redis.check_rate_limits_atomic(checks_fill)

        checks_exceed = [
            RateLimitCheck(scope="team_tpd", entity_id="ra3", limit=100, amount=1, window_seconds=86400),
        ]
        with pytest.raises(RateLimitError) as exc_info:
            await counter_no_redis.check_rate_limits_atomic(checks_exceed)
        assert exc_info.value.retry_after > 0
        assert exc_info.value.retry_after <= 86400
