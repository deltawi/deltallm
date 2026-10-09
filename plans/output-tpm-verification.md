# Output TPM reservation baseline: historical verification

Status: superseded by the completion accounting implementation. See [current implementation and verification](output-tpm-usage-verification.md). The results and runtime description below are retained as historical evidence and do not describe the current worktree behavior.

Date: 5 October 2026. Branch: `feature/output-tpm-limit`.
Base: `f5ffd80dfb4c100a2063fb6a7939bd6f7ba68bd2`, fetched from `origin/main`.

The implementation adds nullable `output_tpm_limit` policies for API keys, runtime users, teams, and organizations. The database, joined authentication query, cache invalidation, admin APIs, UI controls, and public documentation contain the field. Omitted updates preserve the value. A null value clears it. All configured parent scopes apply across text models and synchronous and local batch requests.

## Enforcement boundary

- The limit uses the UTC minute in which each provider attempt starts. It is an admission-minute limit, not a rolling window or a generation-time token limit.
- Each governed request needs an explicit output cap. The gateway reserves `cap * n` in the existing atomic admission transaction. Complete provider output usage returns unused allowance once.
- Failed dispatches, partial streams, cancellation, and unknown usage retain the full allowance after dispatch. Cache hits and failures before dispatch settle to zero. Each answer retry and MCP model phase gets a separate reservation.
- Official OpenAI and Anthropic text endpoints are qualified. Endpoint and payload checks run after deployment defaults. Unsupported candidates fail locally without a provider health penalty. Realtime and provider-native batch calls are outside this release. Governed local batch items use individual provider calls and the existing claim fencing.
- Selector classification remains gateway work outside the caller output policy, as the reviewed plan specifies. Its current routing and billing controls still apply.
- Output policies require protected standalone Redis, no eviction, and `fail_closed`. Shared policies require stored API-key authentication. After a state-changing Redis restart, restore, flush, or failover, pause governed ingress until the next UTC minute. Redis Cluster and automatic continuity across lost Redis state are outside this release.

The provider cap is necessary for the reservation ceiling. If a qualified provider reports output above its cap, accounting records the larger value and emits `cap_exceeded`. Settlement failure retains the reservation and cannot cause a provider retry.

## Scope and efficiency

There are no new clients, pools, background tasks, deployment allocators, or configuration fields. SQL remains in the repository layer. Authentication reads the four policies in its existing joined query. Output coordination has a one-second deadline.

Admission uses one Redis transaction for ordinary limits, applicable tier/fair-share/parallel controls, and all output scopes. Complete usage needs at most one settlement transaction. Unknown usage needs no settlement call. Streams have no per-chunk Redis calls. Responses reuse the provider adapter's existing JSON parse.

The rate-limit contracts, lease types, and admission Lua were extracted before the large existing owners were extended. `rate_limit_policy.py` and `limit_counter.py` still contain existing size debt. This release adds narrow typed calls into those owners. A complete rewrite would change existing quota and lease behavior beyond this feature. The rate-limit service owns the remaining extraction; it belongs in the next admission-owner refactor.

Auth cache version 5 invalidates versions 4 and 5 during rollout. Response cache version 6 prevents use of old entries and includes `max_completion_tokens` in the default key fields. The API-key page uses the existing lazy route wrapper to keep the initial UI bundle below the main-branch baseline.

## Verification results

The locked development environment used Python 3.12.8, Node 23.6.1, PostgreSQL 16, and Redis 7.2. PostgreSQL and Redis were disposable local services created for this task. The commands below used their local test URLs through `DATABASE_URL`, `REDIS_URL`, and `DELTALLM_TEST_REDIS_URL` where required.

| Check | Command | Result |
| --- | --- | --- |
| Full hermetic and application suites | `.venv/bin/pytest -m 'hermetic or app' -q --tb=short` | 5,546 passed; four setup errors from concurrent UI asset generation. All four passed after the build completed. |
| Full PostgreSQL lane | `.venv/bin/pytest -m postgres -q --tb=short` | 360 passed, four skipped by existing test guards. Both new output-policy database tests executed. |
| Full Redis lane, final state | `.venv/bin/pytest -m redis -q --tb=short` | 135 passed. |
| Full Helm lane | `.venv/bin/pytest -m helm -q --tb=short` | 71 passed. |
| Final provider, stream, and batch checks | `.venv/bin/pytest tests/test_output_tpm_text_redis.py tests/test_output_tpm_batch_redis.py tests/test_output_tpm_contracts.py tests/test_chat_hop_compatibility.py tests/providers -q --tb=short` | 437 passed. |
| Modern-cap translation and single-parse evidence | `.venv/bin/pytest tests/test_output_tpm_contracts.py tests/test_provider_compat.py -q --tb=short` | 174 passed. |
| Final cache checks after version change | `.venv/bin/pytest tests/test_cache.py tests/test_cache_execution_eligibility.py tests/test_routing_cache_identity.py tests/test_cache_redis_integration.py tests/test_cache_routing_redis_integration.py tests/test_output_tpm_text_redis.py -q --tb=short` | 94 passed. Includes rejection of old cache version 5 and separate entries for different modern output caps. |
| Final policy persistence and API checks | `.venv/bin/pytest tests/db/test_output_tpm_policy.py tests/test_output_tpm_admin.py tests/test_ui_rate_limits.py tests/test_ui_self_service_keys.py tests/bootstrap/test_auth_bootstrap.py tests/config/test_dynamic.py -q --tb=short` | 126 passed. |
| Prisma client | `.venv/bin/prisma generate --schema=./prisma/schema.prisma` | Successful. |
| Migration paths | `.venv/bin/python scripts/verify_migration_paths.py --admin-database-url "$TEST_ADMIN_DATABASE_URL" --base-ref v0.1.47` | Fresh install, last-release upgrade, and shared-feature upgrade passed. |
| Python lint and format | `.venv/bin/ruff check` and `.venv/bin/ruff format --check` with the 64 changed Python paths | Passed. |
| UI unit tests | `npm --prefix ui run test:unit` | 281 passed. |
| UI production build | `npm --prefix ui run build` | Passed. Initial JavaScript: 370.15 KB gzip; baseline: 377.54 KB gzip. |
| UI full lint | `npm --prefix ui run lint -- --format json`, also run on the base revision | Both report 116 errors and three warnings. Zero new findings after source-path and location normalization. New UI modules and the route change have zero errors. |
| Config reference | `.venv/bin/python scripts/docs/generate_config_reference.py --check` | Current; 323 fields. |
| Test classification | `.venv/bin/pytest --collect-only -qq --dependency-lane-report` | 6,121 tests in exactly one of the five primary lanes: 3,963 hermetic, 1,588 app, 364 PostgreSQL, 135 Redis, 71 Helm. |
| Whitespace | `git diff --check` | Passed. |

