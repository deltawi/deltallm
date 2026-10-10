# Native Realtime WebSocket runtime

Status: opt-in runtime for manual OpenAI conversation and transcription sessions.
Date: 2026-10-01. Owner: API runtime, provider integration, and billing maintainers.

## Implementation and release boundary

Normal application bootstrap now installs the runtime when explicitly enabled.
Configuration, admin model fields, routing and grants, shared Redis leases,
per-turn request limits, exact usage accounting, durable recovery, readiness and
shutdown are connected to their existing owners. PostgreSQL remains the billing
source of truth; the existing spend worker is the only ledger writer.

The supported profile is server-to-server, manual turns, one billable operation
at a time, using the public OpenAI origin. It supports text/audio/cache tokens
and duration-metered transcription. Every provider turn has durable intent before
dispatch; terminal usage is durable before delivery. No provider call is replayed.

Automatic VAD, hard shared budgets, token/audio quotas, legacy key concurrency
caps, guardrails, browser auth and alternative origins remain explicitly denied.
These require additional qualification or shared accounting controls. In particular,
HTTP must honor the same reservations before hard budgets can be supported; a
WebSocket-only hold or periodic spend check is insufficient. Do not remove user
limits to work around these denials.

Live exact-model compatibility, multiple-replica load, ingress behavior and
provider usage reconciliation are still deployment release checks. Repository
wire tests use a local OpenAI-protocol peer; they do not substitute for those checks.

## Decision and current scope

Use the native OpenAI `WS /v1/realtime` protocol for conversational audio and
transcription. Keep OpenAI event names, IDs, audio payloads, function-call results,
and ordering. Map public model aliases only in documented control fields. Keep
supplied-text synthesis on the existing HTTP Speech API; no custom speech
WebSocket URLs or event envelope are introduced.

The endpoint uses existing bearer authentication, model grants, routing generation,
deployment credentials and tier policy services. It confirms manual upstream
session controls before accepting the socket. Each turn consumes existing request
quotas and physical deployment capacity, then records durable dispatch intent.
The first transcription audio append is the billable boundary because streaming
transcription can start work before commit. Each receipt freezes exact customer
and provider charges and enters the existing spend outbox and canonical ledger.

Disabled or unhealthy runtimes deny admission. There is no permissive fallback
for Redis, billing, unsupported policies or missing prices. See the
[public guide](../guides/realtime.md) for configuration and client flows.

## Ownership and integration boundary

| Owner | Responsibility |
| --- | --- |
| `src/api/v1/endpoints/realtime.py` | HTTP/WebSocket edge, shared authentication, bounded handshake, sanitized denials and close behavior |
| `src/realtime/runtime.py` | Dependency ownership, local capacity gate before authentication, tracking and draining connection tasks |
| `src/realtime/lifecycle.py` | Shared cleanup and final-write deadlines, bounded resource exits, finalization failure reporting |
| `src/realtime/session.py` | Two supervised pumps, backpressure, local allowances, periodic health checks, cancellation |
| `src/realtime/protocol.py` | Native event validation, fixed model aliases, unsupported control rejection, public error mapping |
| `src/realtime/errors.py` | Shared diagnostic sanitization, bounded safe correlation IDs, gateway error identities |
| `src/providers/openai_realtime.py` | Server-owned credentials, qualified origin, one connection attempt, bounded upstream buffers |
| `src/realtime/admission.py`, `routing.py`, `capacity.py` | Pinned routes and prices, existing authorization, per-turn dispatch, owned shared leases and revocation |
| `src/bootstrap/realtime.py` | Composition from existing services, recovery attachment even when new sessions are disabled |
| `src/billing/charges/realtime_usage.py`, `realtime_pricing.py`, `realtime_charge.py` | Stable receipt identities, exclusive token/duration units, frozen exact prices and attribution |
| `src/db/billing/realtime_billing.py`, `realtime_recovery.py` | Bounded durable intent/receipt journal and settlement through the existing spend worker |

Lower layers receive typed inputs and socket protocols, never fabricated HTTP
requests. Shared authentication accepts `HTTPConnection` for headers, app and
state. Request-specific custom auth hooks fail closed for WebSockets; HTTP hook
behavior is unchanged. The transport has no global HTTP client and creates no
fire-and-forget tasks. Its required admission context owns final accounting and
resource release on normal close, error, cancellation and setup failure.

