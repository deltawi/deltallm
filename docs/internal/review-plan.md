# DeltaLLM Code Review Plan

## Project Overview

**DeltaLLM** is an open-source LLM gateway providing a unified OpenAI-compatible API for multiple LLM providers (OpenAI, Anthropic, Azure, etc.) with enterprise-grade features.

### Architecture Summary
- **Backend**: FastAPI (Python) with async support
- **Database**: PostgreSQL with Prisma ORM
- **Cache/State**: Redis (for caching, rate limits, cooldown state)
- **Frontend**: React-based Admin UI

### Key Features to Review
1. **Core Proxy**: Auth, routing, provider abstraction
2. **Routing & Failover**: Load balancing, cooldowns, health checks
3. **Caching**: Redis/memory backends, cache key composition
4. **Guardrails**: PII detection, prompt injection protection
5. **Observability**: Prometheus metrics, spend tracking, callbacks
6. **Billing**: Budget enforcement, spend ledger, alerts

---

## Review Phases

### Phase 1: Core API & Authentication (P0)
**Focus**: Security-critical authentication and key management systems

**Scope**:
| Module | Files | Lines (approx) |
|--------|-------|----------------|
| Auth System | `src/auth/*.py` | ~800 |
| API Auth Middleware | `src/api/v1/endpoints/auth.py`, `src/middleware/auth.py` | ~400 |
| Key Service | `src/services/key_service.py` | ~200 |
| Platform Identity | `src/services/platform_identity_service.py` | ~300 |
| JWT Handler | `src/auth/jwt.py` | ~150 |
| SSO Handler | `src/auth/sso.py` | ~300 |

**Checklist**:
- [ ] API key validation is constant-time (timing attack prevention)
- [ ] JWT token validation handles all edge cases (expired, malformed, missing signature)
- [ ] Password hashing uses appropriate salt and algorithm
- [ ] Session management is secure (proper TTL, invalidation)
- [ ] RBAC enforcement is consistent across all admin endpoints
- [ ] Master key never leaks in logs or error messages
- [ ] Rate limiting prevents brute force on auth endpoints

**Assigned to**: reviewer1

---

### Phase 2: Provider Adapters & Routing Core (P0)
**Focus**: Request routing, provider abstraction, and failover logic

**Scope**:
| Module | Files | Lines (approx) |
|--------|-------|----------------|
| Router Core | `src/router/router.py`, `src/router/strategies.py` | ~800 |
| Failover Manager | `src/router/failover.py` | ~300 |
| Cooldown Manager | `src/router/cooldown.py` | ~200 |
| Health Tracking | `src/router/health.py`, `src/router/state.py` | ~400 |
| Provider Base | `src/providers/base.py` | ~300 |
| OpenAI Adapter | `src/providers/openai.py` | ~400 |
| Anthropic Adapter | `src/providers/anthropic.py` | ~500 |
| Azure Adapter | `src/providers/azure.py` | ~300 |

**Checklist**:
- [ ] Router handles all error cases from providers (timeout, rate limit, auth failure)
- [ ] Failover chains don't create infinite loops
- [ ] Cooldown state is consistent in multi-instance deployments
- [ ] Health checks don't cause cascading failures
- [ ] Provider adapters properly sanitize input before forwarding
- [ ] Retry logic has exponential backoff and jitter
- [ ] Timeout handling doesn't leave hanging connections

**Assigned to**: reviewer2

---

### Phase 3: Caching System (P1)
**Focus**: Cache correctness, key generation, and backend reliability

**Scope**:
| Module | Files | Lines (approx) |
|--------|-------|----------------|
| Cache Middleware | `src/cache/middleware.py` | ~300 |
| Cache Key Builder | `src/cache/key_builder.py` | ~200 |
| Backends | `src/cache/backends/*.py` | ~600 |
| Streaming Cache | `src/cache/streaming.py` | ~300 |

**Checklist**:
- [ ] Cache key correctly incorporates all relevant request parameters
- [ ] Cache doesn't return stale data after model updates
- [ ] Streaming responses are properly assembled before caching
- [ ] Redis backend handles connection failures gracefully
- [ ] Cache TTL is respected across all backends
- [ ] Cache hit/miss metrics are accurate
- [ ] Sensitive data is never cached (PII in responses)

**Assigned to**: reviewer1

---

### Phase 4: Guardrails & Content Safety (P1)
**Focus**: Guardrail framework, PII detection, prompt injection

**Scope**:
| Module | Files | Lines (approx) |
|--------|-------|----------------|
| Guardrail Base | `src/guardrails/base.py` | ~200 |
| Guardrail Registry | `src/guardrails/registry.py` | ~300 |
| Middleware | `src/guardrails/middleware.py` | ~400 |
| Presidio Integration | `src/guardrails/presidio.py` | ~200 |
| Lakera Integration | `src/guardrails/lakera.py` | ~200 |

**Checklist**:
- [ ] Guardrails run in correct order (pre-call, post-call)
- [ ] Exceptions in guardrails don't crash the request pipeline
- [ ] PII detection doesn't have bypass vulnerabilities
- [ ] Prompt injection detection has adequate coverage
- [ ] Guardrail failures have configurable fail-open/fail-closed behavior
- [ ] Log mode doesn't accidentally expose sensitive data

**Assigned to**: reviewer2

---

