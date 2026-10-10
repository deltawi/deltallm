# Output TPM usage enforcement implementation plan

Prepared: 5 October 2026. Status: implemented in this worktree; see the [implementation and verification report](output-tpm-usage-verification.md).
Branch: `feature/output-tpm-limit`. Worktree: `deltallm-output-tpm`.
Main baseline: `f5ffd80dfb4c100a2063fb6a7939bd6f7ba68bd2`.

Replace maximum-output reservations with a soft output rate limit. Check recorded output before a caller request. Let an admitted request finish. Record complete provider output after each model attempt. Refuse new requests when a configured scope is exhausted.

Build this change in the current worktree. Keep the database, authentication, admin API, UI, and cache correctness work. Replace the reservation model in place and remove its obsolete code. There will be one output enforcement model and no new mode setting.

The [previous plan](output-tpm-implementation-plan.md) and [reservation verification report](output-tpm-verification.md) describe the replaced reservation implementation. Their runtime results are historical baseline evidence only. The new report records the completion accounting results and measurement limits.

## Design decision

The output policy is a usage throttle. It is not a hard spending budget or a guaranteed ceiling on tokens generated in any 60 seconds. Existing billing, hard budgets, provider limits, concurrency controls, request deadlines, and retry bounds retain their separate roles.

Choose response accounting instead of reserving a maximum because the user wants admitted calls to finish and wants general provider support. This removes the mandatory output cap and endpoint qualification requirements. It also accepts output from calls that were already admitted when another call exhausted a scope.

A reservation implementation would provide a stronger admission bound but would still require an enforceable cap. An estimated reservation would add a tuning policy and could still exceed the limit. Token coordination during a stream would add latency and would need provider token evidence that a gateway does not always receive. Neither is part of this release.

These choices follow the [repository engineering rules](../RULES.md). A soft rate limit does not replace the atomic reservation required for a hard economic budget.

## External contracts checked

These primary sources were checked on 5 October 2026:

