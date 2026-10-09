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
| Full hermetic lane, with loopback sockets | 4,216 passed; no skips |
| Full application lane | 1,613 passed |
| Full PostgreSQL lane, with Redis | 390 passed, including the pinned isolated SDK cases |
| Full Redis lane | 221 passed; no skips |
| Full Helm lane | 71 passed |
| Fresh, last-release, shared-feature, and model-identity recovery migrations | Passed |
| Upgrade from the shared scalar output TPM head | Passed with the same migration verifier |
| Prisma generation | Passed |
| Ruff check for `src` and `tests`; changed Python format checks | Passed |
| UI unit tests | 304 passed |
| Collection and lane audit | 6,511 tests, each in one primary lane |
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

Review fixes make startup, admin writes, and inference use the same tier-setting
resolver. Explicit config values take precedence over environment defaults.
Both tier modes bind at startup. Dynamic changes to their effective values
return `409 restart_required` before config persistence or related mutations.
Relevant coordination and authentication changes validate output policies
before saving. Peer reload rejection preserves the last valid runtime state.
The admin config API returns `400` for invalid updates. Tests check one policy read
for a relevant update and no policy read for unrelated updates. Inference adds
no SQL or Redis calls. The simulator uses the inferred deployment model type
and omits output TPM projections for non-text requests.

The review loop completed on 2026-10-08 and used three passes against the complete
PR. It reran the four backend lanes, lint, and documentation checks. UI, Helm,
migration recovery, and load evidence retain the prior qualification results;
this loop changes no UI or schema. Pass 1 fixed two P2
findings: startup-bound tier modes could be saved without changing the live
service, and compatible streams could lose complete usage before EOF or
`[DONE]`. Pass 2 fixed the same late-reporting issue between Anthropic's terminal
`message_delta` and `message_stop`. Pass 3 found no P0, P1, or P2 findings. Each
temporary fix plan was deleted after its fix and focused checks.

OpenAI-compatible and Anthropic adapters now retain complete raw output before
the next read. Later output or invalid evidence clears that count. Transport
failure and disconnect retain valid final evidence. Regressions cover zero
output, positive output, crossing the limit, later malformed events, and all
four text endpoints with real Redis. Quota accounting stays in the existing
finalizer, with one normal write per positive or unknown attempt and no quota
I/O per frame.

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

## Main merge verification (2026-10-08)

The branch merges `main` at `4facbbea0730ed64058b954b1dc47979f2c940f7`
(Console sign-in). Seven conflict files were resolved. The Console scope checks,
bounded primary fallback, atomic cache fill, and revocation owner remain in
place. Team directory queries include the scalar and model output fields.

The combined auth contract uses cache version 7. It ignores older allow
records, invalidates allow records in versions 4, 5, 6, and 7, and retains
revocation tombstones. Lookup and fill also check version 5 denials. Revocation
publishes version 5 and 7 tombstones atomically. This adds no Redis round trip:
a warm auth lookup remains one operation, and a cold lookup remains one joined
SQL read and one cache fill. Quota admission and completion budgets are unchanged.

| Gate | Result |
| --- | --- |
| Hermetic | 4,433 passed across the full lane and socket-dependent rerun |
| Application | 1,616 passed across the full lane and local HTTP/WebSocket reruns |
| PostgreSQL | 484 passed; one opt-in Console cardinality profile skipped |
| Redis | 230 passed |
| Helm | 80 passed |
| Collection and lane audit | 6,844 tests, each assigned to one primary lane |
| Migrations | Fresh, last release, shared feature, model-identity recovery, and upgrade from the new main passed |
| UI | 311 unit tests passed; production build and conflict-path lint passed |
| Full UI lint | Existing 88 errors and 3 warnings remain unchanged |
| Python | Prisma generation, full Ruff check, and changed cache/test format checks passed |
| Documentation | Generated references, health, strict build, and public containment passed |

The sandbox prevents local socket binding. The affected three hermetic and ten
application cases were rerun with loopback access and all passed. The database
and Redis checks used disposable services. Both task-owned containers were
removed after verification. The merged UI initial JavaScript bundle is
311.30 kB gzip.

## Dependency and capacity evidence

### Revocation fixes after the main merge review

The follow-up fixes both P1 findings on merge commit
`9b380b23e528bbe5a63f0be230e0d9094416360a`:

- A v7 allow now requires a live v5 allow with the same random guard. Atomic
  fill writes both entries with the same TTL. Missing, expired, changed, or
  pre-fix guards discard the v7 allow and use the bounded primary read. A v5
  revocation cannot leave usable v7 auth even if no request sees its tombstone
  before it expires. Output policy is read only from the primary or v7.
