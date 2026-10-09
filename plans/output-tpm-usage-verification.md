# Output TPM usage enforcement: implementation and verification

Updated: 6 October 2026. Branch: `feature/output-tpm-limit`.
Main baseline: `f5ffd80dfb4c100a2063fb6a7939bd6f7ba68bd2`.
Pull request integration baseline: `b8a4ad6fde71b20e6db1d8cf25c980ff7b8ec8bd`.
Worktree: `deltallm-output-tpm`.

The [approved usage plan](output-tpm-usage-enforcement-plan.md) is implemented in this worktree. It replaces the reservation runtime in place. The four policy fields, additive migration, joined authentication query, invalidation, admin API, UI editors, and response-cache correction are retained. The [reservation report](output-tpm-verification.md) is historical baseline evidence.

## Implemented behavior

Redis checks recorded output at caller admission. A request admitted below every applicable limit can finish, including its bounded provider retries and MCP phases. Complete output from each actual provider attempt is recorded once. An attempt that crosses a limit still returns its normal answer. New requests receive the existing scope-specific 429 until reset.

All accounting uses the fixed UTC minute in which Redis first records the completed attempt. It does not measure tokens emitted during a minute or the last 60 seconds. Concurrent admitted calls can exceed the limit. Output caps keep their normal generation meaning; output TPM does not require or change them.

API keys, runtime users, teams, and organizations retain strict positive integer or null policies. Configured parent limits apply. The request captures verified scope identities once; retries and finalization add no policy SQL.

Chat Completions, Completions, Responses, and Messages use their shared text execution path, for JSON and supported streaming adapters. Generic OpenAI-compatible servers, custom base URLs, Azure, Groq, and vLLM no longer need endpoint qualification for this policy. Raw compatible aggregate completion usage is counted once, including multiple choices and reasoning when present in that aggregate. Groq top-level and metadata usage are supported; matching copies are counted once and conflicting copies are unknown. Cumulative stream usage is not summed.

Native Anthropic, Gemini, and Bedrock adapters supply raw output evidence before normalization. Gemini counts candidates and separately reported thoughts; omitted thoughts require consistent raw totals to establish zero thoughts. Native Gemini streaming remains unsupported by the existing adapter. This change does not add that capability.

When dispatched work ends without complete usage, final accounting marks all captured scopes unknown. The admitted call keeps its existing result. New calls at those scopes receive 503, `output_tpm_usage_unknown`, and Retry-After until the next UTC minute. One uncertain call can therefore pause a shared team or organization. Later known output does not clear that flag in the same minute. Explicit zero, response-cache hits, and failures before dispatch need no accounting write. An HTTP error alone does not prove zero generated output.

Local batch items share these counters and keep claim-epoch result fencing. Known output from a stale worker counts even when its result cannot commit. Native provider batches, aggregate microbatches, and Realtime remain guarded under an applicable policy. Selector classifier work and non-text output keep their existing controls.

## Ownership, failure, and dependency bounds

The request-local output context owns sequential attempt IDs, raw evidence, and final accounting. Duplicate cleanup is a no-op. Each actual upstream attempt gets a fresh server UUID. Providers report evidence and do not write quota state.

Admission validates output state before existing rate or parallel writes. It reads at most four output buckets in the existing transaction and creates no output bucket or receipt. The v2 namespace does not reinterpret old reservation state. Numeric counters saturate at 2,147,483,647 rather than at the configured limit.

Positive and unknown output use one final Lua operation across all scopes and a receipt. Matching receipt replay returns the original snapshot without another charge; conflicting reuse is rejected. Keys are bounded to 512 bytes, receipts to 4 KiB, receipt retention to 120 seconds, and bucket retention to reset plus 30 seconds. Coordination keeps its one-second bound and existing bounded NOSCRIPT recovery. There is no new client, pool, package dependency, queue, process role, config mode, or background task.

Final accounting failure preserves the answer, omits unknown remaining capacity, and records a warning and fixed-label metric. It does not retry provider work or change provider health. Redis admission fails closed. Lua execution prevents concurrent interleaving but cannot promise rollback after runtime or OOM failure. Process death, lost Redis state, or a write that cannot be recorded can lose output from this soft counter. Receipts provide deduplication while retained state exists; they are not a durable billing ledger.

Known JSON results use the final snapshot for headers; streams retain the admission snapshot because headers precede completion. Unknown remaining capacity is omitted. Existing billing and hard-budget accounting keep their owners.

## Code review fixes

The five review findings are fixed in the same worktree:

