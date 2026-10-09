# Output TPM implementation plan

Prepared and reviewed: 5 October 2026. Status: reservation implementation retained as the current code baseline.
The [output TPM usage enforcement plan](output-tpm-usage-enforcement-plan.md) supersedes this design. The replacement is planned and is not implemented yet. Baseline verification is recorded in `output-tpm-verification.md`.
Baseline: `f5ffd80dfb4c100a2063fb6a7939bd6f7ba68bd2` from `origin/main`.
Branch: `feature/output-tpm-limit`. Worktree: `deltallm-output-tpm`.

Add one nullable `output_tpm_limit` to the existing caller policies. Reserve an enforced output allowance in the current atomic admission call. Return unused allowance after complete provider usage. Keep the current TPM behavior.

The first release covers API keys, runtime users, teams, and organizations. It counts output across all text models at each scope. It reuses the current text, batch, and lease owners. It adds no deployment allocator, tier policy fields, local fallback, background worker, or global feature setting.

The implementation qualifies the official OpenAI and Anthropic text endpoints. It checks the effective endpoint and output cap after deployment defaults. See [verification](output-tpm-verification.md) for the implemented boundary, test results, and performance evidence. Response cache version 6 includes the new `max_completion_tokens` field in its default key fields.

This is a fixed admission-minute limit. Output belongs to the minute when a model attempt starts. It is not a rolling limit or a limit on tokens emitted during each minute. Large caps reduce concurrency until usage is known. These facts must appear in API docs and UI help.

## Research and design choice

The initial research checked these primary sources on 5 October 2026:

