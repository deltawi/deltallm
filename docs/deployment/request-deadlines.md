# Request deadlines and bounded background work

`router_settings.timeout` is the total inference timeout in seconds (default 600).
It begins before the ingress queue, upload, authentication, prompt resolution and
guardrails. Cache hits, routing, retries/backoff, MCP execution, normal accounting
and downstream response delivery consume the same monotonic budget. It applies to
chat and compatible aliases, embeddings, rerank, images and audio. Health and admin
requests do not use the inference deadline.

When routing is reached, a route's shorter timeout is measured from inference ingress and shortens the
remaining request budget, including finalization. A route cannot extend the global
budget. Reloads affect subsequent requests; an active request keeps its pinned
routing generation and deadline. A cache hit uses the global ingress budget without
creating a provider execution plan. Set the global timeout high enough for the
longest supported modality before choosing shorter route limits.

HTTP connect, pool, write and read limits remain explicit and are capped by remaining
time. A provider read timeout measures idle time between received chunks; frequent
chunks do not extend the total stream lifetime. Local expiry returns HTTP 408 with
`request_deadline_exceeded` before response headers. The messages compatibility API
keeps its native error envelope. After headers, an incomplete stream is aborted;
there is no replacement response, success terminal marker or provider retry.

Normal finalization is awaited in both outbox and legacy spend modes. Legacy writes
no longer escape the request as fire-and-forget tasks, and a failed write produces
the existing persistence-unavailable error. This does not make legacy accounting
transactional or recoverable: use the [durable spend rollout](spend-recovery.md)
for production. Cancellation still gives resource cleanup its existing short,
shielded grace. The deadline can therefore expire before cleanup finishes; that
grace cannot start a replacement provider attempt. PR6's durable operation remains
available for recovery when a provider outcome or receipt is ambiguous.

## Optional callbacks

Callback delivery is best effort and has finite per-process capacity. Required
audit, spend, notifications and business webhook delivery keep their existing
durable owners. The manager snapshots accepted callback payloads, bounds recursive
size/depth before copying, and discards oversized/full work without delaying an
inference response. Request exception objects and their tracebacks are not retained;
failure callbacks receive a safe error and the logging payload's error information.

| Startup setting | Default | Meaning |
| --- | ---: | --- |
| `callback_max_pending` | 128 | Total queued and running deliveries |
| `callback_max_concurrency` | 4 | Simultaneous async deliveries and maximum synchronous adapter threads |
| `callback_max_payload_bytes` | 262144 | Maximum recursively measured payload before copying |
| `callback_max_bytes` | 8388608 | Delivery payload allocation, including a three-times copy allowance |
| `callback_timeout_seconds` | 5 | Per-delivery time including slot wait; also the inline hook limit |
| `callback_shutdown_seconds` | 5 | Total optional delivery/thread drain grace |

The synchronous adapter executor has a separate allocation of the same
`callback_max_bytes`; it retains that charge if a timed-out delivery stops waiting
while its thread continues. A callback list accepts at most 32 handlers per outcome.
The manager retains at most 64 handlers across active and retired configurations.
Retired SDK clients close under the same blocking allocation and per-handler lock;
queued deliveries cannot reopen them. A stuck or failed close keeps its handler
allocation. Further registrations are rejected with a resource metric and warning
until cleanup succeeds or the process restarts. Cleanup shares the delivery shutdown
grace and never starts an unbounded collection of close tasks.

OpenTelemetry uses a handler-owned provider and synchronous span processor inside
this executor, with a five-second exporter timeout and explicit provider shutdown.
S3 uses one pooled connection per handler, five-second connect/read limits, one
attempt and explicit client close. The existing Langfuse v2 adapter uses one SDK
consumer configuration, five-second requests, one retry limit, serialized flush,
and explicit shutdown; its SDK may create ingestion/media threads in addition to
the gateway executor. SDK internals and custom integrations still need their own
memory/connection limits when operators enable them.
Configured custom integrations are trusted code: async implementations must yield
to the event loop and propagate cancellation, and synchronous implementations must
use the manager's bounded `run_blocking` facility rather than create their own
tasks/executors. Pre-call hooks retain their existing ordered mutation behavior
inside the request deadline. They are distinct from required guardrail policy.