1. Final accounting runs in a request-owned cancellation shield with the existing one-second coordination bound. A stream disconnect can no longer cancel the write before it starts. Direct task cancellation permits cleanup to retry the same event ID; Redis receipts prevent a second charge. A cleanup timeout reports accounting failure and removes known remaining capacity.
2. Team and organization output-policy updates, including clears, queue invalidation in the existing outbox within the policy transaction. An enqueue failure returns `503` and rolls back the policy. Immediate invalidation has a half-second bound; the existing worker owns retries after failure or process interruption. No new queue or worker is added. Cached authentication can retain the previous policy until recovery or expiry; the feature docs now state this limit.
3. Messages streaming requests retain canonical usage for the Messages translator, including usage-only terminal frames. Normal Chat Completions usage suppression keeps its existing behavior.
4. JSON dispatch is marked by the shared provider hop after signing succeeds. Missing Bedrock credentials do not record unknown usage for work that was never sent.
5. Runtime-user editing aborts pending saves on unmount, entity change, or session change, and rejects late success and error results by request identity. A ref prevents duplicate submission. The parent reconciles by account and runtime-user identity and cannot reopen a closed modal or replace another account.

Regression tests exercise actual ASGI disconnect cancellation with real Redis, known and unknown next-call enforcement, bounded cleanup, direct cancellation and receipt identity, signing failure followed by recovery, Messages usage with and without a policy, transactional outbox rollback and worker recovery with PostgreSQL, and late UI responses below and above the responsive breakpoint.

The second code review found two additional accounting defects. Both are fixed:

1. JSON and streaming connection-pool timeouts report known zero output. No connection was acquired, so these failures create no output bucket or receipt and cannot pause shared scopes. Read and write timeouts retain unknown accounting. Bounded provider hops retain their existing sanitized transport-error contract.
2. Bedrock streams report complete raw usage after valid upstream completion and before the next downstream frame. A failed send cannot discard this evidence, including when the stream has no content. Missing metadata remains unknown, and explicit zero needs no accounting write.

New regression tests exercise HTTPX's actual pool-acquisition timeout without sending a provider request, ambiguous timeout handling, bounded-hop error parity, and Bedrock close timing. Real-Redis route tests prove zero writes after pool failure, subsequent shared-scope admission, one retained charge after a failed final-frame send, and numeric denial only when known output reaches the limit. The runtime fixes add no network, SQL, or Redis calls.

The third code review found three further defects. All three are fixed:

1. Key PUT, runtime-user PUT, and organization POST upsert now queue output-policy invalidation in the same transaction as the policy write. These paths reuse the existing outbox and bounded immediate attempt. They include clears, skip queue writes for omitted policies, return success after a post-commit cache failure, and roll back all transactional changes if enqueue fails.
2. Team and organization token discovery now uses the same team precedence as authentication: direct key team, runtime-user team, then service-account team. Scope SQL lives in the key repository. Each invalidation still uses one discovery query and bounded Redis delete batches; team invalidation does not join the team table. Both immediate invalidation and worker recovery remove current and legacy cache entries for affected keys while retaining unrelated entries.
3. Bedrock streaming retains valid raw output usage after a valid message stop when input or total usage fails validation. The response validation error remains. Explicit zero creates no output bucket or receipt. Missing, invalid, or out-of-range output remains unknown.

The fixes add no inference-path database, Redis, or network call. The added queue write runs only during admin policy mutation. PostgreSQL regressions cover setting and clearing all four scopes, failed immediate invalidation, enqueue rollback, and recovery through the real worker. Additional tests check service-account fallback and team precedence, the half-second immediate-invalidation bound, raw Bedrock evidence and preserved validation errors, and next-call admission or denial with real Redis.

The fourth code review found two more defects. Both are fixed:

1. Deployment defaults now respect both accepted Chat output-cap names. A caller's `max_tokens` or `max_completion_tokens` prevents the default for the other name from being added. JSON and streaming calls keep the caller's cap and unrelated defaults, with or without an output policy.
2. Bedrock streams retain final raw output when metadata arrives after a valid message stop, before response validation or the next upstream read. A later timeout or cancellation keeps the known count and the original transport error. Normal completion reports usage once. Missing metadata remains unknown, and explicit zero requires no output write.

The runtime changes add no database, Redis, or network call, parser, task, or lifecycle. New HTTPX transport tests cover both cap names, both response modes, timeout, direct cancellation, partial streams, one upstream close, and one final accounting attempt. Real-Redis route tests cover failure after metadata, zero output, below-limit output, overage, missing metadata, receipts, shared-scope state, and next-call admission or denial.

| Path | Measured or tested quota command budget |
| --- | --- |
| No output policy | Existing one admission EVAL; no added command or output state. |
| Governed positive output | One admission EVALSHA and one final accounting EVALSHA, independent of scope count. |
| Governed cache hit, explicit zero, or no dispatch | One admission; no output accounting or new receipt. |
| Dispatched unknown output | One admission and one final accounting; subsequent guarded calls need one denial command. |
| Retry or MCP phase already admitted | No new output admission; at most one final accounting command per actual attempt. |
| Stream chunk | Local evidence and existing parsing only; no output Redis or SQL call. |

