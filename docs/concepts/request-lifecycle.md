# Life of a Request

DeltaLLM evaluates inference requests in a deliberate order so transformations cannot bypass
authorization, cache hits cannot bypass policy, and streaming resources remain owned until
the response finishes.

This page follows the text-generation path. Other inference endpoints reuse the same core
invariants but may have endpoint-specific validation or provider translation.

## Request sequence

```text
HTTP ingress
  │
  ├─ authenticate bearer token and resolve tenant scope
  ├─ acquire cheap model-agnostic preflight capacity
  ├─ resolve prompts and run pre-call hooks/guardrails
  ├─ validate the transformed payload
  ├─ authorize the final model or route group
  ├─ enforce concurrency, budget, and rate admission
  ├─ look up the tenant-scoped response cache
  ├─ select a deployment and execute bounded failover
  ├─ translate provider response or stream frames
  └─ finalize usage, spend, audit, callbacks, and resource leases
```

## 1. Authentication and scope

The proxy API requires a bearer token. DeltaLLM accepts the configured master key, a hashed
virtual key, and—when configured—JWT or custom authentication. Virtual-key cache lookup can
use Redis, but PostgreSQL remains the durable source. Authentication is never granted merely
because Redis is unavailable.

The authenticated identity is converted into a runtime scope containing the user, API key,
team, and organization identifiers that are present. If the request belongs to an
organization, DeltaLLM also requires that organization to be active. See
[Tenancy and access](tenancy-and-access.md).

## 2. Cheap preflight capacity

Before prompt resolution or other expensive policy work, the gateway acquires a bounded,
model-agnostic preflight slot. This protects shared control dependencies from overload. It
does not replace final model authorization, budget enforcement, rate limits, or concurrency
admission.

## 3. Prompt and policy transformation

If the request references a registered prompt, DeltaLLM resolves and renders it using the
caller's scope. Pre-call callbacks and guardrails can then transform messages, metadata, or
other request fields.

Because those components can change policy-relevant data, DeltaLLM validates the transformed
payload and authorizes its final model after the transformation. Invalid transformed data is
rejected before routing.

## 4. Authorization and final admission

Model visibility is evaluated against callable-target bindings, scope policies, legacy
compatibility constraints, and organization-tier policy when enabled. DeltaLLM then applies
bounded parallel admission, budget enforcement, and hierarchical rate limits to the final
payload.

The cheap preflight slot is released after final admission. Rate and concurrency leases stay
owned until the final response frame, error, cancellation, or client disconnect.

## 5. Cache lookup

For supported endpoints, response-cache lookup happens only after authentication and the full
preflight sequence. Cache identity includes the caller's applicable scope and the request
dimensions that affect the response.

A cache hit still records the configured usage, spend, metrics, and request-log effects. A
miss continues to routing. See [Caching](../features/caching.md) for controls and backends.

## 6. Routing and provider execution

The request pins the current routing generation, resolves the requested model or route group,
and selects an eligible deployment. A provider adapter owns provider-specific authentication,
payload translation, response translation, and error mapping.

Retries and failover are bounded by the configured deadline and classification rules. The
gateway does not retry arbitrary side effects. For a stream, failover is allowed only before
the first validated downstream chunk; after bytes reach the client, the gateway preserves the
stream outcome instead of replacing it with another provider response.

See [Routing and failover](../features/routing.md) for strategies and failure behavior.

## 7. Completion and accounting

Success, provider failure, timeout, cancellation, and disconnect all converge on resource
cleanup. Depending on the endpoint and configuration, DeltaLLM records:

- token and request usage;
- customer and provider cost attribution;
- request and route-decision metrics;
- required audit metadata and permitted payloads;
- callbacks and post-call guardrails;
- provider health outcomes; and
- response-cache writes for successful cacheable responses.

Operational pipelines can report degraded, delayed, or unavailable state; they should not be
rendered as zero. Monitor the gateway using the [observability guide](../features/observability.md)
and inspect audit behavior in [Audit log](../features/audit-log.md).

## Error boundaries

- Authentication and inactive-organization failures stop before provider routing.
- A post-transformation validation or authorization failure cannot fall back to another
  provider.
- Local capacity rejection is not classified as provider failure.
- Provider errors are normalized into sanitized, stable gateway errors.
- Once streaming response bytes are sent, a later failure terminates that stream and does not
  start a replacement response.
