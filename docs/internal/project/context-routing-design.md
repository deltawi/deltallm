# Context-capacity routing decision

Status: accepted for the context-routing rollout.

## Decision

Context-capacity routing is a route-group policy owned by `src/router`. The published
PostgreSQL route policy is the durable source of truth for dynamic groups; the typed
startup configuration is the source of truth for static groups. Runtime generations
contain immutable policy and deployment snapshots built off the request path.

For chat and embedding requests, preflight attaches one request-local token-demand
value after hooks and guardrails have produced the final authorized payload. Candidate
planning evaluates that demand against deployment `model_info` in memory. It does not
perform SQL, Redis, filesystem, or provider I/O beyond the routing-state reads already
used by candidate planning.

The policy supports two modes:

- `eligible-only` removes deployments that are known to be too small while preserving
  the configured routing strategy's order.
- `smallest-sufficient` additionally orders sufficient deployments by ascending usable
  capacity, preserving strategy order within an equal-capacity tier.

Unknown capacity follows the route group's explicit `unknown_capacity` setting. Legacy
non-positive `max_tokens`, `max_input_tokens`, and `max_output_tokens` values are read as
unknown during the compatibility window; new admin writes must use positive integers or
omit the fields.

## Fallback and failure behavior

A provider-classified context-window error and a local `context_capacity_exceeded`
decision share the existing `context_window_fallbacks` policy. A local rejection tries
eligible context-fallback groups before any provider call. General fallback ordering and
provider retry classification remain unchanged.

The initiating route group's context block remains authoritative while traversing those
fallback groups, including when a fallback target has no context block of its own.

The gateway returns the existing sanitized `context_length_exceeded` invalid-request
error only when the primary route group is known to be insufficient and no configured
context fallback is eligible. Unknown-capacity exclusion and health/cooldown exhaustion
retain their existing distinct failure behavior. A local capacity rejection never marks
a provider unhealthy.

Request mutation, including later MCP phases, invalidates request-local candidate plans.
The selected deployment and all fallback candidates are therefore re-evaluated against
the latest token demand before an attempt. No retry is added around MCP tools or other
side effects.

A successful pre-provider context fallback is counted as a fallback in simulation and in
the bounded runtime event journal. Its event has `from_deployment: null` because no primary
deployment was attempted.

## Policy lifecycle and tenant scope

Route-policy authorization and tenant scoping remain owned by the existing admin service
and repository. Omission preserves the currently stored context block; explicit
`context: null` deletes it. A saved draft is a complete effective document, so publishing
that draft replaces the published policy exactly.

The editor must reconstruct an explicit deletion tombstone when a saved draft omits a
context block that is still present in the published version. This keeps direct document
publication semantically equivalent to publishing that saved draft while retaining
opaque server-owned fields.

## Alternatives considered

- Sending every request to the largest instance was rejected because it discards the
  cost and latency benefit of smaller deployments.
- Waiting for providers to reject oversized requests was rejected because it consumes
  provider capacity and adds a failed network round trip when metadata is authoritative.
- A second fallback implementation inside request handlers was rejected. Local context
  rejection enters the existing failover-owned context-fallback chain instead.
- Persisting editor-only tombstones in PostgreSQL was rejected because drafts are
  authoritative effective documents, not patch logs.

## Capacity and latency impact

Candidate evaluation is linear in the already bounded number of deployments in the
primary and configured fallback groups. Context fallback groups are planned only after a
local context rejection or a provider-classified context failure. Request-local plan
caching prevents duplicate routing-state reads during failover and microbatch planning.
The change adds no datastore or network round trip to a successful primary selection.

## Migration and compatibility

The policy is opt-in, so groups without a `context` block retain previous routing.
Deployment metadata may be rolled out independently. Missing and legacy non-positive
capacity values remain unknown, governed by `unknown_capacity`.

During the compatibility window, startup configuration and unchanged persisted metadata
continue to load. Admin create/update rejects newly introduced non-positive values, and
the UI renders legacy non-positive values as empty so the next successful edit removes
the legacy sentinel instead of failing an unrelated update.

The compatibility path can be removed after a documented release has normalized stored
metadata and operators have had one full deprecation window. Removal requires an upgrade
fixture proving no supported prior-release configuration contains non-positive sentinels.

## Rollback

Removing the route group's `context` block disables context-capacity filtering without a
deployment restart. Rolling back the application is safe because capacity metadata is
additive and older versions ignore it. If local preselection must be disabled while the
policy remains stored, publish a version without `context`; existing provider-classified
context fallbacks continue to operate.