## Regression gates after the first code review

After the first review fixes, collection contained 6,210 tests, each in exactly one primary dependency lane. PostgreSQL 15 and Redis 7 ran as isolated task-owned local services. The four affected Python lanes were rerun after those fixes. New real-dependency tests did not skip.

| Gate | Result |
| --- | --- |
| Hermetic lane | 4,021 passed. |
| Application lane | 1,590 passed on the full run. |
| Redis lane | 162 passed on the final full run. |
| PostgreSQL lane | 362 passed; four existing official Realtime SDK tests skipped because `DELTALLM_REALTIME_SDK_PYTHON` was unset. |
| Helm lane | 71 passed in the implementation run; separate lint and template checks passed for base, eval, and production profiles with CI test overrides. Review fixes do not change deployment files. |
| Python lint and format | Ruff check and format check passed for all 73 touched Python files. |
| Dependencies and generated client | Frozen development dependency setup and Prisma generation passed. |
| Database migrations | Deployment passed; fresh installation, upgrade from v0.1.48, and shared-feature path checks passed. No rewrite migration was added. |
| UI | 298 unit tests passed; TypeScript/Vite build passed. Changed helpers, API adapter, and regression tests have zero ESLint findings. |
| Full UI lint | Still fails with the recorded baseline: 116 errors and three warnings. New output helpers have no findings. |
| Configuration docs | Generator check passed for 323 fields. |
| Whitespace | `git diff --check` passed. |

An early review-fix run overlapped the UI build and had two asset setup errors while `ui/dist/assets` was replaced. After the build completed, all 115 focused checks passed, and the final full application run passed without errors. The first full Redis run had one failure in the existing selector server-clock lease-expiry test. Its focused rerun and the final full Redis run passed; the test and its timing bounds were not changed. Earlier implementation-stage application and PostgreSQL setup corrections remain in the retained logs. Local socket tests and dependency tests required execution outside the filesystem sandbox. The final gates used the permitted isolated services.

Real Redis tests use independent clients to verify concurrent recorded-only admission, overage followed by denial, ordinary/unified parity, denial without partial legacy acquisition, all-scope writes, receipt replay/conflicts, malformed state, minute reset, saturation after policy increases, unknown parent impact, NOSCRIPT, connection failure/recovery, lost acknowledgement, and TTL bounds. Route, provider, and batch tests cover the four APIs, raw evidence before failed translation, Groq/vLLM layouts, client usage suppression, cache/zero paths, cancellation, admitted retries/MCP, and stale-worker result fencing.

Commands followed the repository dependency-lane gates: frozen uv sync, Prisma generate/deploy, pytest collection and the five lane selections, migration-path verification, touched-path Ruff, configuration-reference check, UI unit/build/lint, Helm lint/template, and whitespace validation. Full logs are retained under `/private/tmp/deltallm-output-tpm-*.log`. Review-fix lane logs use `fixes-hermetic`, `fixes-app`, `fixes-redis-final`, and `fixes-postgres` suffixes; the focused result is in `fixes-focused-final`. Full UI lint still contains 14 existing findings in `RBACAccounts.tsx`; per-file finding counts match the implementation baseline.

### Second code review verification

Collection now contains 6,230 tests: 4,036 hermetic, 1,590 application, 366 PostgreSQL, 167 Redis, and 71 Helm. Every test has exactly one primary dependency lane. The second fixes add 20 regression cases. Eleven new cases failed against the saved pre-fix source and passed after the fixes.

| Gate | Result |
| --- | --- |
| Full hermetic lane | 4,036 passed. |
| Full application lane | 1,590 passed; existing deprecation warnings remain. |
| Full Redis lane | 167 passed against isolated Redis 7; two existing pub/sub-close deprecation warnings remain. |
| Focused provider and structure checks | 108 passed after the final helper extraction. |
| New real-Redis failure regressions | Five passed: JSON/stream pool timeout and three Bedrock final-frame disconnect cases. |
| Python lint and format | Ruff check and format check passed for all 74 changed Python files. |
| Whitespace | `git diff --check` passed. |

The first hermetic run caught the provider-hop function-size limit. Transport-error handling was extracted into a small typed helper without relaxing the guard. The first disconnect harness blocked the outer send; it now uses the existing failed-send pattern with `OSError`. The final checks passed. These fixes do not change database, UI, dependency, or deployment contracts; their earlier checks remain recorded above.

Actual commands used the existing environment: `.venv/bin/pytest -q -m hermetic --durations=10`, `.venv/bin/pytest -q -m app --durations=10`, and `.venv/bin/pytest -q -m redis --durations=10`. Both Redis URL variables pointed to the isolated local service. Collection used `--collect-only -qq --dependency-lane-report`. Ruff used `check` and `format --check` over all changed Python paths. Logs use the `/private/tmp/deltallm-output-tpm-second-fixes-` prefix; final hermetic and Redis logs have `hermetic-final` and `redis-final` suffixes.

