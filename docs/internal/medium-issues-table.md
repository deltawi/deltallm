# MEDIUM Priority Issues - Complete List

| Issue ID | Phase | Component | Description |
|----------|-------|-----------|-------------|
| AUTH-003 | 1 | Auth | Session tokens not bound to client characteristics (IP, User-Agent) - stolen tokens usable from any client |
| AUTH-005 | 1 | Auth | PKCE not implemented for OAuth2 flow - vulnerable to authorization code interception |
| AUTH-006 | 1 | Auth | SSO state parameter not cryptographically random - no server verification of state generation |
| AUTH-007 | 1 | Auth | In-memory SSO state storage breaks in multi-instance deployments, restarts, K8s scaling |
| AUTH-008 | 1 | Auth | JWT handler accepts issuer=None, disabling issuer verification |
| AUTH-010 | 1 | Auth | Default salt value "change-me" in PlatformIdentityService (superseded by CONFIG-001) |
| CONFIG-002 | 7 | Config | Master key has no length/complexity validation - weak keys could be brute-forced |
| CONFIG-003 | 7 | Config | Redis password stored as plain string - could be exposed in logs |
| CONFIG-004 | 7 | Config | Config update race condition - _store_db_config and _reload_config not atomic |
| CONFIG-005 | 7 | Config | Secret resolution missing exception handling - secret manager failures block startup |
| CONFIG-006 | 7 | Config | Model hot reload not thread-safe - registry modified in-place without locks |
| CONFIG-007 | 7 | Config | No database health check at startup - failures occur later instead of fail-fast |
| CACHE-001 | 3 | Cache | Streaming cache reconstruction loses original chunk boundaries and tool call deltas |
| CACHE-002 | 3 | Cache | Streaming cache handler missing JSON exception handling - malformed data crashes stream |
| CACHE-004 | 3 | Cache | Redis delete/clear methods missing exception handling - failures propagate |
| CACHE-009 | 3 | Cache | Redis clear uses unbounded SCAN - slow on large instances |
| CACHE-010 | 3 | Cache | In-memory backend memory leak - entries never proactively expired |
| GUARD-003 | 4 | Guardrails | Lakera API errors may log sensitive data (full HTTP response with prompt text) |
| GUARD-004 | 4 | Guardrails | Presidio regex patterns easily bypassed (e.g., "user at example dot com") |
| GUARD-007 | 4 | Guardrails | No circuit breaker on Lakera API - cascading latency if service degraded |
| RATE-002 | 6 | Rate Limit | Fallback counters memory leak - entries never cleaned up |
| RATE-003 | 6 | Rate Limit | Non-atomic fallback rate checks - race conditions in batch check-and-increment |
| RATE-005 | 6 | Rate Limit | Error handler may log sensitive data (API keys, tokens, user content in stack traces) |
| ADMIN-001 | 8 | Admin API | List endpoints missing pagination - performance/DoS risk with large tables |
| ADMIN-003 | 8 | Admin API | Model deployment changes not propagated to runtime in-memory registry |
| ADMIN-004 | 8 | Admin API | Prisma query returns empty list on connection failure - masks DB issues |

## Summary by Phase

| Phase | Component | MEDIUM Count |
|-------|-----------|--------------|
| 1 | Core API & Auth | 6 |
| 3 | Caching | 5 |
| 4 | Guardrails | 3 |
| 6 | Rate Limiting | 3 |
| 7 | Configuration | 6 |
| 8 | Admin API | 3 |
| **TOTAL** | | **30** |
