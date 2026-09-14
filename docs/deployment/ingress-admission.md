---
title: Gateway ingress admission
description: Bound HTTP work and request-body buffering before authentication and caching.
status: experimental
audience: operators
---

# Gateway ingress admission

Ingress admission limits how much HTTP work one API process accepts before
authentication, cache lookup, and route handling. It is disabled by default.
Enable it in a controlled environment and qualify the limits against your request
sizes, stream durations, database capacity, and required accounting workload.
The defaults are allocation settings, not a measured concurrency rating.

## Configure the process budget

Set these startup settings under `general_settings` in `config.yaml`, or under
`config.general_settings` in Helm values:

```yaml
general_settings:
  gateway_ingress_enabled: true
  gateway_ingress_max_active: 100
  gateway_ingress_max_waiters: 0
  gateway_ingress_queue_timeout_ms: 10
  gateway_ingress_max_body_bytes: 33554432
  gateway_ingress_max_buffered_bytes: 67108864
  gateway_ingress_body_timeout_seconds: 10
  gateway_ingress_health_max_active: 4
  gateway_ingress_control_max_active: 16
  gateway_ingress_control_max_buffered_bytes: 67108864
```

Restart API processes after changing these fields. The dynamic configuration API
rejects changes to them, including adding an explicit default that would override
an environment setting. Explicit YAML/DB settings take precedence over the
corresponding `DELTALLM_GATEWAY_INGRESS_*` environment variables. Helm supplies
explicit values, so use Helm overrides for chart deployments.

`max_active` bounds admitted application lifetimes. A permit covers upload,
authentication, response generation, the full stream, and application cleanup.
It remains owned while cleanup runs after a disconnect or final response frame.
This keeps retained work bounded when a client leaves before accounting finishes.
No detached task is created to release permits.

Waiting requests consume no application body buffer and enter neither
authentication nor caching. `max_waiters: 0` rejects immediately when active
capacity is full. A positive value permits that many queued requests, each for
at most `queue_timeout_ms`, subject to event-loop scheduling. Cancelling a waiter
removes it from the queue.

POST inference routes use the inference allocation: chat/completions, completions,
responses, messages, embeddings, images/generations, audio/speech,
audio/transcriptions, and rerank, with or without the `/v1` prefix and trailing
slash. Other paths and methods, including UI/API requests, diagnostics, and
unknown routes, use `control_max_active` with no waiting and a separate
`control_max_buffered_bytes` budget. Control requests cannot consume the reserved
inference permits or raw body bytes. Downstream dependency pools still impose
shared limits.

Exact GET/HEAD health and metrics paths (`/health`, `/health/liveliness`,
`/health/readiness`, `/metrics`, including trailing slashes) use the finite health
allocation with no waiting or body buffering. Keep operational endpoints private
as described in [Health, diagnostics, and metrics](../api/health.md).

## Bound uploads and retained bytes

After admission, the gateway buffers an ordinary request body before calling
inner middleware. It checks both declared `Content-Length` and actual bytes,
including chunked uploads, and replays the complete body once. Later receive
calls preserve disconnect notifications. `body_timeout_seconds` limits this
upload stage; it does not impose a total stream deadline.

`max_body_bytes` limits one body; `max_buffered_bytes` limits the sum of raw body
bytes charged to inference application lifetimes. Control work has its own byte
budget. Charges remain until application
cleanup finishes, even after the body has been handed to the route. If concurrent
uploads exhaust their allocation’s byte budget, another upload is rejected before
authentication. Partial buffers are released on rejection, timeout, disconnect,
or cancellation.

The byte budget is not a process RSS limit. Buffer conversion temporarily copies
one body; Python allocation overhead, parsed objects, provider responses, and
HTTP-server buffers also need memory. Set the pod memory budget with that headroom
and measure it. This ingress limit also applies to file and audio uploads; choose
an appropriate maximum for the upload APIs you expose. Configure connection,
header, and transport limits at the HTTP server and edge as well.

## Handle local rejection

| Status | Error code | Cause |
| --- | --- | --- |
| 503 | `gateway_ingress_full` | Active/waiter allocation full or queue deadline reached |
| 503 | `gateway_ingress_buffer_full` | Aggregate body byte allocation exhausted |
| 413 | `gateway_request_body_too_large` | Declared or observed body exceeds the request limit |
| 408 | `gateway_request_body_timeout` | Upload stage exceeded its deadline |
| 400 | `invalid_content_length` | Malformed, repeated, or inconsistent length |

The table lists OpenAI-style error codes. `/messages` and `/v1/messages` use the
canonical Anthropic error envelope and status-based error type (for example,
`overloaded_error` for 503). Capacity errors include `Retry-After: 1`. HTTP/1 rejection responses close the
connection so an unread upload cannot be reused as another request. Client retries
should respect the delay and add jitter. A local rejection does not call a provider
or record a provider failure. Rejections before authentication do not invoke
request-failure persistence. Required audit and accounting still apply to admitted
requests through their existing lifecycle.