### Third code review verification

Collection contains 6,278 tests: 4,062 hermetic, 1,596 application, 376 PostgreSQL, 173 Redis, and 71 Helm. Every test has exactly one primary lane. These fixes add 48 regression cases. All 32 cases selected to reproduce the three defects failed against the saved pre-fix source and passed with the fixes.

| Gate | Result |
| --- | --- |
| Focused provider, admin, and key-service checks | 135 passed. |
| Focused PostgreSQL and Redis regressions | 18 passed. |
| Full hermetic lane | 4,062 passed. |
| Full application lane | 1,596 passed; 147 existing deprecation warnings remain. |
| Full PostgreSQL lane | 372 passed; four existing official SDK tests skipped because `DELTALLM_REALTIME_SDK_PYTHON` was unset. |
| Full Redis lane | 173 passed; two existing pub/sub-close deprecation warnings remain. |
| Python lint and format | Ruff check and format check passed for all 75 changed Python files. |
| Existing migrations | Deployed successfully to the fresh isolated PostgreSQL 15 database. No schema change is needed for these fixes. |
| Whitespace | `git diff --check` passed. |

The first focused checks exposed test-harness errors: the fake organization upsert replaced existing output policy, and assertions expected an error code or SSE error frame that the existing provider contract does not emit. The fake now preserves output policy during upsert, and tests verify the existing sanitized exception and incomplete stream. The first saved-source run lacked the copied `src/ui` module; its final run includes that module and reports all 32 expected behavior failures. The first hermetic run skipped three local-socket checks inside the sandbox; its permitted full rerun passed all tests without skips. Production guards and existing error behavior were preserved.

Commands used the same dependency lane selections as above, with `-rs` on real-dependency and final hermetic runs. PostgreSQL used the isolated local database and Redis database zero; the Redis lane used database one to isolate concurrent checks. Logs use the `/private/tmp/deltallm-output-tpm-third-fixes-` prefix. The fixes do not change UI, dependencies, configuration, migrations, or deployment files; their earlier verification remains recorded above.

### Fourth code review verification

Collection contains 6,306 tests: 4,086 hermetic, 1,596 application, 376 PostgreSQL, 177 Redis, and 71 Helm. Every test has exactly one primary lane. These fixes add 28 regression cases. Twelve new HTTPX cases failed before the runtime edits. Three new real-Redis cases failed against the saved pre-fix source. All 15 cases pass with the fixes. The original review's 14 temporary reproduction and control cases also pass.

| Gate | Result |
| --- | --- |
| Focused provider, output-contract, and default-identity checks | 195 passed. |
| New real-Redis route regressions | Four passed. |
| Full hermetic lane | 4,086 passed. |
| Full application lane | 1,596 passed; 147 existing deprecation warnings remain. |
| Full Redis lane | 177 passed; two existing pub/sub-close deprecation warnings remain. |
| Python lint and format | Ruff check and format check passed for all 76 changed Python files. |
| Whitespace | `git diff --check` passed. |

Commands were `.venv/bin/pytest -q tests/providers/test_output_transport.py tests/providers/test_output_usage.py tests/test_output_tpm_contracts.py tests/test_routing_identity_dependencies.py`, the full `hermetic`, `app`, and `redis` selections with `--durations=10 -rs`, and collection with `--collect-only -qq --dependency-lane-report`. Both Redis URL variables pointed to the isolated Redis 7 service. Saved-source regressions used database one; final Redis checks used database zero. Ruff ran `check` and `format --check` over every changed Python path. Logs use the `/private/tmp/deltallm-output-tpm-fourth-fixes-` prefix. PostgreSQL, UI, schema, configuration, dependency, and deployment contracts are unchanged by these two fixes; their earlier gates remain recorded above.

### Pull request preparation against main

The feature branch was advanced to the current `origin/main` commit before PR creation. Main adds model-identity migration recovery in separate files. The feature changes were preserved. The following checks ran on the combined branch with isolated PostgreSQL 15 and Redis 7 services:

| Gate | Result |
| --- | --- |
| Full hermetic lane | 4,089 passed; no skips. |
| Full PostgreSQL lane | 372 passed; four existing official Realtime SDK tests skipped because `DELTALLM_REALTIME_SDK_PYTHON` was unset. Twenty-two existing deprecation warnings remain. |
| Focused bootstrap, migration, provider, and output-contract checks | 189 passed. |
| Migration deployment | All migrations, including the output-policy migration, applied successfully to the fresh test database. |
| Migration paths | Fresh install, upgrade from v0.1.48, shared-feature upgrade, and main's new model-identity recovery path passed. |
| Test classification | 6,309 tests in exactly one primary lane: 4,089 hermetic, 1,596 application, 376 PostgreSQL, 177 Redis, and 71 Helm. |
| Performance artifacts | All 68 profile rows are retained. The 150-file raw archive passes its CRC check. |