Production admission pins the existing routing generation, authorizes the
public model, acquires owned distributed permits, and freezes attribution and
prices **before connecting upstream**. Independent transcription within a
conversation and automatic VAD are denied. Input and output are bounded.
Local per-event checks do no network I/O; periodic checks renew ownership and
recheck revocation. Terminal usage must be durably accepted before forwarding
its terminal event. Cancellation while accepting a receipt must be recoverable
using its stable identity and an existing durable dispatch record.

Every owned parallel-lease writer uses Redis time and sets the shared owner key's
expiry to its latest owner deadline. A short WebSocket lease cannot expire a
longer HTTP or tier lease. Acquisition and renewal keep their existing single
Redis round trip; strict renewal still rejects missing or expired ownership.

Only successful native response completion or transcription completion can clear
recovery. The existing cooldown owner composes its health transition and attempt
release into one atomic Redis call, fencing both the live attempt and recovery
token. Stale owners cannot clear a newer manual cooldown. Durable billing
acceptance precedes this transition; Redis failure or cancellation preserves the
permit for idempotent cleanup and never replays provider work.

Ordinary completion, unsuccessful recovery and failed durable dispatch use the
same confirmed-release path. A missing Redis acknowledgment raises an unavailable
error even when the general router policy is `fail_open`; the permit stays owned
until a confirmed release, including a zero count. Session finalization can retry
under its existing cleanup deadline. A lost reply cannot release a newer owner,
and an unresolved finalization failure reaches the existing readiness signal.
There is no additional successful-path Redis call or independent retry loop.

Reporting starts at the per-operation journal `created_at`, read with the database
acceptance timestamp in the existing locked receipt query. Both are frozen in the
first accepted spend payload. Duplicate delivery and worker recovery preserve that
payload, including receipts accepted by older versions. Session start remains
metadata. The timing basis is dispatch through receipt acceptance, not TTFT; the
canonical ledger retains its existing millisecond precision. No migration or
additional database round trip is needed.

Hard-budget sessions require a proven conservative reservation across applicable
scopes. Session duration is not a cost bound. Unsupported hard-budget profiles
must fail closed. Missing usage, abandoned input, provider disconnects and
process death must retain pending liabilities. No absence of usage implies a
free session. The receipt parser accepts qualified token and transcription-duration usage;
unknown dimensions remain pending rather than being priced as zero.

## Compatibility boundary

- Only the public OpenAI HTTPS origin is qualified. Connection URLs and headers
  come from the selected deployment, never client query parameters. Redirects
  and implicit system proxies are disabled. Custom origins require explicit
  egress qualification before enablement; HTTP compatibility is insufficient.
- Server-to-server bearer auth only. Browser origins, browser token
  subprotocols and beta protocol headers are rejected. No browser credential
  storage, ticket endpoint, or UI audio stack is added.
- Realtime conversation controls, audio-buffer events, cancellation,
  truncation and client-owned function exchanges pass through. Hosted tools and
  image input are not qualified. Unknown client operations fail visibly;
  unknown server events retain their native fields. Tools must be arrays when
  present; omitted tools and empty arrays pass through unchanged. Malformed
  tool containers or content types produce client validation errors before
  policy checks or provider forwarding. Events that exceed the decoder or
  copy recursion capacity are rejected as malformed. A parsed client command
  retains its safe correlation ID on rejection; excessive upstream nesting
  produces a sanitized upstream error.
- Transcription transport accepts `intent=transcription` without inventing a
  model query requirement. Admission resolves an authorized
  default route and validates effective upstream session configuration before
  billable input. Nested models map to that bound route. Additional input
  transcription inside a conversational session is blocked until independently
  authorized and priced.
