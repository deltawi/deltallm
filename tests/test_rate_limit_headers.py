from __future__ import annotations

import time

import pytest

from src.services.limit_counter import LimitCounter, RateLimitCheck, RateLimitResult
from src.middleware.rate_limit import (
    RateLimitState,
    _compute_rate_limit_state,
    build_rate_limit_headers,
)


class TestLuaScriptReturnsValues:
    @pytest.mark.asyncio
    async def test_redis_atomic_returns_current_values(self, test_app):
        limiter = test_app.state.limit_counter
        checks = [
            RateLimitCheck(scope="key_rpm", entity_id="k1", limit=100, amount=1),
            RateLimitCheck(scope="key_tpm", entity_id="k1", limit=5000, amount=50),
        ]
        result = await limiter.check_rate_limits_atomic(checks)
        assert isinstance(result, RateLimitResult)
        assert len(result.checks) == 2
        assert len(result.current_values) == 2
        assert result.current_values[0] == 1
        assert result.current_values[1] == 50
        assert result.window_reset_at > 0

    @pytest.mark.asyncio
    async def test_redis_atomic_increments_accumulate(self, test_app):
        limiter = test_app.state.limit_counter
        checks = [
            RateLimitCheck(scope="key_rpm", entity_id="k-acc", limit=100, amount=1),
        ]
        r1 = await limiter.check_rate_limits_atomic(checks)
        r2 = await limiter.check_rate_limits_atomic(checks)
        assert r1.current_values[0] == 1
        assert r2.current_values[0] == 2

    @pytest.mark.asyncio
    async def test_fallback_returns_current_values(self):
        limiter = LimitCounter(redis_client=None, degraded_mode="fail_open")
        checks = [
            RateLimitCheck(scope="key_rpm", entity_id="k2", limit=100, amount=1),
            RateLimitCheck(scope="key_tpm", entity_id="k2", limit=5000, amount=200),
        ]
        result = await limiter.check_rate_limits_atomic(checks)
        assert isinstance(result, RateLimitResult)
        assert len(result.current_values) == 2
        assert result.current_values[0] == 1
        assert result.current_values[1] == 200
        assert result.window_reset_at > 0

    @pytest.mark.asyncio
    async def test_empty_checks_returns_empty_result(self, test_app):
        limiter = test_app.state.limit_counter
        result = await limiter.check_rate_limits_atomic([])
        assert result.checks == []
        assert result.current_values == []