Commands were `.venv/bin/pytest -q -m hermetic --durations=10 -rs`, `.venv/bin/pytest -q -m postgres --durations=10 -rs`, `.venv/bin/pytest -q tests/bootstrap/test_prisma_bootstrap.py tests/test_migration_verifier.py tests/test_batch_selector_profile.py tests/providers/test_output_transport.py tests/providers/test_output_usage.py tests/test_output_tpm_contracts.py`, and `.venv/bin/pytest --collect-only -qq --dependency-lane-report`. Migration commands were `.venv/bin/prisma migrate deploy --schema=./prisma/schema.prisma` and `.venv/bin/python -m scripts.verify_migration_paths --admin-database-url <isolated-admin-database-url> --base-ref v0.1.48 --prisma <worktree>/.venv/bin/prisma`. The worktree's `.venv/bin` was on `PATH` for recovery subprocesses. Test database and Redis URLs pointed only to the task-owned services.

Logs use the `/private/tmp/deltallm-output-tpm-pr-` prefix. Main does not change the feature's application, Redis, UI, configuration, or deployment files; the earlier full gates for those surfaces remain recorded above. The two PR-check containers and their temporary volumes were removed after verification.

### CI repair

The first PR run found three gaps. Its Python 3.11 hermetic lane had two stream-cleanup failures, the Redis lane had four related failures, and the application lane had one RPM assertion failure. The documentation job stopped because the generated public OpenAPI file was stale. PostgreSQL, migration paths, Helm, UI build, and Python lint passed in that run.

All six stream failures reproduced with the frozen dependencies in an isolated Python 3.11 environment. In that Python version, `asyncio.wait_for` starts a separate task. AnyIO disconnect cancellation could stop the waiting response task before the stream's shielded finalizer finished. Outer cleanup then tried to close a running generator or interrupted accounting and caused an idempotent retry. `RequestDeadline.wait_for` now uses `asyncio.timeout` and awaits work in the calling task. This preserves one cleanup owner and the existing timeout error. Exhausted per-attempt limits still reject work before it starts; two regression cases check this boundary.

The audio RPM assertion reproduced when a controlled clock moved between the two requests. Both calls are valid when they fall in different fixed minutes. The test now controls only the limit counter's clock, retains the same-minute denial assertions, and also checks admission and denial after the next-minute reset. Production counter behavior is unchanged.

The public OpenAPI artifact was regenerated with the existing exporter. It now includes the new generation-cap and output-policy fields. The previous PR statement that no checked-in OpenAPI artifact needed regeneration was incorrect.

| Repair gate | Result |
| --- | --- |
| Full Python 3.11 hermetic lane | 4,091 passed; no skips. |
| Full Python 3.11 Redis lane | 177 passed; two existing deprecation warnings remain. |
| Python 3.11 cleanup, accounting, deadline, and RPM focus | 52 passed, including the six reproduced CI failures. |
| Python 3.11 streaming billing, MCP, and rate-limit focus | 47 passed; two existing deprecation warnings remain. |
| Python 3.12 cleanup, deadline, and accounting focus | 46 passed. |
| Generated OpenAPI, config, and provider references | All current; OpenAPI has 234 paths and 303 operations. |
| Documentation health and tests | Health check passed; nine tests passed. |
| Strict documentation build and public containment | Both passed. |
| Changed-path Ruff and whitespace | Check, format check, and `git diff --check` passed. |

The isolated Python 3.11 interpreter is `/private/tmp/deltallm-output-tpm-ci311/bin/python`. Its matching `pytest` ran `-q -m hermetic --durations=25 --durations-min=0.5 -rs` and `-q -m redis -rs --durations=25 --durations-min=0.5`. Both Redis URL variables pointed to the task-owned service. Focus commands selected `tests/test_stream_response.py`, `tests/test_request_deadline.py`, `tests/test_output_tpm_contracts.py`, the audio RPM case, and the two failing Redis test functions. The application focus selected `tests/test_stream_accounting_commit.py tests/mcp/test_chat_execution.py tests/test_rate_limit.py`. The Python 3.12 focus used `.venv/bin/pytest -q tests/test_stream_response.py tests/test_request_deadline.py tests/test_output_tpm_contracts.py`.

Documentation checks followed every command in `.github/workflows/docs.yml`, using the isolated interpreter and a temporary site directory. Generation used `.venv/bin/python scripts/docs/export_openapi.py`; all three reference exporters then passed `--check`. Ruff checked and format-checked `src/router/execution.py tests/test_rate_limit.py tests/test_request_deadline.py`.