- Top-level errors, transcription failures and response failures use the same
  diagnostic sanitizer. Native event types, safe server/client event IDs, and
  transcription item/content identities remain available for correlation.
  Error messages, codes, parameters and extra diagnostic fields are replaced
  or removed. Correlation IDs allow 1–256 ASCII letters, digits, underscores or
  hyphens and cannot contain the selected deployment's credentials. Unsafe
  server error IDs are replaced; unsafe optional correlation IDs are omitted.
  Gateway errors get a unique server event ID and include a safe client event
  ID when rejecting a parsed command, including shared budget, permission and
  rate-limit denials. Policy diagnostics are replaced with the same generic
  admission-denied message used at the handshake. There is no replay, provider
  switching, automatic reconnect, or gateway execution of function calls.

The test fixtures use current native event examples from the
[Realtime WebSocket guide](https://developers.openai.com/api/docs/guides/voice-websockets),
[transcription guide](https://developers.openai.com/api/docs/guides/realtime-transcription),
and [Realtime usage guide](https://developers.openai.com/api/docs/guides/voice-latency-cost).
The transcription query is also shown in the older official
[transcription cookbook](https://developers.openai.com/cookbook/examples/speech_transcription_methods).
GA `session.update` is used, not the cookbook's beta session-update dialect.
The pinned official OpenAI Python SDK 3.22.1 passes both profiles against the
bootstrapped gateway and local provider peer. A live-provider test is still
required to qualify the exact upstream model for release.

## Capacity and failure behavior

Internal defaults bound each process to 64 active connections, a 1 MiB message,
64 MiB cumulative client input, 10,000 client events, 100,000 server events,
a 10-second handshake/write deadline, 60-second idle deadline, and 300-second
session lifetime. The connector bounds its receive queue to four frames,
disables compression, and applies a 32 KiB write high-water mark. The relay has
no event queue: each direction awaits its current send before receiving more.
Three supervised tasks exist per admitted connection; the runtime holds its
local slot through accounting and socket close.

At these defaults, 64 connections imply 128 application sockets and up to 192
relay tasks per process, excluding ASGI/connector tasks and buffers. The four
upstream frames alone can retain roughly 256 MiB per process in the worst case;
JSON objects, encoded copies, ASGI buffers and TLS overhead add to this. Multiply
by worker count and maximum replicas including rollout surge. These are bounded
defaults for testing, **not an approved production capacity envelope**. Uvicorn
and ingress receive buffers/timeouts must be configured consistently, and load
tests must measure actual resident memory and cancellation tails before enablement.

Malformed input, oversized frames, exhausted local allowances, slow writes,
idle sessions, receipt failures, and health/lease failures stop the relay. Both
pumps are joined before the upstream context and admission context exit, in
that order. All three share the original five-second resource-cleanup deadline;
each resource exit is bounded separately so a failed upstream close cannot
grant admission finalization a fresh allowance. The downstream error/close
write is limited to ten seconds and the remaining total drain budget. The
total deadline is therefore fifteen seconds by default, starting at the first
disconnect, failure or shutdown request. No cleanup phase or repeated shutdown
call restarts it. Shutdown waits for that same total deadline and does not
cancel an owner already cleaning up. A local slot remains held through final
socket closure. Resource-exit exceptions and deadline failures remain visible
to shutdown callers, including repeated calls after the connection owner has
exited. Successful cleanup does not misclassify an incoming session exception
or normal cancellation as a finalization failure. A new cancellation that
interrupts a resource exit is recorded as failed cleanup and still propagates
to the connection owner; the remaining resource exits are attempted.

Admission must durably preserve unresolved work if cleanup times out. Context
exits must cooperate with cancellation; the transport cannot promise
finalization of an uncooperative dependency. Shutdown reports unfinished
cleanup and retains its ownership until it actually exits. Unexpected provider
closes surface as sanitized failures. No provider failure is inferred from
local capacity denial.

## Alternatives, rollout and removal

Custom `/audio/*/stream` APIs would require new clients and duplicate protocol
translation. A raw tunnel would skip tenant and billing controls. Wrapping
existing HTTP audio or Responses handlers would lose native session semantics.
The chosen design keeps one small transport and requires the existing policy
and persistence owners to authorize its use.

The additive Realtime intent migration and startup-only configuration ship with
Helm, environment and admin model surfaces. Apply migrations before enabling the
runtime. Older spend workers can consume the same outbox; upgraded recovery
recognizes their canonical ledger writes and settles matching journal entries.
Conflicting ledger facts are quarantined without replacing authoritative usage.

For the shared-lease fix, first disable Realtime admission and drain all old
Realtime workers. Only then enable upgraded workers: old renewal scripts can
still shorten shared HTTP/tier owner-key expiry during a mixed-version rollout.
Keep receipt recovery and the spend worker running during this transition.

Rollback disables new admissions and drains sockets before shared services stop.
Retain the additive schema and pending facts. Accepted receipts continue recovering
with Realtime disabled. Unknown usage remains pending and occupies bounded journal
capacity; it requires authoritative provider reconciliation. Settled journal
entries expire after 30 days. No media or transcript storage is added.

## Verification

`tests/realtime/` covers native field/ID preservation, aliases, transcription
controls, credential/egress handling, malformed messages, usage component
accounting, missing usage, authentication and admission denials, bounded
handshakes, backpressure, cancellation, ownership loss and draining. Regression
coverage includes secret-bearing transcription errors, server/client error
correlation, per-command policy denials, malformed tools and content types,
deep client/provider nesting, cumulative shutdown time, repeated shutdown,
disconnect during drain, pump-join deadlines, resource-exit timeouts and
exceptions, cancellation during resource finalization, and retained ownership
when cleanup outlives its deadline. These tests
use no paid keys; real dependency tests run against isolated Redis and PostgreSQL.
Run the full application lane as well
as the existing authentication and HTTP audio regressions after changes to the
shared connection-auth boundary.

Earlier implementation verification included:

- Hermetic/application regression suite: 5,478 passed.
- Production bootstrap with real PostgreSQL and Redis and a local WebSocket peer:
  two conversation turns and two duration-transcription turns reach the canonical
  ledger and all applicable scope totals exactly once.
- Real dependency failure checks: durable dispatch failure blocks provider work;
  lost leases, revoked users, newly imposed budgets, credential changes, routing
  reconciliation and capacity changes close active sessions.
- Durable recovery checks: repeated receipts, conflicts, older-worker settlement,
  process expiry and unresolved capacity retention.
- Full PostgreSQL/Redis regression lanes: 404 passed; subsequent ownership and
  per-turn quota checks also pass. Official SDK conversation and transcription
  tests run in an isolated environment with locked dependencies.
- UI unit tests: 273 passed; production build; 71 Helm checks; strict public
  documentation build; fresh and upgrade migration validation.

No paid OpenAI request or production deployment was performed. Live model
qualification and the measured capacity envelope remain release gates.


### Review-fix verification (2026-10-01)

The shared-lease, cooldown-recovery and per-turn timestamp fixes passed the full
hermetic/application/PostgreSQL/Redis suite: **5,924 passed**, 71 unrelated Helm
cases deselected. Two later regressions also passed: recovery after a lost Redis
completion acknowledgment cannot release a new owner, and an older accepted
payload retains its original timestamps. The focused Realtime run passed 303
cases, including eight bootstrapped direct-client/official-SDK combinations across
conversation/transcription and healthy/recovering deployments. Repository-wide
Ruff, whitespace checks, nine documentation tests, generated references, strict
public documentation build and public artifact containment passed.

[The comparison harness](../../tests/performance/realtime_review_profile.py) runs
against isolated Redis 7.4.2/PostgreSQL 15 with the locked Python environment.
It measures the changed owner operations at 20 arrivals/second for five seconds
per case, with at most 16 operations in flight. Receipt cases include a fixed
1 ms provider delay and durable acceptance; dispatch, socket handshake, settlement
and TTFT are outside this focused measurement. It is not a deployment capacity
certificate. Baseline: `e0adec486f1baf6adf1cdbba1082ecdf55b1c78a`; after: these three
review fixes, using the same harness in both checkouts.

The repeated pair completed all 800 operations with no dropped arrivals, a maximum
of one operation in flight, zero sampled in-flight slope, and 20 completions/second
in every case. Measured latency in milliseconds:

| Operation | Before p50 / p95 / p99 | After p50 / p95 / p99 |
| --- | --- | --- |
| Non-strict lease renewal | 0.533 / 0.707 / 0.813 | 0.579 / 0.667 / 0.799 |
| Strict lease renewal | 0.537 / 0.775 / 0.878 | 0.552 / 0.809 / 0.950 |
| Healthy turn receipt | 8.514 / 9.361 / 9.803 | 8.427 / 9.131 / 9.238 |
| Recovering turn receipt | 8.462 / 9.301 / 9.587 | 8.378 / 9.178 / 9.397 |

Before and after, each renewal makes one Redis call; each receipt makes one Redis
call and three SQL statements within one transaction (transaction begin/commit are
additional to those statements). The harness asserts these counts. No dependency
round trip, retry or timeout allowance was added; the existing 250 ms billing
transaction bound remains in force. Raw samples also retain scheduling lag and
actual time spent in the fixed provider delay.

The first pair is retained too: the baseline completed 400/400 and the first after
run completed 399/400. One receipt hit the billing deadline during a scheduling
pause of about 280 ms; recovery-receipt p99 was 80.430 ms and maximum in-flight was
seven. The paired repeat above was run without changing code, load or deadlines.
These observations support unchanged call budgets and similar local latency, but
cannot establish a production latency guarantee or conclusively attribute the
initial scheduling pause.

[Raw samples and summaries](project/benchmarks/realtime-runtime-review/) include
both pairs and [the receipt query plans](project/benchmarks/realtime-runtime-review/receipt-explain.txt).
The before/after queries both use the existing primary-key index and lock one row
on a temporary 10,000-row journal. Execution times were 0.018/0.020 ms; these query
plan samples do not measure network or transaction overhead. Reproduce with
`psql -X -v ON_ERROR_STOP=1 -f tests/performance/realtime_receipt_explain.sql`
against an isolated migrated database; all generated rows roll back.

To reproduce the owner comparison, set `DATABASE_URL` and
`DELTALLM_TEST_REDIS_URL` to isolated migrated services, then run the same harness
file with `PYTHONPATH` pointing at each checkout and its locked Python executable:
`python tests/performance/realtime_review_profile.py --label after --output-dir /tmp/realtime-profile`.
Use the absolute harness path and `--label before` when importing the baseline.

### Release acknowledgment follow-up (2026-10-02)

Regression coverage now exercises the production-default `fail_open` router as
well as `fail_closed`: ordinary completion, cancelled/failed recovery, lost
release replies, cancellation, failed dispatch, confirmed zero and a newer owner.
Two real WebSocket/PostgreSQL cases verify that transient release failure closes
the socket, keeps the durable receipt and frees shared capacity during cleanup.
The new Redis regressions reproduced seven failures before the fix.

Verification with isolated services and the configured SDK executable:
`pytest -q tests/realtime tests/router/selection/test_realtime*.py tests/test_realtime*.py`
passed **383** tests. The documentation workflow passed all reference and health
checks, **9** documentation tests, strict MkDocs build and public containment.
Repository-wide Ruff, touched-file formatting and `git diff --check` passed.

The same constant-arrival harness above compared baseline `eec4cda6` with this
release acknowledgment fix. All **800/800** operations completed, with no dropped
arrivals, 20 completions/second in every case and zero sampled in-flight slope.
Successful-path dependency counts stayed at one Redis call per renewal or receipt,
and two SQL reads plus one write in one transaction per receipt. SQL and deadlines
are unchanged. Raw records include scheduling lag, fixed-provider time and load
samples in [the release comparison](project/benchmarks/realtime-runtime-review/release-ack/).

| Operation | Before p50 / p95 / p99 (ms) | After p50 / p95 / p99 (ms) |
| --- | --- | --- |
| Non-strict lease renewal | 0.540 / 0.714 / 1.887 | 0.909 / 1.348 / 1.639 |
| Strict lease renewal | 0.886 / 1.938 / 2.874 | 0.821 / 1.374 / 1.630 |
| Healthy turn receipt | 8.359 / 9.710 / 23.285 | 9.905 / 12.659 / 14.622 |
| Recovering turn receipt | 9.831 / 12.706 / 21.844 | 9.797 / 17.017 / 65.813 |

Maximum observed in-flight work was one except for five during the after-run's
recovering receipt case. This successful-recovery code path is unchanged by the
follow-up, but these samples cannot establish the cause of the latency variation
or a production latency guarantee. No measurement was discarded or deadline relaxed.