These allocations are local to one process. For three one-process pods with a
limit of 100, the configured inference ceiling is 300 application lifetimes;
uneven traffic, cleanup, byte pressure, and downstream limits can reduce admitted
work. This arithmetic does not establish sustainable throughput. Authenticated
organization preflight and model/tier/budget admission retain their separate
policies. Validate overload, streaming, cancellation, and dependency failure under
the same pod and database settings before choosing a production limit.

The lifecycle follows the [ASGI HTTP protocol](https://asgi.readthedocs.io/en/stable/specs/www.html):
body frames and disconnect notifications are distinct from the application task's
completion. The gateway keeps admission ownership until that task exits.

## Bound authentication fallback

Authentication fallback is always bounded, independently of the optional ingress
switch. These startup settings use the same explicit configuration precedence,
with `DELTALLM_AUTH_FALLBACK_*` environment equivalents:

```yaml
general_settings:
  auth_fallback_max_active: 8
  auth_fallback_max_waiters: 32
  auth_fallback_queue_timeout_ms: 10
  auth_fallback_timeout_seconds: 0.5
  auth_fallback_cache_timeout_seconds: 0.1
  auth_fallback_cache_max_bytes: 131072
```

One application-owned `KeyService` owns the lookup tasks and queue. Concurrent
misses for the same hashed key share one lookup; different keys share the bounded
execution allocation. Both the task registry and waiting callers are capped at
`max_active + max_waiters`. Each caller receives a separate auth object. There is
no process-local result cache that could extend revocation staleness.

A warm key keeps the existing budget of one Redis read and zero key SQL queries.
A cold key adds one bounded-row key query and at most one best-effort Redis write;
concurrent misses share the latter operations. Organization lifecycle checks retain
their existing authoritative policy and operation budget. Cache entries must have
the full serialized auth schema, match the hashed identity, fit the byte limit,
and pass absolute expiry checks. Missing, malformed, or unavailable cache entries
use the bounded database path. Cache write failures do not reject otherwise valid
database authentication. Redis TTL never exceeds the key’s remaining lifetime.

When fallback is full, overdue, or unavailable, the gateway returns a local 503
with `Retry-After: 1`. It does not try another identity mechanism or call a provider.
Invalid or expired credentials still return 401. Bearer credentials are limited
to 8,192 characters before key, JWT, or custom authentication. The lookup deadline begins after
the separately bounded cache read, so allow up to both budgets plus scheduling
and existing policy work when interpreting client latency.

A caller deadline does not establish a native PostgreSQL deadline. An overdue
lookup keeps its execution slot until the underlying work finishes; its result
cannot authorize or fill the cache. This prevents repeated client timeouts from
creating unlimited SQL. Shutdown closes lookup admission, invalidates pending
results, and cancels/observes owned tasks before centrally owned clients close.
The foreground database allocation separately owns native queries and enforces
pool, statement, and lock deadlines. During shutdown its slot can outlive the
cancelled auth task, and stays occupied until native work finishes. Database
exhaustion becomes the same local auth 503; additional misses cannot bypass the
foreground allocation. Cache invalidation continues to use the separate control
database allocation. Configure both budgets before increasing pod counts.

Existing Redis `key:v4` TTL and cross-replica invalidation delivery remain in use.
Local invalidation also fences pending lookup results. This does not establish a
new distributed cache-fill/revocation ordering protocol: in-flight cache writes
retain the existing cross-replica race and TTL/invalidation contract.

## Observe and qualify the allocations

The [local ingress/authentication measurement](ingress-measurement.md) records
the controlled overload comparison, dependency counts, raw samples, and limits
of that evidence.

Use `deltallm_ingress_active`, `deltallm_ingress_waiters`, and
`deltallm_ingress_buffered_bytes` by the fixed `allocation` label (`inference`,
`control`, `health`). `deltallm_ingress_queue_seconds` and
`deltallm_ingress_rejections_total` expose waiting and rejection outcomes.
`deltallm_auth_fallback_tasks` includes overdue owned work;
`deltallm_auth_fallback_callers` counts live callers. The `events_total` and
`seconds` metrics under the same auth prefix expose cache/admission outcomes and
caller/execution duration without credential, tenant, or exception-text labels.

For each API process, the example permits at most 100 inference lifetimes, 16
control lifetimes, and 4 health lifetimes, with up to 128 MiB of charged raw bodies
across the inference and control allocations. Auth fallback can run at most 8
owned lookups and retain at most 40 tasks/callers. Multiply by processes per pod
and peak pods, including rolling-update surge, when checking shared dependencies.
These are configured ceilings; they are not throughput promises. Measure warm and
cold keys, Redis failure/recovery, database stalls, mixed control traffic, long
streams, disconnects, and accounting under the actual pod resources.

Ingress owns admission before dependencies because route-local semaphores cannot
bound uploads or authentication work. Separate control/health allocations preserve
inference capacity during unrelated traffic. Auth coalescing is limited to pending
work to preserve the existing cache invalidation contract. These choices add no
per-key locks or unbounded overflow queues. Roll out ingress explicitly, observe
rejections and latency, and qualify native dependency limits before increasing the
process or pod ceilings.
