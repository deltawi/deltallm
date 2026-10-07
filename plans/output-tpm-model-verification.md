# Output TPM by model: implementation and verification

Date: 2026-10-07. Baseline: `ea2200c415e48fead83f34c38d8c76faa642cff7`.

## Ownership and scope

Tier model policies now carry an output TPM allowance for each organization and
callable model. The existing tier compiler selects the effective assignment.
Team and key maps add independent model limits. Scalar output limits still
apply across models. Every configured parent and child scope must pass.

The existing output policy, Redis admission script, provider usage evidence,
completion accounting, and cleanup owner enforce the new limits. The maximum
scope count changes from four to seven. There are no new runtime dependencies,
clients, pools, background tasks, or configuration switches. Shared
capacity-pool output limits remain outside this change.

The existing soft-limit contract remains in effect: an admitted call can
finish after crossing a limit. Later calls wait for the next fixed UTC minute.
Unknown dispatched output closes all captured scopes for that minute. Complete
provider evidence is required. The existing OpenAI-compatible, Groq, vLLM,
Anthropic, Gemini, and Bedrock adapters retain their endpoint capabilities.

## Verification results

| Gate | Result |
| --- | --- |
| Full hermetic lane, with loopback sockets | 4,119 passed; no skips |
| Full application lane | 1,599 passed; two later publication regression cases also passed in the focused admin run |
| Full PostgreSQL lane, with Redis | 384 passed; the four official SDK cases passed separately with the pinned isolated SDK environment |
| Full Redis lane | 185 passed |
| Full Helm lane | 71 passed |
| Final focused policy, tier API, accounting contract, and lane checks | 156 passed |
| Final focused Redis accounting checks | 31 passed |
| Final model admin checks | 5 passed, including fail-open activation and re-enable rejection |
| Fresh, last-release, shared-feature, and model-identity recovery migrations | Passed |
| Upgrade from the shared scalar output TPM head | Passed with the same migration verifier |
| Prisma generation | Passed |
| Ruff check for `src` and `tests`; changed Python format checks | Passed |
| UI unit tests | 304 passed |
| Collection and lane audit | 6,364 tests, each in one primary lane |
| Generated OpenAPI/config/provider references | Passed; OpenAPI regenerated |
| Documentation tests, health, strict build, and public containment | Passed |
| Production UI build | Passed; initial JS gzip reduced from 370.39 kB to 328.55 kB |
| Changed UI path lint | Passed |
| Full UI lint | Existing debt: 88 errors and 3 warnings; baseline was 116 errors and 3 warnings |

Disposable PostgreSQL 15 and Redis 7 services were used. The official SDK tests
used the repository's pinned requirements in a separate temporary environment.
The migration verifier created and removed its own databases. No production
service or data was used. The two task-owned containers and temporary UI
fixture server were removed or stopped after verification.

The real Redis tests cover all seven scopes, concurrent replica accounting,
organization sharing, model separation, limit edits without counter reset,
unknown usage, all four text entry points, and individual local batch calls.
Real PostgreSQL tests cover map storage, joined auth lookup, cache hits,
transaction rollback, outbox clears, tier clone, revision checks, and bulk
updates. Existing provider, retry, cancellation, cleanup, Realtime, permission,
and billing regressions passed in their normal lanes.

UI checks cover strict integer and map bounds, duplicate model IDs, explicit
clears, prototype-safe IDs, abort signals, mutation ownership, labels, keyboard
focus, and pending controls. DOM tests use narrow and wide viewport values.
The built tier nested route and model editor were inspected with fixture data:
Output TPM appeared beside RPM and TPM, in the grid, and in bulk controls. The
team detail editor loaded its saved scalar and model output limits. This smoke
check used local fixture responses; backend authorization was checked by tests.

## Dependency and capacity evidence

