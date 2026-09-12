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

All ordinary HTTP paths and methods share this allocation, including inference
aliases, uploads, UI/API requests, and unknown paths. This prevents a different
route spelling from bypassing admission. Exact GET/HEAD health and metrics paths
(`/health`, `/health/liveliness`, `/health/readiness`, `/metrics`, including trailing
slashes) use the separate finite health allocation with no waiting. These paths
do not buffer request bodies. Diagnostics such as `/health/deployments` use the
ordinary allocation. Keep operational endpoints private as described in
[Health, diagnostics, and metrics](../api/health.md).

## Bound uploads and retained bytes

After admission, the gateway buffers an ordinary request body before calling
inner middleware. It checks both declared `Content-Length` and actual bytes,
including chunked uploads, and replays the complete body once. Later receive
calls preserve disconnect notifications. `body_timeout_seconds` limits this
upload stage; it does not impose a total stream deadline.

`max_body_bytes` limits one body; `max_buffered_bytes` limits the sum of raw body
bytes charged to admitted application lifetimes. Charges remain until application
cleanup finishes, even after the body has been handed to the route. If concurrent
uploads exhaust the shared byte budget, another upload is rejected before
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

Capacity errors include `Retry-After: 1`. HTTP/1 rejection responses close the
connection so an unread upload cannot be reused as another request. Client retries
should respect the delay and add jitter. A local rejection does not call a provider
or record a provider failure. Rejections before authentication do not invoke
request-failure persistence. Required audit and accounting still apply to admitted
requests through their existing lifecycle.

These allocations are local to one process. For three one-process pods with a
limit of 100, the configured ordinary-work ceiling is 300 application lifetimes;
uneven traffic, cleanup, byte pressure, and downstream limits can reduce admitted
work. This arithmetic does not establish sustainable throughput. Authenticated
organization preflight and model/tier/budget admission retain their separate
policies. Validate overload, streaming, cancellation, and dependency failure under
the same pod and database settings before choosing a production limit.

The lifecycle follows the [ASGI HTTP protocol](https://asgi.readthedocs.io/en/stable/specs/www.html):
body frames and disconnect notifications are distinct from the application task's
completion. The gateway keeps admission ownership until that task exits.