The broad suite ran before the final cache namespace update and repository extraction. The final affected cache, policy, database, and Redis checks cover those changes. The table lists overlapping runs; their counts must not be added together.

Real Redis checks cover concurrent admission from two limiter instances, all four shared scopes, ordinary and unified admission replay, settlement replay, no partial charge on denial, wrong key types, corrupt ownership and minute values, ownership expiry, old-minute settlement, and serialization bounds. Application checks cover all four text API shapes, JSON and SSE, retries, MCP phases, cache hits, cancellation, incomplete streams, and raw Anthropic output evidence. Batch checks cover shared synchronous quotas, claim epochs, lost execution, and individual execution of governed items.

The new UI tests cover permission denial, pending saves, server denial, associated errors, successful saves, route loading, output-field focus, Escape, and focus restoration at 375 and 1,024 pixels. Browser visual inspection was blocked because the environment could not verify its admin access policy. The visual layout smoke test remains unverified. The DOM tests do not prove rendered layout.

Full UI lint remains a failing baseline gate. Existing errors in the touched pages and summary component were preserved; the feature adds no lint findings. The production build also retains the existing large-chunk warning. No lint rules or tests were disabled.

## Local performance evidence

The [profile](../tests/performance/output_tpm_profile.py) reuses the existing constant-arrival load runner. Each case sends 800 requests at 100 requests/second for eight seconds. It uses an in-process ASGI app, fixed provider responses, fake authentication/routing/billing, and real Redis. The baseline uses the main-branch code with the same profile. These short local runs measure gateway behavior; they do not establish production capacity or real-provider time to first token.

| Case | p50 / p95 / p99, milliseconds | Quota Redis calls per request |
| --- | --- | --- |
| Main-branch baseline, no output policy | 5.46 / 8.51 / 26.82 | One `EVAL` |
| Final no-policy path, after cache update | 5.24 / 7.59 / 23.73 | One `EVAL` |
| One output scope, JSON | 6.56 / 8.83 / 12.73 | Two `EVALSHA` |
| Four output scopes, JSON | 6.16 / 8.21 / 11.11 | Two `EVALSHA` |
| Four output scopes, complete stream | 6.81 / 9.32 / 15.18 | Two `EVALSHA` |
| Four output scopes, unknown stream usage | 5.40 / 6.87 / 9.60 | One `EVALSHA` |
| Final four-scope cache hit | 6.35 / 8.59 / 11.15 | Two `EVALSHA`; no provider call |

Every run completed all 800 requests with HTTP 200 and zero generator drops. Final runs had zero sampled in-flight growth. The profile asserts the expected Redis command counts. Host load and short samples limit latency comparisons; the figures do not prove a percentage improvement.

Measured Redis ownership records used 424 bytes each. Bucket records used approximately 232–240 bytes each in the four-scope cases. At the default 600-second router timeout, ownership retention is 630 seconds plus up to 60 seconds until the admission minute resets. At 100 admitted attempts/second, about 69,000 retained ownership records use about 29 MB at the measured record size, plus buckets and operating headroom. The serialized ownership ceiling is 4 KiB and the key ceiling is 512 bytes; deployments must size Redis for their scope counts, attempt rates, deadlines, and these larger bounds. Existing pools are reused; governed complete attempts add one Redis command each.

Machine-readable evidence is saved in [output-tpm-profile.json](output-tpm-profile.json). The [authentication query plan](output-tpm-auth-query-plan.json) confirms one joined query with a local four-scope fixture. It reports 0.487 ms planning and 0.300 ms execution on that small database. Production cardinalities require separate measurements. Process peak RSS includes the whole test app and is not an incremental feature memory measurement.

## Rollout

Apply the additive migration first. Upgrade or drain all gateway and batch-worker replicas before enabling policies. Configure protected Redis with no eviction and `fail_closed`. Start with a key policy and explicit request caps, then add shared scopes. The [rate-limiting documentation](../docs/features/rate-limiting.md#output-tokens-per-minute) contains the public contract and Redis recovery procedure.

To stop enforcement, clear the policies with null while keeping the additive columns. Previous response-cache entries expire through their normal TTL. The worktree contains the complete change for review.