Logs use the `/private/tmp/deltallm-output-tpm-ci` prefix. The task-owned Redis container and its volume were removed. The PR workflow supplies the full application, PostgreSQL, and Helm gates for this repair. The repairs add no SQL, Redis, or network call, queue, pool, or background task. The existing output accounting command bounds remain.

## Local performance evidence

The existing [output profile](../tests/performance/output_tpm_profile.py) and [constant-arrival generator](../scripts/measure_gateway_load.py) were reused. The primary comparison below used a fresh export of the main baseline and the implementation before review fixes, the same fixed provider fixtures, real standalone Redis with no eviction, and 100 offered requests/second for eight seconds per case. Each case started and completed 800 requests with zero generator drops. That comparison ran after the broad test lanes to avoid their load.

Machine-readable results are in [output-tpm-usage-profile.json](output-tpm-usage-profile.json). The [raw result archive](output-tpm-usage-load-results.zip) retains the earlier comparisons and the later review comparisons below. At the second-review stage, the archive contained 86 files and the JSON contained 36 summary rows. Later sections record the additional results. The first table uses the isolated replacement rerun and fresh main baseline.

| Case | Commands/request | End-to-end p95 / p99, ms | Max observed in flight | In-flight slope/second |
| --- | --- | --- | --- | --- |
| Main, no output policy | 1 | 7.53 / 68.58 | 12 | 0 |
| Replacement, no output policy | 1 | 7.38 / 10.76 | 3 | 0 |
| One scope, JSON | 2 | 12.74 / 51.13 | 10 | 0 |
| Four scopes, JSON | 2 | 12.54 / 56.50 | 11 | 0 |
| Four scopes, stream | 2 | 11.02 / 42.55 | 11 | 0 |
| Four scopes, unknown guard | 1 | 4.79 / 8.42 | 3 | 0 |
| Four scopes, cache | 1 | 4.47 / 6.90 | 2 | 0 |
| Four scopes, explicit zero | 1 | 37.39 / 79.47 | 41 | 0.0357 |

The unknown case first records one unknown warmup attempt with two commands, then returns 800 expected 503 responses and makes no further provider calls. The cache case makes no steady-state provider calls. Null policy keeps the existing EVAL path. Warmup SCRIPT LOAD calls are reported separately in the JSON.

The explicit-zero case had generator scheduling lag of 285.58 ms at p99 and 364.37 ms maximum. Its end-to-end maximum was 406.33 ms. One sampled in-flight request at second five produced the small positive fitted slope; subsequent samples returned to zero and the run drained. These short, noisy tails do not support a latency speedup claim. All cases completed their offered work without sustained accumulation in the sampled interval.

Measured Redis MEMORY USAGE was 360 bytes per receipt and 248 bytes per bucket for these fixtures. At 100 final events/second and 120-second receipt retention, 12,000 such receipts require about 4.12 MiB before additional operating headroom. The 4 KiB serialization ceiling gives a separate 46.9 MiB sizing bound at that rate, before keys, allocator overhead, and buckets. Small sampled receipts do not replace the configured bound or a deployment capacity budget.

The retained [reservation profile](output-tpm-profile.json) is useful historical evidence: cache requests needed two coordination commands and now need one; positive-output requests still need two. Its timestamps differ, so its latency is not a controlled comparison. The [joined authentication query plan](output-tpm-auth-query-plan.json) remains applicable because the four-field authentication projection is unchanged. The final UI initial bundle is 370.39 kB gzip, up 0.22 kB from before review fixes and 7.15 kB below the recorded main baseline of 377.54 kB.

### Review-fix performance check

After all affected test lanes and the final UI build finished, the unchanged main export and all seven feature cases ran again at 100 offered requests/second for eight seconds. Each case completed 800 requests with zero generator drops: 6,400 requests total. The profile asserts exact Redis command counts. Results use `main-after-review` and `review-fixes` labels in the same JSON and raw archive.

| Case | Commands/request | End-to-end p95 / p99, ms | Max observed in flight | In-flight slope/second |
| --- | --- | --- | --- | --- |
| Main, no output policy | 1 | 5.55 / 6.52 | 2 | 0 |
| Review fixes, no output policy | 1 | 5.79 / 14.28 | 3 | 0 |
| One scope, JSON | 2 | 7.30 / 9.62 | 4 | 0 |
| Four scopes, JSON | 2 | 8.52 / 23.33 | 7 | 0 |
| Four scopes, stream | 2 | 8.24 / 15.06 | 4 | -0.0714 |
| Four scopes, unknown guard | 1 | 4.92 / 8.03 | 2 | 0 |
| Four scopes, cache | 1 | 4.19 / 6.05 | 3 | 0 |
| Four scopes, explicit zero | 1 | 5.56 / 6.87 | 2 | 0 |