- Atomic revocation publishes v5/v7 tombstones and clears exact v4/v6 entries.
  The rollback command uses this owner and no longer sends a separate delete.
  Stop and drain old readers before reconciliation to prevent an old in-flight
  fill from restoring its own format. Exact deletion covers old 300-second
  entries without relying on their expiry.

Real Redis regression tests cover observed and unobserved three-second v5
tombstone expiry with a thirty-second v7 allow, guard loss and replacement,
malformed guards, pre-fix cache entries, delayed fills, first committed fills,
and raw legacy entries. Real PostgreSQL/Redis tests cover durable removal,
outbox recovery, and rollback preview, apply, and retry. An additional check
loads the actual v5 main and v6 parent readers: the v5 reader accepts the
compatibility entry, and both deleted-key scenarios deny after the fix. Its
source and result are included in the raw archive below.

| Follow-up gate | Result |
| --- | --- |
| Hermetic | 4,433 passed |
| Application | 1,616 passed, including local HTTP/WebSocket cases |
| PostgreSQL and cross-owner Redis | 485 passed; one opt-in Console cardinality profile skipped |
| Redis | 240 passed |
| Collection and lane audit | 6,855 tests, each assigned to one primary lane |
| Python | Full repository Ruff and all changed-file format checks passed |
| Documentation | Generated references, health, strict build, and public containment passed |
| Final fix review | No remaining P0, P1, or P2 finding in this fix |

The UI, Helm chart, schema, and migrations are unchanged by this follow-up;
their merge qualification above remains applicable. The temporary fix plan is
removed after implementation and verification. All task-owned test containers
are removed before the update is pushed.

The [auth load summary](output-tpm-revocation-load-summary.json) and
[raw samples](output-tpm-revocation-load-results.zip) retain the full
qualification and preliminary trials. The archive contains 36 files and
passes its CRC check. Reproduce the qualification with:

```sh
python -m tests.performance.key_auth_revocation_profile \
  --before 9b380b23e528bbe5a63f0be230e0d9094416360a \
  --redis-url "$DELTALLM_TEST_REDIS_URL" \
  --output-dir /tmp/output-tpm-auth-profile --rate 50 --duration 10
```

The runner uses real standalone Redis and a fixed in-process primary. Primary
call counts represent the joined SQL operation; timings do not include SQL or
a provider. It releases completed cases before the next arrival window. Each
case completes all 500 calls with zero dropped arrivals and zero sampled queue
growth. Both maps contain 64 IDs of 256 bytes in the populated cases.

| Auth path | Before / after Redis calls | Before / after primary calls | Before p50 / p95 / p99, ms | After p50 / p95 / p99, ms |
| --- | --- | --- | --- | --- |
| Warm, empty maps | 500 / 500 | 0 / 0 | 1.15 / 1.94 / 2.93 | 2.33 / 4.06 / 5.94 |
| Warm, populated maps | 500 / 500 | 0 / 0 | 4.97 / 6.50 / 7.26 | 3.46 / 6.19 / 7.53 |
| Cold, populated maps | 1,000 / 1,000 | 500 / 500 | 4.70 / 5.64 / 7.92 | 4.72 / 7.23 / 8.81 |

Local timing varies across cases and does not establish production capacity.
The extra v5 entry omits all new output policy fields. In the populated fixture
it uses 1,288 bytes beside the 35,523-byte v7 entry, rather than copying the two
large output maps. Other legacy auth fields and metadata still need memory
headroom; these fixture sizes are not a maximum.

Preliminary 500-RPS trials retain their failed samples. After releasing
completed cases, all four warm cases meet their call budgets at 500 RPS. The
pre-fix populated cold case exceeds the 100-ms Redis lookup budget and uses
bounded primary fallback: all 2,500 calls complete, but only 3,785 Redis calls
occur instead of 5,000. The after cold case did not run at that rate. Earlier
warm trials also have generator drops and a primary fallback. These trials do
not qualify cold capacity at 500 RPS. The 50-RPS run compares both versions at
the repository's qualification direction and retains every raw sample.

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
limits. New auth entries use version 7 with a matching v5 guard; invalidation
clears allow records in versions 4, 5, 6, and 7 and preserves tombstones.
Default null fields retain the existing scalar behavior.

Use shared standalone Redis, fail-closed coordination, and stored-key auth for
shared policies. Enforced tier output limits also require
`tier_policy_missing_service_mode: fail_closed`. Tier mode changes require a
deployment configuration change and restart. Startup, reload, policy writes,
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