### Phase 5: Billing & Spend Tracking (P1)
**Focus**: Cost calculation, budget enforcement, spend ledger

**Scope**:
| Module | Files | Lines (approx) |
|--------|-------|----------------|
| Spend Ledger | `src/billing/ledger.py` | ~300 |
| Spend Tracking | `src/billing/spend.py` | ~400 |
| Budget Enforcement | `src/billing/budget.py` | ~300 |
| Cost Calculation | `src/billing/cost.py` | ~200 |
| Alerts | `src/billing/alerts.py` | ~200 |

**Checklist**:
- [ ] Token counting is accurate for all providers
- [ ] Cost calculation uses correct pricing (including cached tokens)
- [ ] Budget enforcement is race-condition free
- [ ] Spend updates are atomic
- [ ] Budget reset logic handles timezone correctly
- [ ] Soft budget alerts don't spam
- [ ] Spend logs can't be tampered with

**Assigned to**: reviewer1

---

### Phase 6: Rate Limiting & Middleware (P1)
**Focus**: Rate limit counters, middleware stack, error handling

**Scope**:
| Module | Files | Lines (approx) |
|--------|-------|----------------|
| Rate Limit Middleware | `src/middleware/rate_limit.py` | ~400 |
| Limit Counter | `src/services/limit_counter.py` | ~300 |
| Error Middleware | `src/middleware/errors.py` | ~200 |
| Platform Auth Middleware | `src/middleware/platform_auth.py` | ~200 |
| Admin Middleware | `src/middleware/admin.py` | ~150 |

**Checklist**:
- [ ] Rate limits are enforced hierarchically (org > team > key > user)
- [ ] Redis failures gracefully degrade to in-memory counters
- [ ] Rate limit windows reset correctly
- [ ] Parallel request limits work correctly
- [ ] Error responses don't leak sensitive info
- [ ] All middleware handles exceptions without hanging

**Assigned to**: reviewer2

---

### Phase 7: Configuration & Startup (P2)
**Focus**: Config loading, secret resolution, dynamic config

**Scope**:
| Module | Files | Lines (approx) |
|--------|-------|----------------|
| Config Loader | `src/config.py` | ~300 |
| Config Runtime | `src/config_runtime/*.py` | ~800 |
| Main App | `src/main.py` | ~400 |

**Checklist**:
- [ ] Secret resolution doesn't log sensitive values
- [ ] Invalid config fails fast with clear errors
- [ ] Environment variable interpolation handles missing vars
- [ ] Dynamic config reload doesn't cause race conditions
- [ ] Config precedence (file vs DB) is correct
- [ ] Hot reload doesn't lose in-flight requests

**Assigned to**: reviewer1

---

### Phase 8: Admin API & Database (P2)
**Focus**: Admin endpoints, database repositories, model management

**Scope**:
| Module | Files | Lines (approx) |
|--------|-------|----------------|
| Admin Endpoints | `src/api/admin/endpoints/*.py` | ~1000 |
| Admin Router | `src/api/admin/router.py` | ~200 |
| Repositories | `src/db/repositories.py` | ~600 |
| DB Client | `src/db/client.py` | ~200 |

**Checklist**:
- [ ] All admin endpoints require proper authentication
- [ ] Admin endpoints have proper authorization checks
- [ ] Database queries are parameterized (SQL injection prevention)
- [ ] Pagination works correctly on list endpoints
- [ ] Model deployment changes propagate correctly
- [ ] Key regeneration invalidates old keys immediately

**Assigned to**: reviewer2

---

## Issue Recording Format

When reviewers find issues, record them in `docs/internal/issues.md` with this format:

```markdown
### Issue #X: [Brief Title]

**Severity**: CRITICAL | HIGH | MEDIUM | LOW
**Phase**: [Phase number]
**Found by**: [reviewer1 | reviewer2]
**Status**: OPEN | CONFIRMED | IN_PROGRESS | FIXED

**Description**:
[Detailed description of the issue]

**Files Impacted**:
- `src/.../file.py` (lines X-Y)

**Suggested Fix**:
[Description of how to fix]

**Actual Fix** (filled by developer):
[Description of fix applied]
```

---

## Developer Workflow

1. Monitor `docs/internal/issues.md` for new issues
2. For each CRITICAL/HIGH issue:
   - Confirm the issue is valid
   - Create a fix branch if needed
   - Implement the fix
   - Update the issue with fix details
   - Mark status as FIXED
3. For MEDIUM/LOW issues, batch fix if possible

---

## Timeline

| Phase | Reviewer | Estimated Duration |
|-------|----------|-------------------|
| Phase 1 | reviewer1 | 2-3 hours |
| Phase 2 | reviewer2 | 2-3 hours |
| Phase 3 | reviewer1 | 1-2 hours |
| Phase 4 | reviewer2 | 1-2 hours |
| Phase 5 | reviewer1 | 1-2 hours |
| Phase 6 | reviewer2 | 1-2 hours |
| Phase 7 | reviewer1 | 1 hour |
| Phase 8 | reviewer2 | 1-2 hours |

**Total estimated review time**: ~16 hours across both reviewers

---

## Communication Protocol

- Reviewers report issues via `docs/internal/issues.md`
- Lead (me) coordinates parallel review phases
- Developer picks up issues from the issues file
- Use relay messaging for urgent coordination
