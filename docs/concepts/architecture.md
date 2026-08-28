# Architecture

DeltaLLM separates latency-sensitive inference traffic from operator and background work.
PostgreSQL owns durable control-plane state, Redis provides fallible shared coordination and
caching, and process memory holds bounded runtime snapshots that can be rebuilt.

## System boundaries

```text
                                  ┌──────────────────────────────┐
OpenAI-compatible clients ───────▶│ API replicas                 │
                                  │                              │
Operators ───────────────────────▶│ Data plane: inference        │──────▶ LLM providers
                                  │ Control plane: Admin API/UI  │──────▶ MCP/webhooks/email
                                  └───────────┬─────────┬────────┘
                                              │         │
                                  durable     │         │ coordination/cache
                                              ▼         ▼
                                        PostgreSQL    Redis
                                              ▲
                                              │ claims, leases, results
                                  ┌───────────┴───────────────┐
                                  │ Batch/background workers │──────▶ Object storage
                                  └───────────────────────────┘
```

### Data plane

The data plane serves synchronous and streaming inference endpoints such as chat,
completions, Responses, embeddings, images, audio, and reranking. It authenticates the
caller, applies policy, selects a deployment, translates the request through a provider
adapter, and records required operational and economic effects.

The data plane is designed to keep control-plane work out of the request path. Admin UI
queries, reports, migrations, maintenance, and batch execution should not consume the
request path's bounded provider or queue capacity.

### Control plane

The control plane includes the Admin API and UI, configuration and provider management,
identity and membership management, reports, health diagnostics, and worker orchestration.
The Playground is the intentional UI feature that sends data-plane requests.

## State ownership

| Component | Owns | Failure expectation |
| --- | --- | --- |
| PostgreSQL | Identities, memberships, keys, policies, model configuration, spend and audit metadata, batch state | Durable source of truth. Production migrations run once, before API rollout. |
| Redis | Auth and response caches, shared rate/admission state, routing health and cooldowns, invalidation signals | Fallible infrastructure. Each feature declares whether it falls back, degrades, or denies requests. |
| Process memory | Request-local state and bounded immutable runtime snapshots | Disposable and rebuilt from configuration or durable state. It is never the only cluster-wide record. |
| Object storage | Shared batch input/output artifacts | Required for batch workers that can run on different hosts or pods. Local disk is evaluation-only staging. |
| Provider/control HTTP clients | Bounded connections to providers, MCP servers, webhooks, identity providers, and email services | Timeouts, concurrency, retries, and redirects are bounded by integration policy. |

Redis Pub/Sub accelerates invalidation but is not a durable event log. A missed notification
must be recoverable from PostgreSQL-backed state, generation checks, or reconciliation.

## API process startup

Each API process constructs its long-lived dependencies in an ordered lifespan:

1. Infrastructure: configuration, PostgreSQL, Redis, dynamic configuration, HTTP clients,
   repositories, and provider adapters.
2. Audit and email services.
3. Authentication and identity services.
4. Routing state, provider health, failover, and response cache runtime.
5. Runtime policy services such as prompts, guardrails, budgets, MCP governance, and
   invalidation.
6. Batch services and supervised workers enabled for that process role.

Shutdown unwinds those owners in reverse order. Long-lived clients and supervised tasks are
created during bootstrap rather than per request.

## Routing snapshots and multiple replicas

Model deployments, route groups, callable-target grants, and related policy are assembled
into a runtime routing generation. A request pins one generation so a concurrent reload
cannot give it a mixture of old and new policy.

Production correctness assumes multiple API replicas. Durable policy changes live in
PostgreSQL and are reconciled across processes; Redis can accelerate coordination but does
not replace the database. See [Routing and failover](../features/routing.md) for configuration
and [router state schema cutover](../deployment/router-state-schema-cutover.md) for rollout
details.

## Batch and background execution

Batch work is a durable asynchronous state machine. Workers claim PostgreSQL records with
leases and fencing, heartbeat while they own work, and persist transitions so another worker
can recover after process loss. Multi-replica deployments use shared object storage rather
than one container's local filesystem.

See [Batch API and production setup](../features/batching.md) for supported workloads,
storage, scheduler behavior, and worker topology.

## Deployment consequences

- Docker Compose is suitable for development and evaluation; exposed local PostgreSQL and
  Redis services are not a production boundary.
- Production DDL must run through one coordinated, retry-safe migration workflow before API
  replicas start the new application version.
- Public liveness should reveal only coarse process state. Detailed health and metrics need
  authentication or documented network protection.
- Database, Redis, provider, worker, and HTTP connection capacity must be budgeted across the
  maximum number of process replicas.

Continue to [Life of a request](request-lifecycle.md) for the inference path and the
[deployment overview](../deployment/index.md) for operational guidance.