class TestComputeRateLimitState:
    def test_zero_percent_usage(self):
        checks = [
            RateLimitCheck(scope="key_rpm", entity_id="k1", limit=100, amount=1),
            RateLimitCheck(scope="key_tpm", entity_id="k1", limit=10000, amount=100),
        ]
        result = RateLimitResult(
            checks=checks,
            current_values=[1, 100],
            window_reset_at=int(time.time()) + 60,
        )
        state = _compute_rate_limit_state(result, checks)
        assert state.rpm_limit == 100
        assert state.rpm_remaining == 99
        assert state.tpm_limit == 10000
        assert state.tpm_remaining == 9900
        assert state.warning is None

    def test_50_percent_usage(self):
        checks = [
            RateLimitCheck(scope="key_rpm", entity_id="k1", limit=100, amount=1),
            RateLimitCheck(scope="key_tpm", entity_id="k1", limit=10000, amount=100),
        ]
        result = RateLimitResult(
            checks=checks,
            current_values=[50, 5000],
            window_reset_at=int(time.time()) + 60,
        )
        state = _compute_rate_limit_state(result, checks)
        assert state.rpm_remaining == 50
        assert state.tpm_remaining == 5000
        assert state.warning is None

    def test_80_percent_triggers_approaching_limit(self):
        checks = [
            RateLimitCheck(scope="key_rpm", entity_id="k1", limit=100, amount=1),
        ]
        result = RateLimitResult(
            checks=checks,
            current_values=[82],
            window_reset_at=int(time.time()) + 60,
        )
        state = _compute_rate_limit_state(result, checks)
        assert state.rpm_remaining == 18
        assert state.warning == "approaching_limit"

    def test_95_percent_triggers_near_limit(self):
        checks = [
            RateLimitCheck(scope="key_rpm", entity_id="k1", limit=100, amount=1),
        ]
        result = RateLimitResult(
            checks=checks,
            current_values=[96],
            window_reset_at=int(time.time()) + 60,
        )
        state = _compute_rate_limit_state(result, checks)
        assert state.rpm_remaining == 4
        assert state.warning == "near_limit"

    def test_99_percent_triggers_near_limit(self):
        checks = [
            RateLimitCheck(scope="key_rpm", entity_id="k1", limit=100, amount=1),
            RateLimitCheck(scope="key_tpm", entity_id="k1", limit=10000, amount=100),
        ]
        result = RateLimitResult(
            checks=checks,
            current_values=[99, 100],
            window_reset_at=int(time.time()) + 60,
        )
        state = _compute_rate_limit_state(result, checks)
        assert state.warning == "near_limit"

    def test_100_percent_usage(self):
        checks = [
            RateLimitCheck(scope="key_rpm", entity_id="k1", limit=100, amount=1),
        ]
        result = RateLimitResult(
            checks=checks,
            current_values=[100],
            window_reset_at=int(time.time()) + 60,
        )
        state = _compute_rate_limit_state(result, checks)
        assert state.rpm_remaining == 0
        assert state.warning == "near_limit"

    def test_binding_scope_selects_most_restrictive(self):
        checks = [
            RateLimitCheck(scope="org_rpm", entity_id="o1", limit=1000, amount=1),
            RateLimitCheck(scope="team_rpm", entity_id="t1", limit=100, amount=1),
            RateLimitCheck(scope="key_rpm", entity_id="k1", limit=500, amount=1),
        ]
        result = RateLimitResult(
            checks=checks,
            current_values=[100, 90, 50],
            window_reset_at=int(time.time()) + 60,
        )
        state = _compute_rate_limit_state(result, checks)
        assert state.rpm_scope == "team_rpm"
        assert state.rpm_limit == 100
        assert state.rpm_remaining == 10

    def test_empty_result_returns_empty_state(self):
        result = RateLimitResult()
        state = _compute_rate_limit_state(result, [])
        assert state.rpm_limit == 0
        assert state.tpm_limit == 0
        assert state.warning is None


class TestBuildRateLimitHeaders:
    def test_headers_populated_with_limits(self):
        state = RateLimitState(
            rpm_limit=100,
            rpm_remaining=80,
            rpm_reset=1700000000,
            rpm_scope="key_rpm",
            tpm_limit=50000,
            tpm_remaining=45000,
            tpm_reset=1700000000,
            tpm_scope="key_tpm",
            warning=None,
        )
        headers = build_rate_limit_headers(state)
        assert headers["x-ratelimit-limit-requests"] == "100"
        assert headers["x-ratelimit-remaining-requests"] == "80"
        assert headers["x-ratelimit-reset-requests"] == "1700000000"
        assert headers["x-ratelimit-limit-tokens"] == "50000"
        assert headers["x-ratelimit-remaining-tokens"] == "45000"
        assert headers["x-ratelimit-reset-tokens"] == "1700000000"
        assert headers["x-deltallm-ratelimit-scope"] == "key_rpm,key_tpm"
        assert "x-ratelimit-warning" not in headers

    def test_warning_header_present(self):
        state = RateLimitState(
            rpm_limit=100,
            rpm_remaining=10,
            rpm_reset=1700000000,
            rpm_scope="team_rpm",
            tpm_limit=0,
            tpm_remaining=0,
            tpm_reset=0,
            tpm_scope="",
            warning="approaching_limit",
        )
        headers = build_rate_limit_headers(state)
        assert headers["x-ratelimit-warning"] == "approaching_limit"

    def test_no_limits_returns_empty_headers(self):
        state = RateLimitState()
        headers = build_rate_limit_headers(state)
        assert headers == {}

    def test_warning_only_when_no_limits(self):
        state = RateLimitState(warning="near_limit")
        headers = build_rate_limit_headers(state)
        assert headers == {"x-ratelimit-warning": "near_limit"}