| Source | Relevant finding | Choice for DeltaLLM |
| --- | --- | --- |
| [Claude rate limits](https://platform.claude.com/docs/en/api/rate-limits) | Separate input and output TPM. Output is counted during generation; `max_tokens` does not count as output usage. | Use a separate output dimension. State that admission reservations differ from this provider behavior. |
| [Bedrock Mantle quotas](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-mantle.html) | Admission reservations and output generation limits are different controls. | Do not promise generation-time enforcement from an admission counter. |
| [LiteLLM caller limits](https://docs.litellm.ai/docs/proxy/users#tpm-rate-limit-type-inputoutputtotal) and [separate deployment limits](https://docs.litellm.ai/docs/proxy/io_token_rate_limits) | Input/output/total modes, reservations, and usage adjustment are documented. Separate deployment limits are a different surface. | Preserve legacy TPM. Add a caller field; leave deployment output capacity for a separate release. |
| [Gemini rate limits](https://ai.google.dev/gemini-api/docs/rate-limits) | Its usual TPM dimension is input tokens. | Name the output field and token source explicitly. |

A post-response increment is smaller, but concurrent requests can all pass before any usage arrives. It cannot provide a strict admission ceiling. Per-chunk coordination adds stream latency and still does not cover ordinary JSON responses. A bounded reservation with one final adjustment is the smallest design that provides the required concurrency safety.

## Release boundary

Add `output_tpm_limit` to these existing records:

- `DeltaLLM_VerificationToken`: API keys, including service-account keys.
- `DeltaLLM_UserTable`: runtime users.
- `DeltaLLM_TeamTable`: teams.
- `DeltaLLM_OrganizationTable`: organizations.

Use strict positive integers through `2**31 - 1`, or `null`. Reject booleans, numeric strings, fractions, zero, and negative values. Omitted updates preserve the value; `null` clears it. All configured ancestors apply. A child cannot remove an ancestor limit.

Count generated output from every answer attempt and MCP model phase. Include tool, refusal, reasoning, and choice output when the provider includes them in its output total. Do not add reasoning details twice. Input and cached input tokens do not count. A gateway response-cache hit has zero generated output. Existing cache billing remains unchanged.

| Execution mode | Required behavior |
| --- | --- |
| Chat, Completions, Responses, Messages | Enforce the same caller policies for JSON and streaming responses. |
| Local batch text items | Enforce the same caller limits when each item executes. Sync and batch share ancestor counters. |
| MCP chat and provider retries | Reserve each model phase and dispatched attempt. Do not charge caller RPM again. |
| Embeddings, rerank, images, audio | Keep their existing controls. Do not invent output-token conversions for other units. |
| Realtime and provider-native batch | Reject applicable unsupported output policies before upstream work. |
| Selector classifiers | Keep existing routing capacity and billing rules. Classifier output is gateway work and is outside caller output TPM. |

Defer per-model output maps, tier and batch-specific output fields, output capacity pools, deployment output limits, selector output allocation, weighted output fairness, input TPM, output TPD, and generation-time pacing. The release must not expose fields for these controls.

Persisted API-key authentication is the supported policy source. JWT currently creates scope IDs without loading these limits in [authentication](../src/middleware/auth.py). Do not accept client quota claims. Until trusted policy hydration exists, reject shared user/team/org output policy writes in installations with JWT or unqualified custom authentication. Also reject configuration changes that would create that combination with existing policies. Perform this check at the control-plane/bootstrap boundary. Per-key policies remain available. Preserve the master-key exemption. Do not add policy SQL to inference requests or claim unsupported auth is protected.

## Code to extend

| Existing owner | Change |
| --- | --- |
| [Prisma schema](../prisma/schema.prisma), [KeyRepository](../src/db/key_repository.py), [KeyService](../src/services/key_service.py), [auth DTO](../src/models/responses.py) | Four nullable fields, the same joined read, typed scope values, cache version, and existing invalidation. |
| [Rate policy](../src/rate_limit_policy.py), [LimitCounter](../src/services/limit_counter.py), [unified admission Lua](../src/services/tier_fair_share_admission_lua.py) | Add output checks to both ordinary and unified admission. Existing tier and pool limits keep their current dimensions. |
| [Text preflight](../src/chat/preflight.py), [request DTOs](../src/models/requests.py) | Resolve a bounded output allowance after request mutation and before final admission. |
| [Executor](../src/chat/executor.py), [provider resolution](../src/providers/resolution.py), [provider receipts](../src/providers/token_receipt.py) | Enforce the same cap after provider translation/defaults. Capture output evidence before normalization can fill missing values with zero. |
| [Stream usage](../src/chat/stream_usage.py), [chat route](../src/routers/chat.py), [HTTP lease lifecycle](../src/middleware/rate_limit_lifecycle.py) | Use complete output evidence and one cleanup owner. Finish known settlement before the terminal event. |
| [Cache middleware](../src/cache/middleware.py) | Settle JSON and SSE hits to zero through the same owner. |
| [MCP execution](../src/chat/mcp_execution.py), [failover](../src/router/failover.py) | Use one attempt wrapper for output admission and evidence. Preserve provider retry and tool boundaries. |
| [Batch policy](../src/batch/policy.py), [batch item execution](../src/batch/chat_item_execution.py) | Carry output ownership and settle each attempt with existing claim fencing. Remove parallel-only early returns where they skip output cleanup. |
| [Realtime capacity](../src/realtime/capacity.py), native batch submission owner | Extend unsupported-policy guards. |
| [Admin endpoints](../src/api/admin/endpoints/), [UI contracts](../ui/src/lib/api.ts), [limit summary](../ui/src/components/admin/RateLimitSummary.tsx) | Add create/edit/read/summary support for the four caller scopes and existing self-service rules. |

The current [rate limit docs](../docs/features/rate-limiting.md) incorrectly say that identity per-model controls do not exist. Correct that statement while documenting that this first output release has scope-wide limits only.

## Output allowance

A limited request must provide an explicit output cap. Use Chat `max_tokens` or `max_completion_tokens`, Responses `max_output_tokens`, and Messages `max_tokens`. Add the missing Chat field and map these names through the existing adapters. Reject conflicting Chat cap fields.

Calculate `R = cap per choice * n` with checked arithmetic. Preserve the existing rejection of unsupported `best_of`. If no cap is supplied, return a clear `400` before charging rate counters. Do not insert a new global 1024-token default, use a context window as an output cap, or silently shorten a request to fit capacity.

This explicit-cap requirement applies only when an output policy is active. Requests with null policies keep their payload and behavior. Adding support for the modern Chat cap must still have normal provider contract tests.

Use existing provider capability metadata plus one typed output-bound capability. A qualified adapter must enforce the reserved cap across reasoning, tool output, and all choices. Check every actual candidate before dispatch. Provider defaults cannot increase the cap after admission. Do not fetch capabilities during a request or build a new model registry.

Exclude candidates that cannot enforce the bound. If none remains, return a sanitized `503`; classify it as a local policy failure. If `R` cannot fit a caller scope, return that scope's `429`. Caller quota failures do not trigger provider cooldown or ordinary provider retries.

## Minimal runtime contract

Extend existing contracts with:

- An explicit output dimension on rate checks and header state.
- A typed allowance containing cap, choice count, and aggregate bound.
- An optional output reservation on the existing `RateLimitLease`.
- Output evidence: complete nonnegative provider count, proven no dispatch/cache hit, or unknown.

The server-owned operation ID, phase index, and attempt index identify a reservation. Reuse the existing server event identity for correlation. A client request header is not an ownership token. Fingerprint the bounded allowance and pinned scope keys so a retry cannot reuse an ID with different inputs.

The Redis ownership record needs two states: reserved and settled. Expiry is record absence, not another state machine. Keep only the allowance, scope keys, admission minute, reset/expiry, fingerprint, and settlement result. Do not store provider payloads, full policy snapshots, or billing receipts.

There are at most four output scopes per attempt. Use the shared namespace builder, hashed IDs, and fixed names. Bound key bytes and ownership bytes at serialization; a 512-byte key and 4 KiB ownership record are sufficient design ceilings for this scope. Derive the maximum attempts/phases from existing retry and MCP bounds.

## Atomic admission

For a stable configured policy and retained Redis state, enforce:

```text
committed output + outstanding allowances <= output_tpm_limit
```

The guarantee assumes that the provider enforces its qualified cap. A policy decrease can place existing usage above the new limit; new admission then stops until reset. Do not change or refund an in-flight reservation because policy changed.

Use one fixed Redis hash per output scope with `window_id` and `used` fields. Read Redis `TIME` inside admission and use `floor(seconds / 60)`. A new minute treats the old value as zero; write the new fields only after all checks allow. Return the selected minute and reset in the reservation.

This layout avoids a clock round trip and client clock skew. It also avoids constructing undeclared minute keys in Lua. Redis requires script keys to be supplied explicitly in [EVAL](https://redis.io/docs/latest/commands/eval/). Pass all scope and ownership keys through `KEYS`. Do not change legacy RPM/TPM window keys.

Extend both existing admission entry points:

1. Check existing ownership and fingerprint. A repeated successful acquire returns its existing result without charging again.
2. Validate all input bounds, stored values, key types, legacy checks, output scopes, and applicable tier/fair-share/parallel controls.
3. If any check denies, change no quota counters and acquire no new leases.
4. On success, charge legacy amounts, reserve `R` in each output bucket, acquire applicable leases, and record ownership in the same Lua operation.

Preacquired parallel leases remain under their existing owner and must be released on denial. Do not replace current preflight ordering. Deduplicate scope keys before validation. A backward clock value must not reset a bucket that contains a later minute.

Use the existing [cached Lua runner](../src/services/redis_lua.py). Preload scripts during bootstrap, not on the first inference request. Keep script text constant and use arguments for values. Test `NOSCRIPT` recovery.

An ambiguous transport result can retry only the same acquisition ID within the existing deadline. A Lua runtime error is an unavailable result, not a safe retry with a new ID. Lua does not roll back earlier writes after an error; validate before writes and keep any uncertain charge conservative. Dispatch only after a complete successful acquisition.

## Idempotent adjustment and cleanup

For complete output `A`, replace the reserved amount `R` with `A`. A proven cache hit or no-dispatch result uses zero. Missing, partial, estimated, or malformed output keeps `R`.

Validate output at the raw provider boundary. A normalized `completion_tokens=0` is insufficient if the source field was absent. Validate output independently of input/pricing fields. A character estimate is telemetry, not refund evidence.

Use one bounded settlement Lua operation across the four scopes. Validate before mutation, record the settled ownership marker before any refund, then apply adjustments. If a runtime error interrupts adjustment, some refund may be withheld; a retry must never refund twice. Repeated equal settlement is a no-op; conflicting evidence is a recorded contract error. A settled marker alone does not prove that every adjustment completed. Return confirmed bucket values on success; retain the admission snapshot after an uncertain result.

Only adjust a bucket whose stored minute matches the reservation. If a later minute is present, close ownership without changing that bucket. Do not recreate old state. Missing state before its logical reset is a coordination failure; missing state after reset requires no refund.

Unknown usage needs no counter write. Keep its reservation charged and close the request-local owner conservatively. Cleanup cannot later turn unknown dispatch into a no-dispatch refund. If actual usage exceeds the enforced cap, charge the excess once where the window still applies and report a provider contract violation. Never clamp the count to hide the excess.

A bucket expires after its reset plus a small cleanup grace. Ownership records expire after reset plus the existing maximum operation deadline and bounded settlement grace. No scan, heartbeat, janitor, or SQL ledger is needed for these short-lived throughput counters. A crash withholds the refund; the next admission minute restores capacity.

One attempt wrapper owns provider dispatch and output evidence. The first attempt uses the reservation from final preflight. A retry or next MCP phase acquires output only; it does not repeat caller request-window charges. Freeze scope attribution, and preserve the public model across fallback. Count every generated phase, not just the final MCP response.

Settle complete output before JSON delivery or the stream terminal event. Keep internal provider usage requests and client-visible stream options unchanged. On disconnect or cancellation, close upstream resources and retain unknown allowance. Use the HTTP lifecycle only as the existing final cleanup fallback. Release parallel leases even when output adjustment fails.

Local batch uses the same owner and item claim epoch. A reclaimed item gets a new attempt reservation; a stale worker cannot commit a result. Charge at execution, not upload. Cache hits still pass full preflight before lookup, then settle to zero before delivery. They can be denied before lookup when output capacity is exhausted.

## Redis failure and recovery

An active output policy requires shared Redis and existing `redis_degraded_mode=fail_closed`. Reject non-null policy writes when that contract is unavailable. At bootstrap/config reload, validate existing policies through a bounded control-plane check. Do not add a per-process output limiter or silently follow legacy fail-open behavior.

Unavailable or ambiguous admission returns `503` before dispatch. Settlement failure retains the charge and emits a bounded metric/log; it does not invalidate a successful model response or cause another provider attempt.

Use the existing Redis client, pool, timeouts, and dependency readiness. No per-request `INFO server`, shared continuity record, 60-second cold-start gate, or new recovery worker is required for this release.

The supported coordination topology is a protected standalone primary with no eviction of admission/ownership keys. An active bucket is authoritative only within retained Redis state. Redis replication can lose acknowledged writes during failover, as the [Redis replication docs](https://redis.io/docs/latest/operate/oss_and_stack/management/replication/) explain. This feature is a throughput limiter, not a durable economic ledger.

After a state-changing restart, flush, restore, or failover, use existing deployment/ingress operations to pause output-governed traffic until the next UTC minute, then resume on the healthy primary. Test this recovery procedure. A normal application restart with retained Redis state needs no pause. Automatic failover with a strict continuity guarantee and Redis Cluster support require separate work. Do not claim that a health probe or periodic server-ID sample proves that guarantee.

## Persistence and operator contract

Use one additive Prisma migration with four nullable integer columns and positive-value constraints. No backfill or tier/pool schema change is needed. Extend the existing joined auth query, `KeyRecord`, typed auth DTO, and serialization together. Bump `key:v4` to a new cache version and extend existing key/user/team/org invalidation. Old invalidation consumers remain until old replicas drain.

Update all create/update/read/list paths for these scopes, including service-account keys, runtime-user updates, and self-service ceilings. Backend authorization and ancestor limits remain authoritative. Do not expand unsupported JWT/custom scope handling through client metadata.

Add one labeled numeric input and summary value to existing caller limit forms. Use **Output tokens per minute** and **output tok/min**. Explain the required request cap, held allowance, and admission-minute behavior. Blank on create means null; clearing on edit sends null. Test omitted versus null, permissions, validation, and keyboard use.

Extract only the rate-limit persistence, mapping, and form seams that this feature touches in files over 800 lines. Use existing UI components and transport. Follow the required domain split for touched API contracts. Do not refactor all tier/model editors, rebuild the form system, or add a chart dependency.

Expose `x-ratelimit-limit-output-tokens`, `x-ratelimit-remaining-output-tokens`, and `x-ratelimit-reset-output-tokens`. Choose the output scope with the least remaining fraction and a stable tie order. Use typed dimensions; suffix and `amount == 1` heuristics can misclassify output.

Headers use admission state for streams. JSON may use the known settlement result without another read. On settlement failure retain the admission snapshot; do not present an unconfirmed refund as available capacity. Preserve existing headers and extend CORS exposure if applicable.

Use the current error envelope: scope-specific `429` plus `retry-after`, clear `400` for a missing/conflicting cap, and sanitized `503` for unavailable coordination/cap capability. Do not expose ownership IDs or Redis keys.

Null is the feature's off switch. No new global settings are needed. Document existing Redis requirements in config/Helm examples and production checks where applicable. Correct the rate-limit docs and add a release note and API examples.

## Efficiency budget

| Path | Allowed added work |
| --- | --- |
| No output policy | No SQL, Redis call, provider parameter, background task, or script work for output. Existing calls still occur. |
| Normal limited attempt | Output reservation inside the existing admission call; at most one final adjustment call. No separate counter read. |
| Unknown dispatched usage | Keep the reservation; no output adjustment call. |
| Retry or MCP next phase | One output-only acquisition and at most one adjustment for that additional model call. |
| JSON/SSE cache hit | One zero-output adjustment; no provider work. |
| Stream chunks | Local usage tracking only; zero output dependency calls per chunk. |

One final adjustment is the necessary cost of returning capacity safely. Combine it with existing release work only when keys, ownership, and timing permit it without a second lifecycle. Do not make that optimization a new subsystem or weaken lease duration.

Lua work is linear in at most four output scopes. Use compact scalar ownership fields and return header state from the existing result. Add no clients or pool growth. Metrics use fixed outcome/source/mode enums, not identity labels.

Measure before/after dependency counts and p50/p95/p99 gateway overhead with [the existing load generator](../scripts/measure_gateway_load.py) and a fixed local provider mock. Include a shared organization, streaming, and many small keys. Check offered/received rate, time to first token, queue slope, pool wait, and Lua time. Disabled/null policies must have no measurable regression beyond run variance.

Capacity is active scopes plus `attempts per second * ownership retention`. Measure bytes per bucket/record and state the maximum replica/load profile. No new performance framework or fixed 50 RPS release claim is needed.

## Implementation sequence

| Slice | Deliverable | Gate |
| --- | --- | --- |
| 1 | Typed dimension/allowance/evidence and the small affected policy seams. Record the admission-minute and Redis recovery choices in a short design decision. | Cap, choice count, provider qualification, unknown evidence, and null-policy parity tests. |
| 2 | Four columns, repository/auth/cache propagation, guarded admin contracts, and caller UI inputs/summaries. | Fresh/upgrade migrations, DB constraints, authorization, self-service, auth-mode compatibility, null/omission, and peer invalidation tests. |
| 3 | Atomic reservation in existing admission and compact idempotent adjustment. Cover ordinary and unified admission. | Real Redis concurrency, mixed-scope denial, fairness/parallel denial, clock/window boundaries, duplicate calls, malformed state, and script/outage tests. |
| 4 | Shared attempt/stream/cache lifecycle, MCP phases, local batch fencing, and unsupported-path guards. | Route, retry, terminal-order, disconnect, crash, batch, cache, and unsupported-profile tests. |
| 5 | Docs, production config checks, performance evidence, rollout and rollback verification. | Required affected test lanes, UI build/lint, migration paths, config/Helm parity, and dependency/latency budget. |

Keep null policies compatible at each slice. Do not activate policy writes in a mixed-version installation. All five slices are necessary for this release; deferred controls are not release gates.

## Acceptance checks

Use real Redis for shared correctness and real PostgreSQL for schema/invalidation paths. Extend adjacent tests rather than create a parallel harness.

- Many replicas cannot admit more than the configured allowance within retained state. One scope or fair-share denial leaves output uncharged.
- Complete usage returns unused capacity once; missing output, prompt-only stream usage, estimates, and worker death cannot refund.
- Old-minute settlement cannot lower or recreate current-minute capacity. Duplicate and conflicting calls cannot double-refund.
- An enforced cap covers choices, tools, and reasoning. Provider defaults, hooks, and fallback cannot enlarge it. Requests without output policy stay unchanged.
- Streams settle before terminal delivery, close on disconnect, and release owned parallel leases once. Cleanup failure cannot retry the provider.
- All four text APIs, every MCP phase, cache hit type, retry, and local batch execution use the same owner.
- No upload-time charge, no stale batch commit, and no silent Realtime/native-batch/auth-mode bypass.
- Redis errors are closed for output admission. Script loading, `NOSCRIPT`, cancellation, ambiguous responses, and controlled state-loss recovery are tested.
- New API/UI fields preserve permissions, null/omission, self-service ceilings, cache invalidation, and unknown metadata.
- Automated dependency counts enforce the table above; contention and memory remain bounded at the declared load.

During implementation, run focused checks first, then the required affected `hermetic`, `app`, `postgres`, `redis`, and `helm` lanes. Preserve all five CI lanes and the aggregate `test` check. Run Ruff, Prisma generation, fresh/upgrade and migration-path checks, touched-file ESLint, UI unit tests/build/full lint, and applicable Helm lint/template for base/eval/production. Follow [CONTRIBUTING.md](../CONTRIBUTING.md) and [RULES.md](../RULES.md) for exact commands. Record exact results and existing failures.

## Rollout and rollback

Apply the additive schema first. Deploy all API replicas and workers with null output limits, drain old versions, and confirm the new cache contract and provider qualification. Confirm closed mode, Redis memory protection, supported auth modes, and the recovery procedure before the first policy write.

Enable one test key with explicit request caps. Check concurrency, short responses, streaming, retries, cache hits, outage, and batch. Then enable shared scopes. Keep provider headroom because gateway windows and provider generation windows differ.

To roll back, clear active output fields through authorized APIs, complete invalidation, drain in-flight output ownership, then roll application code back. Keep the additive schema. Removing a limit does not refund an accepted attempt or claim that protection remains active.

## Planning verification

Only this plan is changed. All 30 local references resolve. Code fences are balanced. The complete new file and the tracked worktree pass whitespace checks. The scope and ownership paths were checked against the source at the recorded baseline.

No product implementation or runtime test result is claimed. Runtime, migration, UI, and performance evidence remains implementation work.