| Source | Documented behavior | Use in this plan |
| --- | --- | --- |
| [Kong AI rate limiting](https://developer.konghq.com/plugins/ai-rate-limiting-advanced/#known-limitations-of-ai-rate-limiting-advanced) | A response completes; its token cost affects the next request. Completion tokens can be a separate count. | This is the closest gateway model for the selected behavior. |
| [LiteLLM caller TPM](https://docs.litellm.ai/docs/proxy/users#estimated-output-tokens-requests-without-max_tokens) | Its general TPM limiter reserves a cap or estimate, then adjusts to actual usage. | This is the alternative for stronger control of concurrent work. |
| [LiteLLM separate OTPM](https://docs.litellm.ai/docs/proxy/io_token_rate_limits) | Its beta feature checks current OTPM plus the output cap before a call and records actual output afterward. | Do not describe all LiteLLM limits as simple post-response counting. |
| [Claude rate limits](https://platform.claude.com/docs/en/api/rate-limits) | Output TPM counts tokens during generation. The requested maximum does not enter the OTPM calculation. | Provider generation limits differ from a gateway completion counter. |
| [Groq streaming types](https://github.com/groq/groq-python/blob/main/src/groq/types/chat/chat_completion_chunk.py) | Stream usage can appear at the top level or in Groq metadata. | Cover both supported layouts in the provider adapter. |
| [vLLM stream usage](https://docs.vllm.ai/en/latest/features/per_request_metrics/) | Its final usage chunk needs usage reporting enabled. Token usage remains available for multiple choices. | Reuse the existing request for final usage; count the aggregate once. |
| [Gemini usage metadata](https://ai.google.dev/api/generate-content#UsageMetadata) | Response candidates and thoughts have separate token fields. | Interpret those fields in the native adapter without counting input tokens. |

These sources support different policies. The choice for DeltaLLM is a product decision, not a claim that all gateways use one standard.

## Public behavior

Keep `output_tpm_limit` on API keys, runtime users, teams, and organizations. Its type remains a strict integer from 1 through 2,147,483,647, or null. Omission preserves a value; null clears it. All configured ancestors apply. A child cannot remove a parent limit.

At admission, allow a new text request only when each applicable scope has known recorded usage below its limit. Do not add the request cap or an estimate to that usage. A total equal to the limit is exhausted.

After each provider attempt, add its complete output count to all scopes captured for the caller request. Count the provider aggregate once, including reasoning, tools, refusals, and choices when they are part of that output aggregate. Do not add reasoning detail fields again when the aggregate already includes them. Input tokens and cached input tokens are excluded.

Crossing the limit does not change a successful admitted response into a rate-limit error. It does not cut a stream or shorten generation. The next caller request receives a scope-specific 429 until the window resets.

Example: a scope has a limit of 1,000 and recorded usage of 900. A request produces 300 tokens. It completes successfully, records 1,200, and subsequent requests receive 429 for that minute.

Concurrency example: ten calls can all be admitted at 900 before any completes. If each produces 200 tokens, all finish and the recorded total reaches 2,900. Existing concurrency limits reduce this exposure; they do not make output TPM a hard ceiling. Without bounded per-call output, concurrency alone cannot give a finite token-overage guarantee.

Output caps remain normal generation parameters. Keep their existing validation, translation, and cache identity. Output TPM does not require a cap, inject one, change it to fit remaining capacity, or add special choice-count restrictions.

## Window and time

Use fixed UTC minutes selected by Redis TIME. Do not introduce a rolling log or a token bucket in this change.

A final accounting operation belongs to the minute in which Redis first records it. A call admitted at 12:00:59 that completes and records usage at 12:01:10 charges the 12:01 minute. This prevents a long call from adding tokens only to a window that has already expired.

Each provider attempt is recorded when it completes. Separate attempts or MCP phases can belong to different minutes. All output from one attempt is assigned to its accounting minute, even if generation crossed several minutes.

A duplicate accounting operation returns its original receipt. It cannot move the charge to another minute. A delayed attempt completion is a new accounting operation, not a refund against its admission minute.

Document this as a completion accounting window. It is neither the last 60 seconds nor a measurement of tokens emitted in each minute. Boundary bursts and delayed accounting are accepted properties of this soft control.

## Request and attempt ownership

Check output policy once during final admission of a caller request, after mutation and final model authorization. Reuse the current atomic admission call.

An admitted request can finish its existing bounded provider retries and MCP model phases. Output TPM does not perform new admission checks between those phases. Continue to enforce the existing routing, budget, tool, concurrency, and deadline rules.

Each actual provider attempt has a fresh server-owned event ID. The same ID is used for every accounting call for that attempt. A provider retry that performs more upstream work gets a new ID. Repeating an accounting callback does not get a new ID.

Capture the verified key, user, team, and organization scope identities at caller admission. Use that snapshot for the request's output. Do not accept quota identities or idempotency IDs from client metadata. Do not add policy SQL to retries or finalization.

Use one request-local output context in the current lease lifecycle. It starts and closes individual attempt records and owns final accounting. Provider adapters supply token evidence; they do not update Redis.

## Missing usage and failures

Choose a conservative fallback without inventing token counts: when dispatched work ends without complete output evidence, mark the captured scopes as having unknown usage for the accounting minute. New requests at those scopes receive 503 with `output_tpm_usage_unknown` and Retry-After until the next minute. Already admitted requests can still finish.

This can pause a shared team or organization after one uncertain call. It is an explicit availability tradeoff to prevent missing usage and cancellation from being treated as free output. It is not a statement that the quota was numerically exceeded.

A successful provider response with usable content is still delivered when output usage is missing. Keep existing malformed-response validation; do not make an invalid response successful merely to collect usage.

| Outcome | Output accounting | Caller and future requests |
| --- | --- | --- |
| Complete provider output count, including explicit zero | Record a positive count once; zero needs no counter write. | Return the normal response. Later requests use recorded usage. |
| Response-cache hit | Known zero generated output; no output accounting write. | Preserve cache behavior and billing. Admission still occurs before lookup. |
| Failure before dispatch | Known zero; no output accounting write. | Preserve the existing error and failover rules. |
| Provider proves rejection before generation | Known zero using an adapter-owned, tested contract. | Preserve the existing provider error. Do not infer zero from every HTTP error status. |
| Complete usage received, followed by translation or delivery failure | Record the known provider count once. | Preserve the transport/validation error. Generated work still counts. |
| Partial stream, missing final usage, ambiguous timeout, or cancellation after dispatch | Mark usage unknown once. | Preserve the admitted call's existing result/error; refuse new scoped calls for that minute. |
| Redis unavailable or corrupt state during admission | No provider dispatch. | Return local 503; do not change provider health. |
| Final accounting cannot be recorded | Preserve the response; record accounting failure and unknown remaining capacity locally. | Redis outage still closes new admission while it lasts. Some output can remain unrecorded after recovery; report this limitation. |
| Process dies before final accounting | No receipt can be assumed. | Known output already recorded remains. Unobserved output can be lost from this soft counter. |

Reuse the current bounded cancellation cleanup owner. Close upstream streams and release acquired leases once. Attempt output finalization within the existing cleanup deadline; do not add an untracked task, a new queue, or a provider retry to repair Redis accounting.

A process crash or an unrecordable Redis write cannot be converted into an exact token count without another durable lifecycle. This plan accepts that limit. It adds no output ledger or reservation to hide the tradeoff. Durable billing and required audit retain their existing owners and guarantees.

## Provider coverage

Remove output-cap enforcement and hostname guards from [OpenAI](../src/providers/openai.py), [Anthropic](../src/providers/anthropic.py), and the [base adapter](../src/providers/base.py). Routing must no longer skip Groq, vLLM, Azure, or custom compatible endpoints solely because output TPM is set.

Extend the existing adapter boundary for complete output evidence. Extract from the already parsed raw response or native stream event before normalization can supply zeros or estimates. Use one parser for each existing stream. Do not create a second generic SSE parser or a provider-discovery request.

| Adapter path | Complete output evidence | Required fixtures |
| --- | --- | --- |
| Generic OpenAI-compatible JSON, including Azure and custom bases | Raw `usage.completion_tokens`; it is the aggregate for the request. | No cap, legacy/modern cap, multiple choices, zero, missing/invalid usage, custom base, Azure aliases. |
| Generic compatible SSE and profiled chat adapters | Authoritative final usage plus normal stream completion. A cumulative usage value replaces earlier values; it is not a delta to sum. | Usage-only chunk, final usage on a terminal choice, cumulative intermediate usage, EOF/cancellation, no client usage request. |
| Groq SSE | Support top-level usage and terminal `x_groq.usage` through adapter-owned extraction. Count matching copies once; conflicting copies are unknown. Require a normal terminal event for complete evidence. | Both layouts, matching duplicates, conflicting layouts, early-error metadata, no usage. |
| vLLM SSE | Final aggregate usage; reuse supported `stream_options.include_usage`. Intermediate continuous usage is partial evidence. | Include usage enabled, client suppression, multiple choices, continuous statistics, missing final chunk. |
| Anthropic JSON and SSE | Raw `output_tokens`; final message usage and message stop for streams. | Thinking/tool output, cache input fields, partial message start, missing terminal usage. |
| Gemini native text | Raw candidates plus separately reported thoughts. When thoughts are omitted, accept zero thoughts only if valid raw prompt/total counts prove total equals prompt plus candidates; otherwise use unknown evidence. | Thinking/non-thinking, all candidates, input exclusion, native final usage, missing or inconsistent totals. |
| Bedrock Converse text | Raw `usage.outputTokens` and native final stream metadata. | JSON, native event stream, thinking/tool output, cache input fields, missing metadata. |

Reuse [provider resolution](../src/providers/resolution.py), [the adapter registry](../src/providers/registry.py), [compatible stream translation](../src/providers/openai_compatible.py), and [profiled chat adapters](../src/providers/profiled_chat.py). Add only a small provider override or typed profile field where a real usage-layout difference requires it. Keep provider-name conditions out of generic routes.

Request final stream usage only through verified existing capability metadata. Preserve the client's requested stream shape: internally needed usage-only frames remain hidden unless the client requested them. Do not send a speculative parameter to an arbitrary compatible server.

Missing, negative, fractional, boolean, oversized, or conflicting usage is unknown. Explicit integer zero is known. Billing estimates and character estimates are not complete output evidence. Preserve existing caller-facing usage and billing contracts separately.

General support means supported text adapters can run under output policy without brand/URL qualification. It does not guarantee continuous quota service for a server that never reports usable output. Such a server enters the documented unknown-usage policy.

## Endpoint and batch scope

Apply one policy to JSON and streaming requests through the current Chat Completions, Completions, Responses, and Messages paths. Reuse their canonical chat execution; do not add four parallel implementations.

Local batch items use the same scoped counters as synchronous requests. Each item execution gets its normal policy admission. Its bounded retries can finish. Count generated work from each provider attempt, including work from a stale worker when usage is known.

Keep PostgreSQL claims and claim-epoch fencing for batch result commits. A reclaimed item that performs another provider call gets a new output event ID. It must not reuse the previous attempt receipt or erase previous measured output.

Keep governed items on individual provider calls. A provider-native or aggregated microbatch does not supply reliable per-caller output attribution for this scope. Keep the existing guard and ordinary fallback; lifting it needs separate attribution work.

Keep Realtime and provider-native asynchronous batches unsupported under an applicable output policy. Keep embeddings, images, speech, transcription, and rerank on their existing controls. Text input can retain its current supported multimodal input behavior; do not silently count other output units as text tokens.

Selector classifier output remains outside caller output TPM, as in the current feature scope. Its existing routing and billing controls remain active.

## Redis contract

Use the current shared client, bounded pool, Lua helper, namespace builder, and preload lifecycle. Keep protected Redis with no eviction and `fail_closed` for active output policies. There is no per-process quota fallback. This change retains the supported standalone topology; it does not add Redis Cluster support.

Use a new output capability schema version, v2. Do not read or reinterpret v1 reservation state. Hash identities. Keep no more than four scope keys and one final receipt key in an accounting operation.

Each scope bucket stores a UTC minute ID, recorded usage, and an unknown-usage flag. Old-minute buckets read as empty for the current minute. Unknown flags expire with their minute and cannot be cleared by a later known charge in the same minute.

Keep values as validated nonnegative integers. Because soft usage can exceed a configured limit, saturate the admission counter only at the global maximum supported limit, 2,147,483,647. This remains exhausted under every legal policy, including a later limit increase. Do not clip at the current scope's limit, which would lose relevant usage after a policy increase. Existing billing retains its full count independently.

### Admission

In the ordinary and unified rate/fair-share/parallel scripts:

1. Read Redis time and validate the output argument and bucket shapes before existing admission writes.
2. For each configured scope, return unavailable if usage is unknown; return 429 if recorded usage is at least its limit.
3. Run the existing RPM, TPM, fair-share, and parallel checks and acquisitions.
4. Return the output snapshot with the normal admission result.

Output admission does not write a bucket, allocate an ownership record, increment output, or replay a saved legacy admission result. Remove the output-specific replay path that can return old RPM/parallel results. Preserve the existing ownership and release contracts for parallel leases.

A denial creates no output charge and must not partially acquire legacy controls. Keep key, argument, result-size, type, integer, and minute validation. Output checks remain bounded and part of the existing transaction, not separate GET calls.

### Final accounting

Use one Lua operation for a positive output count or unknown-usage event:

1. Validate the event ID, fingerprint, receipt, all bucket types/values, and bounded serialization before writes.
2. If the receipt already exists and matches, return its original result without a new charge. Reject conflicting reuse.
3. Read Redis time. For each captured scope, start the current minute if necessary. Add the count with saturation, or set the unknown flag.
4. Store one compact final receipt and return the scoped snapshot.

A final receipt is a deduplication record, not a reservation or a billing ledger. It stores the scope/event fingerprint, evidence kind, count when known, minute, and result. Use a fixed 120-second TTL after recording. Keep accounting retries and callbacks within that retention; never replay an expired event as new work.

Keep 512-byte key and 4 KiB serialized-receipt bounds. Bucket expiry is the next minute boundary plus 30 seconds. There is no wildcard deletion, scan, global singleton counter, or per-token update.

Use the existing bounded NOSCRIPT load-and-retry mechanism within the one-second coordination deadline. Do not add generic transport retries or a durable reconciliation worker. A lost acknowledgement can be checked/replayed with the same retained event ID through the existing lifecycle; never repeat a provider call to repair accounting.

Atomic script execution prevents concurrent interleaving. It does not provide rollback after every Lua/runtime/OOM failure. Validate before writes, protect memory headroom, and report ambiguous accounting failure. Do not promise durable exactly-once output accounting after lost Redis state or process death.

## Types and code changes

Keep the current cohesive files and replace their responsibilities in place.

| File or owner | Planned change |
| --- | --- |
| [Output types](../src/services/admission/output_limit_types.py) | Keep bounded scopes. Remove allowance/reservation types. Add immutable policy, usage snapshot, and final accounting-event types. |
| [Output preparation](../src/services/admission/output_admission.py) | Prepare verified scopes only. Remove cap arithmetic and admission-owned retention/IDs. |
| [Output Lua](../src/services/admission/output_limit_lua.py) | Replace reserve/refund scripts with read-only output admission checks and final usage recording. |
| [Redis output boundary](../src/services/admission/output_limit_redis.py) | Parse v2 snapshots/receipts and map exhausted, unknown, and unavailable results. Replace settlement with accounting. |
| [Rate contracts](../src/services/admission/rate_limit_contracts.py) and [lease](../src/services/admission/rate_limit_lease.py) | Replace output reservation with an optional policy/snapshot/context. Preserve parallel acquisition ownership. Allow unknown output remaining values. |
| [LimitCounter](../src/services/admission/limit_counter.py), [ordinary script](../src/services/admission/rate_limit_admission_lua.py), [unified script](../src/services/admission/tier_fair_share_admission_lua.py) | Update existing narrow output seams. Keep one admission transaction and the unchanged null-policy path. Do not add a new concern to the large counter file. |
| [Output context](../src/services/admission/output_token_context.py) | Admit once per caller request, assign each actual attempt an ID, record final evidence once, and remove repeated output-only reservation checks. |
| [Preflight](../src/chat/preflight.py), [rate policy](../src/rate_limit_policy.py), [middleware](../src/middleware/rate_limit.py) | Remove output-specific cap validation. Carry the verified policy and snapshot into the same context. |
| [Executor](../src/chat/executor.py), [chat hop](../src/providers/chat_hop.py), [chat route](../src/routers/chat.py), [stream usage](../src/chat/stream_usage.py) | Remove cap qualification. Capture raw complete usage with one parse. Record before response completion and share cleanup ownership. |
| [MCP](../src/chat/mcp_execution.py), [cache](../src/cache/middleware.py), [batch policy](../src/batch/policy.py), [worker persistence](../src/batch/worker_persistence.py) | Preserve existing lifecycle and fencing; use the new event contract. Zero-output paths have no accounting write. |
| [Metrics](../src/metrics/output_tpm.py) | Replace reservation/refund/cap outcomes with admitted, denied, unavailable, accounted, unknown_usage, accounting_failed, and saturated. Keep fixed labels. |
| [UI help](../ui/src/lib/outputTpm.ts) and [rate documentation](../docs/features/rate-limiting.md) | Describe completion accounting, admitted-call overage, provider support, missing usage, and reset semantics. |

Keep the four-column [migration](../prisma/migrations/202610050001_output_tpm_limits/migration.sql), schema, policy repository, joined auth query, cache invalidation, API contracts, UI editors, and permission/self-service tests. Keep the response-cache v6 correction for modern caps; output accounting semantics do not require another response-cache namespace change.

Keep current auth-mode safeguards: stored-key policies use the existing hydrated policy snapshot. This plan does not add shared policy hydration to JWT/custom authentication, relax authorization, or remove the master-key exemption.

Delete unused reservation code and assertions: OutputAllowance, OutputReservation, reserve/settle amount arguments, reserved/settled record phases, cap-required/provider-unsupported errors, refund logic, and per-attempt quota reacquisition. Preserve normal generation-cap validation and existing lease release logic.

## Responses and observability

Keep the existing output limit, remaining, reset, and scope header names.

Known JSON accounting returns the snapshot from the final Lua result, with remaining clamped at zero. There is no extra header read. Known zero/cache responses retain the admission snapshot. Streaming response headers retain the admission snapshot because they are sent before final usage.

When remaining capacity is unknown, omit the remaining header rather than emit zero. Keep the known limit/reset/scope where available. Exhausted usage returns the existing scope-specific 429 code and Retry-After. Unknown usage and coordination failure use distinct local 503 codes; neither changes provider health or triggers routing cooldown.

Select the scope with highest recorded utilization for normal headers, with the existing stable tie order. Select the affected unknown scope for unknown results.

Record bounded decision/accounting metrics and sanitized logs. Include accounting failure in the existing operational degraded detail where that detail is available. Do not add tenant, event, key, or URL labels, a reporting query on inference, or a dashboard subsystem.

UI helper text must say that output is counted after calls finish, new calls are blocked when the recorded limit is reached, admitted calls can exceed it, and the counter resets at UTC minute boundaries. Keep form validation, permissions, accessibility, and null/omission behavior.

## Efficiency and capacity budget

| Path | Required output work |
| --- | --- |
| No output policy | No added SQL, Redis command, payload change, receipt, or background work. |
| Initial governed admission | Read up to four output buckets inside the existing admission command; no output state write. |
| Complete positive output | One final Lua command across all scopes plus a receipt. No separate counter GET. |
| Complete zero, cache hit, or proven no dispatch | No output accounting command and no receipt. |
| Unknown dispatched output | One final Lua command to mark unknown scopes and save a receipt. |
| Retry or MCP phase in an admitted request | No new output admission command; at most one final accounting command for each actual attempt. |
| Stream frames | Local parsing/evidence only; no output Redis or SQL calls per frame. |

A normal positive-output request still uses one admission and one accounting command. The expected savings are no output writes at admission, no cache/zero refund, fewer receipt allocations, and no extra retry/MCP output admissions. Do not claim a large latency improvement without measurements.

Reuse current clients and pool allocations. Keep the one-second output coordination bound and existing total/cleanup deadlines. Preload scripts in the existing bootstrap lifecycle. Do not add new infrastructure, package dependencies, process roles, or config aliases.

Receipt capacity is final accounting events per second times 120 seconds. At 100 events/second, up to 12,000 receipts are retained. The 4 KiB serialization ceiling alone is about 46.9 MiB at that rate, before key/allocator overhead and buckets. This is a sizing bound, not a measured footprint. Measure actual MEMORY USAGE and reserve operating headroom.

Size buckets from active scope identities in the 90-second retention horizon. Include API replicas and local batch workers in the finalization rate. Shared parent buckets need a contention benchmark; per-process pools must still fit the existing deployment-wide capacity budget.

## Implementation sequence

| Slice | Deliverable | Completion gate |
| --- | --- | --- |
| 1 | New typed policy/snapshot/event contract, v2 Redis admission and final accounting, removal of reserve/refund primitives. | Real Redis proves recorded-only admission, all-scope updates, idempotent receipts, unknown guard, saturation, boundaries, and ordinary/unified parity. |
| 2 | Shared request/attempt lifecycle, zero-output fast paths, no cap requirement, no quota interruption of admitted retries/MCP. | App and Redis routes prove a crossing response completes, the next request is blocked, no-cap requests run, and multi-phase work is recorded once. |
| 3 | General provider evidence, Groq/vLLM fixtures, native adapter coverage, streamed final usage without duplicate parsing. | Deterministic provider tests cover success, zero/missing/conflicting evidence, multiple choices, reasoning, terminal ordering, errors, timeout, and cancellation. |
| 4 | Local batch wiring, claim fencing, unsupported-mode guards, and obsolete-code removal. | Real batch/Redis tests prove sync sharing, actual retry counts, stale-worker result denial, and the retained native/microbatch boundary. |
| 5 | UI/docs semantics, configuration/rollout checks, broader regression and performance evidence. | Required lanes, lint/build, migrations, command budget, baseline comparison, and rollout checks pass or have explicit pre-existing failures. |

Change shared types and their callers in one coherent patch so the application remains importable. The slices define review and test gates, not separate deployments. Remove obsolete adapter methods after their callers migrate. Do not ship intermediate mixtures of reservation and usage semantics; the final release has one policy path.

## Required tests

Update existing tests when their reservation behavior is deliberately replaced. Do not delete distributed or failure coverage because the old assertion becomes obsolete.

| Area | Required cases |
| --- | --- |
| [Contracts](../tests/test_output_tpm_contracts.py) | Same payload behavior with and without policy, no required cap, normal cap translation, scope identity, integer bounds, known zero versus unknown, null-policy command parity. |
| [Redis](../tests/test_output_tpm_redis.py) | Two independent clients share four scopes; parallel admission does not consume output; concurrent completions exceed and then block; equal-limit denial; one charge per receipt; conflicting event reuse; expired/old/future/corrupt state; saturation; unknown flags; atomic legacy/fair-share/parallel denial; outage, timeout, NOSCRIPT, lost acknowledgement, and TTL recovery. |
| [Text routes](../tests/test_output_tpm_text_redis.py) | All four APIs, JSON/SSE, no cap, cap larger than remaining quota, first over-limit response succeeds, next request is 429, admission remains before cache, zero-output fast path, raw usage before failed translation, cross-minute completion, usage not exposed unless requested, and no mid-stream quota abort. |
| Providers | Generic custom bases, OpenAI, Azure, profiled compatible adapters, Groq, vLLM, Anthropic, Gemini, and Bedrock fixtures from the evidence table. Avoid live API credentials and unverified arbitrary model claims. |
| Missing usage | Successful missing-usage response, partial output, cancellation, ambiguous timeout, valid pre-generation rejection, unknown scope 503/reset, parent-scope impact, known charges do not clear unknown, failed finalization remains visible, and process-death limits are explicit. |
| Retry and MCP | An admitted request finishes after its own or another call's accounting exhausts quota; every actual model attempt is counted once; side effects are not retried for accounting; new caller requests are denied. |
| [Batch](../tests/test_output_tpm_batch_redis.py) | Shared sync/local counters, no-cap items, retries, distinct claim epochs, known stale-worker generation counted, stale result cannot commit, worker death, quota delay, and guarded aggregate/native execution. |
| Admin, database, auth | Preserve four fields, positive/null/omitted validation, tenant/permission denial, self-service ceilings, one joined auth query, invalidation, bootstrap/reload auth and Redis safeguards, and master-key behavior. |
| UI and docs | New help describes actual behavior; existing permissions, input errors, pending saves, focus, and responsive views stay valid; API/header examples agree with Lua. |
| Dependency budget | Null policy has zero added commands; positive output has one final command; cache/zero has none; unknown has one; retries/phases have no output re-admission; stream chunks have no quota I/O. |

Use deterministic barriers and bounded clock seams for window/concurrency tests, plus real Redis time/key TTL checks. Do not add sleeps to conceal races.

Run focused tests first. For the final shared-policy change, run the full affected hermetic, app, Redis, PostgreSQL, and Helm lanes under [the existing classifier](../tests/dependency_lanes.py). New tests must remain in exactly one primary lane; provision required services so new real-dependency tests do not silently skip.

Use [CONTRIBUTING.md](../CONTRIBUTING.md#testing) and [CI](../.github/workflows/ci.yml) for commands and service setup. Recreate isolated task-owned services; the previous verification services were removed. Match the CI-supported PostgreSQL 15 and Redis 7 contracts. Preserve the aggregate test check and all five lanes.

Required commands include frozen development dependency setup, Prisma generation, touched-path Ruff check/format, focused and per-lane pytest, collection with dependency-lane report, fresh/last-release/shared-feature migration-path verification, UI unit tests/build/full-lint baseline comparison, and Helm lint/template for base/eval/production profiles. Do not edit the applied four-column migration.

After isolated test service URLs and the migration admin URL are set, use these existing gates:

```bash
uv sync --frozen --extra dev
uv run prisma generate --schema=./prisma/schema.prisma
uv run pytest --collect-only -qq --dependency-lane-report
uv run pytest -q -m hermetic
uv run pytest -q -m app
uv run pytest -q -m redis -rs
uv run pytest -q -m postgres -rs
uv run pytest -q -m helm -rs
uv run python scripts/verify_migration_paths.py --admin-database-url "$MIGRATION_TEST_ADMIN_DATABASE_URL"
uv run python scripts/docs/generate_config_reference.py --check
npm --prefix ui run test:unit
npm --prefix ui run build
npm --prefix ui run lint
git diff --check
```

Run Ruff on the complete touched Python path set and ESLint on the touched UI paths. Compare full UI lint with the current recorded baseline; require no new findings and no errors in newly changed helpers. Render all Helm profiles with the exact safe test overrides used in CI. Record actual commands, nonzero exits, skips, and results instead of copying old test totals.

## Performance verification

Reuse [the current output profile](../tests/performance/output_tpm_profile.py) and [constant-arrival load generator](../scripts/measure_gateway_load.py). Do not add a new benchmark framework.

Compare main with no policy, the current reservation implementation, and the replacement under the same fixed provider mock and offered rate. Cover one/four scopes, shared organization contention across clients, JSON, streams, cache hits, unknown usage, long calls, retries/MCP, and small separate keys.

Collect command counts, actual receipt/bucket memory, offered/received/success rates, raw samples, p50/p95/p99, first-token time, in-flight/queue slope, pool wait, and Lua latency. Check null-policy parity and no sustained queue growth at the declared workload. Increase the profile only when needed to assess an unresolved contention or saturation concern.

A short local ASGI profile is not a production capacity certificate. Publish its limits. The existing verification timings apply to the reservation baseline only. Save separate replacement results before updating the final report.

## Rollout and rollback

No additional SQL migration is needed for this rewrite. Keep the additive four-column migration in the complete feature release and verify installation/upgrade paths.

Before activation, run one version across all API replicas and batch workers. Drain reservation-version attempts before starting v2 accounting. Do not mix versions with active output limits because they use different enforcement and state namespaces.

If the reservation build was used outside this worktree, pause governed admission or clear policies through the authorized APIs with invalidation, drain attempts/workers, then deploy the replacement and restore limits at a known UTC boundary. Do not flush Redis or merge reservation totals into completion totals. Old v1 keys expire by their original TTLs.

Enable a test key first. Verify successful threshold crossing, next-call denial, minute reset, no-cap Groq/vLLM traffic, streaming, cache hits, MCP/retries, unknown usage, Redis failure, and local batch behavior. Then enable shared scopes. Keep provider rate limits and capacity headroom separate.

For rollback, pause/clear policies and drain attempts before returning to earlier code. Keep the additive schema. Reverting only the application while keeping active policies would change the public guarantee and can reset effective counters.

After a state-losing Redis restart/restore/failover, use the existing controlled pause until the next UTC minute. No automatic continuity is promised. Accounting outages/process loss remain a documented soft-control limitation.

## Acceptance criteria

The rewrite is complete when:

- A call admitted below the recorded limit completes even when its output exceeds remaining quota.
- New requests at exhausted scopes are rejected, across replicas and sync/local batch, until reset.
- Requests do not need output caps for output TPM. Supported compatible text providers are not blocked by provider name or endpoint host.
- All known provider output is counted once per actual attempt while retained state is available; cache and proven zero output have no write.
- Unknown usage is distinct from zero and numeric exhaustion, with the defined temporary 503 and parent-scope behavior.
- Minute attribution, concurrent overage, long-call behavior, accounting failure, and process-death limits are tested and documented.
- Existing billing, hard budgets, authorization, retry bounds, stream framing, and lease fencing retain their contracts.
- The reservation model and unused tests/branches are removed, with their replacement behavior covered.
- API/UI/migration/auth work remains consistent and the required call, memory, latency, and regression evidence is recorded.

## Plan verification

The original planning request changed planning documents only. The subsequent implementation request is complete in this worktree. The [new verification report](output-tpm-usage-verification.md) records actual regression results, corrected setup failures, existing lint findings and SDK skips, raw load evidence, and the performance measurements that remain outside the local profile. Native Gemini streaming retains its existing unsupported status.
