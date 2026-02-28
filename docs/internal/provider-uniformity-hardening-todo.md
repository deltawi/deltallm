# Provider Uniformity Hardening Plan & TODO

## Scope
- Objective: enforce uniform behavior across providers/endpoints for auth, limits, budgets, guardrails, fallback, spend/usage, and error semantics.
- Endpoints in scope first: `/v1/chat/completions`, `/v1/completions`, `/v1/responses`.
- Performance rule: keep request-path checks minimal; defer non-critical side effects until after response where safe.

## Phase 0 — Design Lock (Completed)

### Locked Decisions
- Streaming fallback/retry is allowed **only before first token**.
- Budget exceeded must return the appropriate quota-style status code (`429`) with stable error payload.
- Redis degraded mode must be policy-driven (`fail_open` or `fail_closed`) and observable.
- Cached responses can have separate pricing; cache-hit accounting must be explicit and deterministic.

### Current-State Findings (from code audit)
- Shared text handler exists for non-stream text endpoints, but stream path bypasses failover/retry and full post-call accounting.
- Cache middleware runs before route dependencies, so cache-hit serving is not guaranteed to run all controls first.
- Budget enforcement service is initialized but not wired into data-plane request execution.
- Fallback attribution currently anchors to the primary deployment/provider in several metrics/logging paths.
- Cache-aware pricing exists in cost calculation code but cache-hit propagation is incomplete.

### Acceptance Criteria
- No endpoint/provider bypasses auth/rate-limit/budget/guardrail pre-checks.
- Streaming and non-streaming follow the same enforcement contract (with first-token retry boundary).
- Budget denials return `429` consistently.
- Metrics/logging/spend attribution reflects the deployment/provider that actually served the request.
- Redis degraded mode is explicit, test-covered, and operationally visible.

---

## Execution Plan & TODO

### Phase 1 — Shared Preflight Pipeline (Completed)
- [x] Add a shared preflight function for text endpoints.
- [x] Ensure preflight order: auth context -> rate limit -> budget check -> model permission -> guardrail pre-call.
- [x] Reuse the preflight in chat/completions/responses paths without duplicating logic.
- [x] Add tests for uniform preflight behavior across all three text endpoints.

### Phase 2 — Cache/Auth Safety (Completed)
- [x] Ensure auth/rate-limit/budget checks execute before cache-hit responses.
- [x] Add cache partitioning by auth scope (key hash or org/team scope) with a safe default.
- [x] Add tests proving no cross-key leakage on cache-hit.

### Phase 3 — Streaming Parity (Completed)
- [x] Move stream execution onto shared lifecycle stages.
- [x] Apply failover/retry only before first token.
- [x] Apply default params for stream path.
- [x] Add stream-side post-call accounting hooks with minimal latency overhead.

### Phase 4 — Budget Enforcement (Completed)
- [x] Wire `BudgetEnforcementService` into billable request paths.
- [x] Add dedicated budget-denied proxy error mapped to HTTP `429`.
- [x] Add tests for key/user/team/org budget blocks.

### Phase 5 — Provider Attribution (Completed)
- [x] Return actual serving deployment/provider from failover execution.
- [x] Use actual provider in metrics/logs/spend callbacks.
- [x] Add fallback attribution tests.

### Phase 6 — Async Post-Response Side Effects (Completed)
- [x] Move non-critical spend/log writes to background execution after response flush.
- [x] Keep correctness-critical checks synchronous.
- [x] Add validation tests that preserve request success while side effects run asynchronously.

### Phase 7 — Redis Degraded Policy (Completed)
- [x] Add configurable degraded behavior (`fail_open` / `fail_closed`) for limiter state.
- [x] Implement in-memory fallback limiter when running fail-open.
- [x] Add tests for both policies.

### Phase 8 — Cache-Aware Pricing Consistency (Completed)
- [x] Propagate `cache_hit` reliably into cost + spend logs.
- [x] Validate cached vs uncached pricing signal in tests.

### Phase 9 — Documentation & Rollout Notes (Completed)
- [x] Document lifecycle, retry boundary, budget code semantics, and degraded behavior in `docs/internal`.
- [x] Record rollout toggles and rollback steps.

#### Rollout / Runtime Toggles
- `general_settings.redis_degraded_mode` (`fail_open` default, `fail_closed` optional strict mode).
- Stream retry/fallback boundary remains fixed to "before first token only".
- Cache scope keying includes authenticated API key scope by default.

## Validation Log
- [x] Targeted regression suite passed after Phase 1:
  - `tests/test_text_endpoints.py`
  - `tests/test_provider_compat.py`
  - `tests/test_rate_limit.py`
- [x] Targeted suite passed after Phase 2:
  - `tests/test_cache.py`
  - `tests/test_text_endpoints.py`
  - `tests/test_rate_limit.py`
  - `tests/test_provider_compat.py`
- [x] Targeted suite passed after Phase 3:
  - `tests/test_chat.py`
  - `tests/test_text_endpoints.py`
  - `tests/test_cache.py`
  - `tests/test_provider_compat.py`
  - `tests/test_rate_limit.py`
- [x] Expanded suite passed after Phase 4-9:
  - `tests/test_uniformity_hardening.py`
  - `tests/test_cache.py`
  - `tests/test_chat.py`
  - `tests/test_text_endpoints.py`
  - `tests/test_embeddings.py`
  - `tests/test_rate_limit.py`
  - `tests/test_limit_counter_atomic.py`
  - `tests/test_provider_compat.py`