The fixes preserve the existing quota call budgets and add no per-chunk coordination. All cases drained without sustained sampled accumulation. These short local samples do not establish a production latency improvement. The unknown case returns the expected guarded `503` responses after its unknown warmup; it does not call the provider again. Policy-invalidation writes occur only on the control plane and are outside this request profile.

### Second code review performance check

After the affected test lanes finished, all seven cases ran against the saved source immediately before the two fixes and then against the final source. Both used the same fixtures, Redis service, and 100 offered requests/second for eight seconds per case. Labels are `second-review-before` and `second-review-after`. Exact quota command assertions passed in every case.

| Case | Commands/request, both | Before p95 / p99, ms | After p95 / p99, ms |
| --- | --- | --- | --- |
| No output policy | 1 | 7.73 / 23.76 | 156.07 / 457.42 |
| One scope, JSON | 2 | 9.48 / 14.92 | 75.83 / 195.19 |
| Four scopes, JSON | 2 | 16.21 / 179.35 | 13.94 / 28.57 |
| Four scopes, stream | 2 | 11.42 / 30.38 | 12.40 / 42.45 |
| Four scopes, unknown guard | 1 | 5.05 / 6.42 | 5.76 / 9.24 |
| Four scopes, cache | 1 | 8.57 / 24.98 | 37.85 / 54.55 |
| Four scopes, explicit zero | 1 | 7.21 / 11.50 | 7.49 / 13.39 |

The first comparison had large scheduling delays. After-fix scheduling-lag p99 was 116.18 ms with no policy, 204.37 ms for one-scope JSON, and 352.91 ms for cache. Maximum observed in-flight work was 53. Some short fitted slopes were positive on both sides; every case's final one-second sample returned to zero and every run drained. These measurements do not establish the cause of the delays or a latency regression.

The three noisy cases were repeated in reverse order: final source first, saved source second. Labels are `second-review-after-repeat` and `second-review-before-repeat`. Both the first comparison and the repeat are retained.

| Case | Before repeat p95 / p99, ms | After repeat p95 / p99, ms | Before / after CPU seconds |
| --- | --- | --- | --- |
| No output policy | 7.21 / 12.71 | 11.24 / 67.15 | 2.713 / 2.730 |
| One scope, JSON | 9.10 / 83.06 | 13.50 / 35.41 | 2.975 / 2.987 |
| Four scopes, cache | 6.43 / 9.65 | 6.14 / 11.51 | 1.857 / 1.844 |

The repeat reduced scheduling-lag p99 to at most 7.76 ms, but latency tails still varied. This is insufficient evidence for a latency speedup or a reliable latency comparison. The verified result is unchanged quota command counts and no additional I/O in the two fixes. All 20 second-review runs completed 800 requests each with zero generator drops: 16,000 completed requests, including the expected guarded 503 responses in the two unknown cases. Redis memory remained 360 bytes per sampled receipt and 248 bytes per sampled bucket.

The profile command was `.venv/bin/python -m tests.performance.output_tpm_profile --redis-url <isolated-redis-url> --label <label> --rate 100 --seconds 8 --output-dir <results-directory>`. The repeat added `--cases null one-json four-cache`. The saved-source runs used the same interpreter with the saved source as their working directory. Performance logs use `second-fixes-performance-before`, `second-fixes-performance-after`, and their `-repeat` suffixes.

### Third code review performance check

The same seven cases ran before and after the third fixes at 100 requests per second for four seconds each. Labels are `third-review-before` and `third-review-after`. Every measured request completed, including expected unknown-usage denials, with no generator drops. Redis command counts matched in every pair:

| Case | Quota commands per measured request | Before p95 / p99, ms | After p95 / p99, ms |
| --- | --- | --- | --- |
| No output policy | 1 | 6.92 / 11.11 | 6.34 / 8.23 |
| One scope, JSON | 2 | 11.32 / 15.74 | 12.31 / 66.35 |
| Four scopes, JSON | 2 | 10.93 / 16.00 | 8.74 / 12.03 |
| Four scopes, stream | 2 | 11.87 / 13.96 | 9.07 / 11.70 |
| Four scopes, unknown guard | 1 | 7.83 / 16.44 | 6.09 / 7.37 |
| Four scopes, cache | 1 | 8.50 / 17.42 | 7.28 / 16.98 |
| Four scopes, explicit zero | 1 | 10.32 / 22.54 | 8.04 / 20.83 |

The first one-scope after sample had 20.76 ms scheduling-lag p99 and a maximum of 12 in-flight requests. That case was repeated in reverse order under `third-review-after-repeat` and `third-review-before-repeat`. Before-repeat p95 / p99 was 10.57 / 15.23 ms; after-repeat was 9.14 / 13.18 ms. Scheduling-lag p99 fell below one millisecond in both repeats. All runs drained; each final in-flight sample was zero. Other test lanes ran during these local samples, so these results establish command parity rather than a latency improvement or production SLO.