The [load summaries](output-tpm-model-load-summary.json) and
[raw archive](output-tpm-model-load-results.zip) retain every result. The archive
contains 28 files and passed its CRC check. The profile uses constant arrivals,
a fixed provider, fake auth/routing/billing, and real standalone Redis. The
first set sends 400 requests per case at 100 requests/second. The stream repeat
sends 800 requests per case at the same rate.

| Path | Redis commands for 400 requests |
| --- | --- |
| No output policy | 400 existing admission commands |
| Four or seven scopes, positive output | 800: one admission and one completion per request |
| Seven scopes, zero output or cache hits | 400 admission commands; no completion writes |
| Seven scopes, unknown output | 401: one admission per request and one unknown completion; later calls receive 503 |

Streams add no quota I/O per frame. There is no new inference SQL or Redis round
trip. All profiles completed their target work without dropped arrivals. The
seven-scope stream repeat used 1,600 commands for 800 completed requests and had
zero observed queue growth. The real integration test also uses eight limiter
instances against the same seven counters.

The eight-second local stream p95 values were 11.80 ms before, 11.95 ms after
with four scopes, and 12.84 ms after with seven scopes. Short profiles varied
with local build and test activity. These measurements verify call budgets and
completed work; they do not establish production latency or capacity. The
runner does not measure time to first token. Peak RSS includes the fixture and
imported application code; it is not a production memory bound.

Measured seven-scope Redis buckets averaged about 253 bytes and receipts about
392 bytes. Four-scope receipts averaged 360 bytes. Bucket retention remains
minute reset plus 30 seconds; receipts remain 120 seconds. At 100 positive
attempts/second, receipts retain about 12,000 records, or 4.7 MB at the observed
record size, plus buckets and operating headroom. The existing serialized
receipt ceiling remains 4 KiB and the Redis key ceiling remains 512 bytes.

The extension adds at most three counter families to one request. Only models
with configured limits create those counters. Each team/key map has at most
64 entries, 256 UTF-8 bytes per ID, and 32 KiB of stored JSON. Two maps can thus
add roughly 64 KiB of payload to an existing auth cache entry. Size shared
Redis for populated maps as well as output accounting; this does not replace
existing metadata/cache limits.

The [auth query plans](output-tpm-model-auth-query-plan.json) use 100
organizations, 1,000 teams, 1,000 runtime users, and 10,000 keys. Both maps have
64 IDs of 256 bytes. The lookup uses one joined SQL query on a cache miss and
zero SQL on a cache hit. The before and after plans retain indexed key lookup
and bounded joins. Recorded execution times were 0.106 ms and 0.081 ms; these
are local observations. The measured cache value was 36,042 bytes for the
fixture's plain IDs. No additional join, connection, or pool is introduced.

Deployment connection arithmetic is unchanged: for any declared replica,
process, worker, or surge count, the added PostgreSQL, Redis, HTTP, and worker
pool allocation is zero. Existing Helm capacity checks passed. Operators must
still qualify their own maximum traffic, model cardinality, and Redis memory
budget before production enablement.

## Rollout and rollback

Apply `202610070001_model_output_tpm_limits` after the scalar migration. Upgrade
or drain every old API and local batch-worker replica before enabling model
limits. New auth entries use version 6; invalidation clears versions 4, 5, and
6. Default null fields retain the existing scalar behavior.

Use shared standalone Redis, fail-closed coordination, and stored-key auth for
shared policies. Enforced tier output limits also require
`tier_policy_missing_service_mode: fail_closed`. Startup, reload, policy writes,
activation, and re-enable checks reject incompatible configuration. Missing or
stale enforced snapshots close admission. Realtime rejects a matching
model-only output policy. Provider-native async batches remain unsupported;
local batch disables aggregate execution when per-caller output must be
attributed.

Before rollback, clear new model policies, wait for auth invalidation or cache
expiry, and drain admitted work. Keep the additive columns. Scalar counter
identities and accounting fingerprints remain compatible. Model counters use
entity and callable identity, so a tier version or limit edit preserves usage.
No deployment or PR merge is included in this work.