Callback errors or overflow do not change a successful provider response. Delivery
does not retry integrations. Capacity remains charged until actual task/thread
completion, including cancellation-resistant extensions. Shutdown stops admission,
waits for its grace, then cancels pending async/queued thread work. Python cannot
kill an already-running thread; a permanently blocked extension can retain a bounded
slot until process termination. Monitor this condition and fix or disable the
extension before restarting. Process termination/drain coordination remains PR8 scope.

## CPU guardrails

Presidio inspection and anonymization run in a bootstrap-owned executor shared by
registered Presidio guardrails, including replacements loaded by configuration.
Each instance serializes access to its optional NLP engine. Synchronous callback
adapters have their own allocation and cannot consume guardrail execution slots.

| Startup setting | Default | Meaning |
| --- | ---: | --- |
| `guardrail_max_concurrency` | 2 | Worker threads per process |
| `guardrail_max_pending` | 8 | Queued plus running inspections |
| `guardrail_max_bytes` | 33554432 | Retained payload allocation, including a four-times transformation allowance |
| `guardrail_timeout_seconds` | 5 | Maximum wait for an inspection, including queue time |
| `guardrail_shutdown_seconds` | 5 | Executor drain grace |

The earliest request/guardrail timeout stops waiting. A running inspection keeps
its slot and byte charge until completion; late results are discarded. Full,
oversized or unavailable execution returns local HTTP 503
`gateway_work_unavailable` and does not call or penalize the provider. Required
guardrails are never skipped because execution capacity is exhausted.

All callback/guardrail limits above are startup-only. Explicit file values override
environment defaults. Environment names use `DELTALLM_` plus the uppercase setting.
Dynamic changes, including adding an explicit default that changes environment
precedence, return `restart_required` before persistence. Update and roll the
deployment to change these allocations.

## Capacity and observation

For each API process the defaults allocate at most six executor threads, 128
callback deliveries, four unfinished synchronous callbacks and eight guardrail
inspections. Payload allocations total at most `8 + 8 + 32 = 48 MiB`, in addition
to ingress buffers, Python task/object overhead, thread stacks, NLP engines,
integration clients and their own SDK buffers. These are payload budgets, not an
RSS limit. Offloading keeps yielding/native CPU work off the event loop. Threads do not
guarantee parallel speedup for Python code limited by the GIL, and arbitrary C
extensions that hold the GIL require process isolation for strict latency bounds.
The fallback email scan also avoids retrying every suffix of a long local part.

Multiply by every process and overlapping API replica that constructs these owners.
The existing example peak of 25 API processes therefore allocates up to 150 threads
and 1200 MiB of payload capacity across the deployment. Include worker roles only
if they construct these runtimes. This change adds no PostgreSQL, Redis or provider
HTTP pool and no data-plane SQL/Redis/network call for deadline propagation.

- `deltallm_request_deadline_expirations_total{response}` distinguishes expiry
  before and after response headers.
- `deltallm_bounded_work_in_flight{allocation}` and
  `deltallm_bounded_work_bytes{allocation}` include unfinished cancelled work.
- `deltallm_bounded_work_rejections_total{allocation,reason}` reports full,
  oversized and closed allocations; `callback_resources` additionally exposes
  exhausted handler capacity and failed SDK cleanup.
- `deltallm_bounded_work_seconds{allocation,outcome}` records thread work through
  actual completion, including queue wait.
- `deltallm_callback_outcomes_total{integration,outcome}` reports optional delivery
  outcomes. Custom handlers use the single `custom` integration label.

Investigate sustained saturation and callback drops before increasing limits.
Compare event-loop lag, received throughput, all-response latency, timeout/rejection
counts and retained-work recovery under the same offered workload. These defaults
are bounded allocations; PR10 still supplies production throughput qualification.

[PR7 measurements](../project/benchmarks/request-work-2026-09-15/README.md) include
raw HTTP and component samples, callback retention, event-loop lag, tail latency,
dependency counts and post-shutdown durable drain. They are local evidence, not a
Kubernetes capacity certificate.
