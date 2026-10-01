# Native Realtime WebSocket foundation

Status: first implementation slice; **not enabled for production traffic**.
Date: 2026-10-01. Owner: API runtime, provider integration, and billing maintainers.

## Decision and current scope

Use the native OpenAI `WS /v1/realtime` protocol for conversational audio and
transcription. Keep OpenAI event names, IDs, audio payloads, function-call results,
and ordering. Map public model aliases only in documented control fields. Keep
supplied-text synthesis on the existing HTTP Speech API; no custom speech
WebSocket URLs or event envelope are introduced.

This change implements the transport foundation and exact usage interpretation.
It registers the endpoint, reuses bearer authentication and organization checks,
adds the OpenAI connector, bounds the relay, and tests it using deterministic
provider and admission doubles. **Application bootstrap does not install a
Realtime runtime.** The endpoint returns a 503 denial, or 403 on ASGI servers
without WebSocket denial-response support. Existing configuration cannot enable
it. This is deliberate: HTTP budget checks and concurrency leases alone cannot
authorize repeated billable turns on a socket.

Routing, model grants, distributed admission, durable usage ingestion and
recovery are the next implementation slice. The `RealtimeAdmission` contract is
required; there is no permissive production implementation or hidden fallback.
Tests install a runtime explicitly. They prove wire and lifecycle behavior, not
production policy correctness or live-provider compatibility.

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
| `src/billing/realtime_usage.py` | Stable receipt identities, exclusive text/audio/cache units, exact Decimal prices, explicit unknown usage |

Lower layers receive typed inputs and socket protocols, never fabricated HTTP
requests. Shared authentication accepts `HTTPConnection` for headers, app and
state. Request-specific custom auth hooks fail closed for WebSockets; HTTP hook
behavior is unchanged. The transport has no global HTTP client and creates no
fire-and-forget tasks. Its required admission context owns final accounting and
resource release on normal close, error, cancellation and setup failure.

Production admission must pin the existing routing generation, authorize the
public model and any independent transcription model, acquire owned distributed
permits, and freeze attribution and prices **before connecting upstream**. It
must enforce bounded billable input and output, including automatic VAD turns.
Local per-event checks do no network I/O; periodic checks renew ownership and
recheck revocation. Terminal usage must be durably accepted before forwarding
its terminal event. Cancellation while accepting a receipt must be recoverable
using its stable identity and an existing durable dispatch record.

Hard-budget sessions require a proven conservative reservation across applicable
scopes. Session duration is not a cost bound. Unsupported hard-budget profiles
must fail closed. Missing usage, abandoned input, provider disconnects and
process death must retain pending liabilities. No absence of usage implies a
free session. The current receipt parser marks duration-based or unknown usage
unqualified, so that such work cannot be accidentally priced as zero.

## Compatibility boundary

- Only the public OpenAI HTTPS origin is qualified. Connection URLs and headers
  come from the selected deployment, never client query parameters. Redirects
  and implicit system proxies are disabled. Custom origins require explicit
  egress qualification in the next slice; HTTP compatibility is insufficient.
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
  model query requirement. Its admission owner must resolve an authorized
  default route and validate effective upstream session configuration before
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
A pinned official SDK and live-provider test are still required before claiming
the combined handshake/profile is qualified for release.

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

There is no schema migration and no configuration switch in this slice. Adding
an apparently usable switch before its admission owner exists would be
misleading. The production slice must add `realtime` mode/capability metadata,
configuration and all applicable environment/Helm/admin surfaces together,
wire the lifecycle through bootstrap, and validate real Redis/PostgreSQL
failure and recovery behavior. It must also prove ingress upgrades and drain
behavior, official SDK compatibility, and bounded dependency-call costs.

Rollback of this slice removes the new route and modules; HTTP paths and
existing data are unchanged. Once enabled in a future slice, rollback must
stop admissions, drain active sessions before shared dependencies, and retain
pending billing receipts. No compatibility shim or parallel ledger is added.

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
use no paid keys and no external service. Run the full application lane as well
as the existing authentication and HTTP audio regressions after changes to the
shared connection-auth boundary.

Verified after the third review fixes on 2026-10-01:

- `.venv/bin/pytest tests/realtime --ignore=tests/realtime/test_wire.py -q --tb=short`:
  176 passed.
- `.venv/bin/pytest -m app -q --tb=short`:
  1,585 passed, 4,271 deselected, with local socket permission. This lane also
  includes the two wire tests. Its 147 warnings are dependency/API deprecations.

Ruff lint and format checks passed for all 26 touched Python files, and
whitespace checks passed. The dependency lock was validated in the initial
implementation and is unchanged by these fixes. No live OpenAI requests,
database migrations or production Redis tests were needed for this deliberately
inactive transport slice.