class TestIntegrationHeaders:
    @pytest.mark.asyncio
    async def test_normal_request_has_rate_limit_headers(self, client, test_app):
        headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
        response = await client.post(
            "/v1/chat/completions",
            headers=headers,
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert response.status_code == 200
        assert "x-ratelimit-limit-requests" in response.headers
        assert "x-ratelimit-remaining-requests" in response.headers
        assert "x-ratelimit-reset-requests" in response.headers
        assert "x-ratelimit-limit-tokens" in response.headers
        assert "x-ratelimit-remaining-tokens" in response.headers
        assert "x-ratelimit-reset-tokens" in response.headers

    @pytest.mark.asyncio
    async def test_429_has_zero_remaining(self, client, test_app):
        limiter = test_app.state.limit_counter
        window_id = limiter._window_id(60)
        redis = test_app.state.redis
        salt = "test-salt"
        raw_key = test_app.state._test_key
        import hashlib
        token_hash = hashlib.sha256(f"{salt}:{raw_key}".encode("utf-8")).hexdigest()

        key = f"ratelimit:key_rpm:{token_hash}:{window_id}"
        redis.store[key] = 2

        headers = {"Authorization": f"Bearer {raw_key}"}
        response = await client.post(
            "/v1/chat/completions",
            headers=headers,
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert response.status_code == 429
        assert response.headers.get("x-ratelimit-remaining-requests") == "0"

    @pytest.mark.asyncio
    async def test_approaching_limit_warning_at_82_percent(self, client, test_app):
        limiter = test_app.state.limit_counter
        window_id = limiter._window_id(60)
        redis = test_app.state.redis
        rpm_limit = 100

        salt = "test-salt"
        raw_key = test_app.state._test_key
        import hashlib
        token_hash = hashlib.sha256(f"{salt}:{raw_key}".encode("utf-8")).hexdigest()
        repo = test_app.state._test_repo
        original_record = repo.records[token_hash]

        from src.db.repositories import KeyRecord
        repo.records[token_hash] = KeyRecord(
            token=token_hash,
            team_id=original_record.team_id,
            organization_id=original_record.organization_id,
            models=original_record.models,
            rpm_limit=rpm_limit,
            tpm_limit=original_record.tpm_limit,
            max_parallel_requests=original_record.max_parallel_requests,
            expires=original_record.expires,
        )
        await redis.delete(f"key:{token_hash}")

        key = f"ratelimit:key_rpm:{token_hash}:{window_id}"
        redis.store[key] = 81

        headers = {"Authorization": f"Bearer {raw_key}"}
        response = await client.post(
            "/v1/chat/completions",
            headers=headers,
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert response.status_code == 200
        assert response.headers.get("x-ratelimit-warning") == "approaching_limit"

        repo.records[token_hash] = original_record
        await redis.delete(f"key:{token_hash}")

    @pytest.mark.asyncio
    async def test_near_limit_warning_at_96_percent(self, client, test_app):
        limiter = test_app.state.limit_counter
        window_id = limiter._window_id(60)
        redis = test_app.state.redis
        rpm_limit = 100

        salt = "test-salt"
        raw_key = test_app.state._test_key
        import hashlib
        token_hash = hashlib.sha256(f"{salt}:{raw_key}".encode("utf-8")).hexdigest()
        repo = test_app.state._test_repo
        original_record = repo.records[token_hash]

        from src.db.repositories import KeyRecord
        repo.records[token_hash] = KeyRecord(
            token=token_hash,
            team_id=original_record.team_id,
            organization_id=original_record.organization_id,
            models=original_record.models,
            rpm_limit=rpm_limit,
            tpm_limit=original_record.tpm_limit,
            max_parallel_requests=original_record.max_parallel_requests,
            expires=original_record.expires,
        )
        await redis.delete(f"key:{token_hash}")

        key = f"ratelimit:key_rpm:{token_hash}:{window_id}"
        redis.store[key] = 95

        headers = {"Authorization": f"Bearer {raw_key}"}
        response = await client.post(
            "/v1/chat/completions",
            headers=headers,
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert response.status_code == 200
        assert response.headers.get("x-ratelimit-warning") == "near_limit"

        repo.records[token_hash] = original_record
        await redis.delete(f"key:{token_hash}")
