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
| `src/billing/realtime_usage.py`, `realtime_pricing.py`, `realtime_charge.py` | Stable receipt identities, exclusive token/duration units, frozen exact prices and attribution |
| `src/db/realtime_billing.py`, `realtime_recovery.py` | Bounded durable intent/receipt journal and settlement through the existing spend worker |

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

Verification in this implementation includes:

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