All 16 third-review runs completed 400 requests each: 6,400 completions with no generator drops. Warmup and measured command counts retain the existing budgets. Sampled receipts and buckets retained their 360-byte and 248-byte sizes. PostgreSQL tests confirm one discovery query per shared-scope invalidation and execute its query plan; the inference authentication query remains unchanged.

The command used `--rate 100 --seconds 4`; the repeat selected `--cases one-json`. Performance logs use the third-fixes prefix with `performance-before`, `performance-after`, and their `-repeat` suffixes. The retained profile JSON now has 52 summary rows, and the load-results ZIP has 118 files with a verified CRC check. All earlier measurements remain in these artifacts.

### Fourth code review performance check

After the affected test lanes finished, the same seven fixed-provider cases ran against the saved source before the two fixes and then against the final source. Each case ran at 100 offered requests per second for four seconds. Labels are `fourth-review-before` and `fourth-review-after`. Every case completed 400 requests with no generator drops and the same quota command counts:

| Case | Quota commands per measured request | Before p95 / p99, ms | After p95 / p99, ms |
| --- | --- | --- | --- |
| No output policy | 1 | 6.71 / 11.29 | 6.12 / 8.98 |
| One scope, JSON | 2 | 6.72 / 8.38 | 8.71 / 11.16 |
| Four scopes, JSON | 2 | 15.09 / 25.01 | 55.78 / 69.86 |
| Four scopes, stream | 2 | 9.09 / 12.68 | 9.43 / 17.47 |
| Four scopes, unknown guard | 1 | 6.07 / 9.13 | 4.40 / 9.26 |
| Four scopes, cache | 1 | 6.30 / 13.93 | 5.54 / 7.70 |
| Four scopes, explicit zero | 1 | 6.07 / 8.98 | 5.43 / 6.80 |

The first after-fix four-scope JSON sample had 198.68 ms scheduling-lag p99 and a maximum of 29 in-flight requests. That case was repeated in reverse order under `fourth-review-after-repeat` and `fourth-review-before-repeat`. After-repeat p95 / p99 was 11.58 / 16.22 ms; before-repeat was 8.66 / 11.87 ms. Scheduling-lag p99 fell to 1.13 ms after the fixes and 0.73 ms before them. The short samples establish command-count parity and drained queues; they do not establish a latency speedup or the cause of the first spike. The unknown cases retain the expected guarded `503` responses.

All 16 fourth-review runs completed 400 requests each: 6,400 completions, with no generator drops. Every final in-flight sample was zero. The same one-admission and at-most-one-accounting budgets remain. The new native Bedrock failure regressions separately prove one final accounting attempt for known positive or unknown output, no accounting for zero, and no new provider call after policy denial.

The profile command was `.venv/bin/python -m tests.performance.output_tpm_profile --redis-url <isolated-redis-url> --label <label> --rate 100 --seconds 4 --output-dir <results-directory>`. The repeat added `--cases four-json`. Performance logs use the fourth-fixes prefix with `performance-before`, `performance-after`, and their `-repeat` suffixes. All earlier results remain. The retained profile JSON now contains 68 rows; the raw archive contains 150 files and passes its CRC check.

This ASGI profile does not measure first-token latency, pool wait separately, or isolated server Lua time. Redis command elapsed time includes connection/pool/network/script work. Provider fixtures and fake auth/routing/billing do not certify live-provider behavior, production cardinalities, deployment-wide pool capacity, or a sustained production SLO. Shared-scope correctness, long-call minute attribution, retries, and MCP command bounds have deterministic and real-Redis test coverage; they were not separately profiled under production load. Use deployment measurements to size shared-parent contention and headroom before broad activation.

## Activation and rollback

Apply the retained additive migration first. Upgrade or drain all gateway and local batch-worker replicas before enabling policies. Use protected standalone Redis, sufficient memory, `noeviction`, and `fail_closed`. Stored-key authentication remains required for shared policy hydration; existing master-key exemptions remain.

Drain any reservation-version attempts before enabling v2 accounting. Do not combine reservation and completion counters or mix versions with active policies. Start with one test key; verify crossing/next-call denial, reset, supported provider usage, missing-usage behavior, streaming, cache, retries/MCP, and local batch before adding shared scopes. Clear policies and drain attempts before rollback, keeping the additive schema. After a state-losing Redis restart/restore/failover, pause governed ingress until the next UTC minute.

The complete implementation and review fixes are on `feature/output-tpm-limit`. All task-owned review and PR-check Redis and PostgreSQL containers and their temporary volumes were removed after verification. No deployment was performed.
