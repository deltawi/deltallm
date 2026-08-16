# DeltaLLM Engineering Rules

This file is the repository-wide engineering contract for humans and coding agents. Read it before changing code. More local rules may tighten these requirements but may not weaken them.

`MUST`, `MUST NOT`, and `NEVER` are requirements for new code and materially touched paths unless explicitly marked repository-wide. `SHOULD` is the default; depart from it only with a written reason, tests, and an explicit trade-off in the change summary.

These requirements govern new and modified behavior. Known exceptions in Section 19 do not force unrelated work into a repository-wide refactor: untouched debt may remain, but a change MUST NOT add another dependency on it or make it worse. When directly modifying a violating path, make the changed behavior compliant or document the smallest bounded migration step that remains and why it cannot safely fit the current scope.

If a requested implementation conflicts with these invariants, stop and explain the conflict, then propose the smallest compliant design. Do not silently create a second config path, lifecycle, policy implementation, storage abstraction, or deployment mechanism.

## 1. Product DNA

DeltaLLM is a self-hosted, multi-tenant, OpenAI-compatible LLM gateway and control plane. Applications keep the OpenAI request format while DeltaLLM owns provider credentials, model routing, policy enforcement, caching, failover, usage, spend, batch execution, MCP access, and operations.

Preserve these properties in this order:

1. Tenant isolation, authorization, secret safety, and economic correctness.
2. OpenAI-compatible behavior and stable operator-facing contracts.
3. Correct distributed behavior under retries, failures, and multiple replicas.
4. Low and predictable gateway overhead, especially time to first token.
5. Explicit, observable degradation instead of silent corruption or false health.
6. A maintainable control plane that can grow without duplicating policy.

Latency-sensitive synchronous and streaming inference endpoints—such as chat, responses/text, embeddings, image, and audio generation—form the **data plane**. Admin APIs, configuration, reporting, the UI, migrations, files/batch orchestration, health diagnostics, and background workers are the **control plane**. Control-plane request handling, reporting, UI work, migrations, and maintenance MUST NOT run inline on inference paths or compete for their bounded HTTP/queue capacity without an explicit allocation. Policy snapshots, admission, required accounting, and required audit are data-plane dependencies and need strict latency/capacity budgets. The Playground is the intentional UI exception that calls data-plane endpoints.

## 2. State and runtime model

Assume production runs multiple API replicas and separate batch workers. A solution that is correct only in one Python process is not production-correct.

| Component | Role | Non-negotiable rule |
| --- | --- | --- |
| PostgreSQL | Durable source of truth for identities, policy, configuration, billing, audit metadata, and batch state | Enforce durable invariants with constraints, transactions, migrations, and idempotency |
| Redis | Shared cache and distributed coordination for routing, admission, leases, cooldowns, and invalidation | Treat it as bounded, fallible infrastructure; declare outage behavior per feature |
| Process memory | Fast, bounded L1 snapshots and request-local state | Everything MUST be size-bounded, disposable, and reconstructible |
| Object storage | Shared durable batch/file artifacts | Multi-pod workloads MUST NOT rely on one container's local disk |
| Providers/MCP/webhooks | External, slow, partially failing systems | Bound time, concurrency, retries, payloads, redirects, and side effects |
| React UI | Operator control plane | Backend authorization is authoritative; operational unknowns are never rendered as zero |

Redis or process memory MUST NOT be the sole durable copy of billing, audit, identity, configuration, or batch work. Pub/sub is a wake-up signal, not a durable event log.

## 3. How an autonomous coding agent works here

### Before editing

1. Inspect `git status` and preserve unrelated or untracked user work.
2. Trace the complete affected path: HTTP contract, authorization, service/policy, repository or provider, cache/invalidation, config, metrics, UI type, docs, and deployment surfaces.
3. Read adjacent tests and the relevant public docs. Treat plans and TODOs as design intent, not proof that code is implemented.
4. State the invariant, source of truth, tenant scope, failure mode, retry boundary, and latency impact of the change.
5. For a cross-cutting change, make a short plan with independently verifiable slices before coding.

### While editing

- Make the smallest coherent vertical change. Do not mix opportunistic cleanup with behavior changes.
- Reuse an existing abstraction when it has the same semantics. If it does not, improve it deliberately rather than adding a look-alike path.
- Keep one owner for every policy and side effect. Search for all callers before changing a shared contract.
- Use typed boundaries and explicit state transitions. Avoid compatibility-by-reflection, magic attribute checks, raw dictionaries, and boolean soups.
- Add or update tests with the implementation, not afterward.
- Do not weaken a test, lint rule, security check, timeout, or production default merely to make a change pass.
- Do not edit or weaken `RULES.md` merely to make a feature compliant. A rule change is its own explicit design decision with rationale and reviewable consequences.
- `RULES.md` is canonical. Any agent/tool-specific instruction file SHOULD point here and stay thin; it MUST NOT fork these rules into a divergent copy.

### Ask only when necessary

Proceed with reasonable, stated assumptions. Stop for direction only when ambiguity would materially alter a public contract, tenant/security semantics, money, irreversible data migration, destructive operation, or production topology.

### Before declaring completion

- Review the diff for duplicated policy, missed configuration surfaces, secrets, tenant filters, unbounded work, compatibility changes, and generated artifacts.
- Run the proportionate gates in Section 18 and report exact commands and results.
- Report known limitations and pre-existing failures honestly. Never say “passes” for a command that was not run.

## 4. Repository boundaries and dependency direction

The intended dependency direction is:

`HTTP/UI edge -> application/domain service -> repository or provider adapter -> infrastructure`

Bootstrap constructs the graph; lower layers do not reach back into FastAPI.

| Area | Responsibility |
| --- | --- |
| `src/main.py` | FastAPI construction and ordered lifespan composition only |
| `src/bootstrap/` | Construct, attach, supervise, and close long-lived dependencies |
| `src/api/v1/` and `src/api/admin/` | Transport parsing, typed contracts, auth dependencies, service invocation, response mapping |
| `src/services/`, `src/chat/`, feature packages | Use cases, orchestration, and domain policy |
| `src/db/` and feature repositories | SQL, Prisma persistence, transactions, and data mapping |
| `src/providers/` | Provider authentication, payload translation, capability handling, streaming, and error mapping |
| `src/router/` | Routing/failover policy and shared routing state |
| `src/batch/` | Durable asynchronous state machine and workers |
| `src/models/` | Typed edge DTOs; not a dumping ground for unrelated domain logic |
| `ui/src/pages/` | Route-level UI orchestration and layout |
| `ui/src/components/` and `ui/src/lib/` | Reusable UI behavior, typed domain helpers, and transport modules |

Rules:

- `src/main.py` MUST remain composition-only. Do not put feature logic there.
- Create long-lived network clients, pools, and supervised background tasks through `src/bootstrap/*`. Never create DB, Redis, HTTP, or provider clients per request or as import-time globals.
- Endpoint modules parse, authorize, call a service, and format a response. SQL, provider protocol logic, and multi-step policy do not belong in handlers.
- New lower-layer interfaces MUST NOT accept FastAPI request objects. Resolve `request.app.state` once at the HTTP edge through typed dependencies; materially touched deep `app.state` access should move toward that boundary.
- Public routes are registered through `src/api/v1/router.py`; admin routes through `src/api/admin/router.py`. Some `src/api/v1/endpoints/*` files still wrap implementations in `src/routers/*`: extend the single existing implementation or extract it into a service, but never add a third parallel route layer.
- `src/ui/routes.py` is the intended owner of the static UI bundle and SPA fallback only. Do not add admin API behavior there. Static serving is currently split with `src/main.py`; when either path is touched, consolidate toward one owner while preserving `/`, `/ui/*`, `/ui/api/*`, assets, and deep links.
- `src/domain/routing` is a compatibility facade over `src/router`; new routing implementation belongs in `src/router` unless a deliberate migration removes the facade.
- New raw SQL belongs in a repository/data-access module; materially touched endpoint/service SQL should be extracted when safe. Values MUST be parameterized and dynamic identifiers MUST come from a static allowlist.
- Do not add catch-all `utils.py`, `common.py`, or `helpers.py` modules. Name modules after the capability they own.
- Modules MUST NOT start tasks, open connections, read mutable external state, or perform network I/O at import time.

## 5. Structure and complexity guardrails

Large files already concentrate too many reasons to change. Existing size is debt, not precedent.

- A new backend production module SHOULD stay below 500 logical lines and a function below 80. A new React page/component SHOULD stay below 400 lines and own no more than one independent editing workflow.
- Do not add a new concern to a production file already over 800 lines. Extract the affected policy, persistence, execution, mapping, hook, or component seam first. A small, localized bug fix may remain localized if extraction would add more risk.
- Split by cohesive capability, not arbitrary line chunks. Each extracted unit needs a narrow typed interface and focused tests.
- Responsive desktop/mobile renderers MUST share the same domain hooks, typed view model, validation, and mutation handlers. Do not duplicate business rules.
- Do not create a second implementation during a refactor. Use a compatibility facade, migrate callers, test parity, then delete the old path in a bounded sequence.
- Prefer explicit dataclasses/Pydantic models/protocols/discriminated unions over `dict[str, Any]`, tuples with positional meaning, and new `Any`.
- Catch broad exceptions only at a request, process, worker, or integration boundary where the error is classified, recorded, and handled. Never silently downgrade authorization, budget, billing, tenant isolation, or durability failures.
- Comments explain invariants, non-obvious failure behavior, or why a trade-off exists. They do not narrate syntax or preserve dead code.

## 6. Data-plane request invariants

For text requests preserve this logical order:

`authenticate -> cheap model-agnostic preflight capacity -> resolve/mutate prompts -> hooks and guardrails -> validate transformed payload -> authorize final model -> budget/final admission -> route/acquire leases -> provider -> durable accounting/audit`

- A lightweight preflight gate protects DB/Redis capacity before expensive prompt/policy work. It MUST be cheap, bounded, model-agnostic, and released once final admission is acquired. It never replaces final model/tier authorization, budget, rate, or concurrency enforcement after mutation.
- Any component that mutates the model, messages, tools, metadata, or token count MUST run before the affected authorization/admission checks, or repeat those checks afterward.
- Cache lookup stays after authentication and full policy preflight. Cache keys include tenant/key scope and every response-affecting dimension. Change the cache-key version when semantics change.
- Do not parse or normalize the same request body independently in multiple middleware/handler layers.
- Once downstream response bytes have been sent, do not retry, fail over, or replace a stream. Streaming failover is allowed only before the first validated downstream chunk.
- Hold concurrency/admission leases until the final response frame or disconnect. Close upstream responses and release only resources actually acquired on success, error, timeout, cancellation, and client disconnect.
- Acquisition and release MUST use an acquisition flag or ownership token. Never decrement or unlock anonymous state after a failed acquire.
- A non-idempotent MCP tool, webhook, email, callback, or external side effect MUST NOT sit inside provider retry/failover scope. Retry only proven-idempotent work or work protected by a stable idempotency key and durable result.
- Provider errors are mapped to stable, sanitized OpenAI-compatible errors. Do not expose credentials, upstream bodies, internal URLs, stack details, or new topology identifiers. Preserve only the existing documented, sanitized route-decision headers unless their contract is deliberately deprecated.

## 7. Latency, async, and capacity

Gateway overhead is a feature. Every data-plane round trip consumes latency and shared capacity.

- A data-plane change that adds or changes awaited SQL, Redis, filesystem, callback, or network work MUST state the reason and planned call/latency budget. Measure before/after call counts on an existing path; a new path establishes its first checked-in regression test or benchmark.
- Never await DB or Redis calls in a loop on the request path. Use one SQL query, `MGET`, a pipeline, or a reviewed Lua script.
- Run independent bounded I/O concurrently; keep dependent work sequential. Concurrency is not permission for unbounded fan-out.
- Use negative caching only where stale absence is safe, tenant-scoped, bounded, versioned/invalidated, and observable; pair cold lookups with per-process single-flight to prevent stampedes. Every memory cache needs both a size bound and eviction policy.
- Do not fix amplification only by increasing pool sizes. Remove repeated work first, then size DB, Redis, HTTP, worker, file-descriptor, and provider capacity as one deployment-wide budget.
- Sum each per-process pool/limit across only the API/worker process roles that instantiate it at their maximum replica counts. The resulting deployment-wide totals MUST fit database, Redis, host, and provider limits with headroom.
- Use the existing shared provider and control-plane HTTP clients. Keep explicit connect, pool, write, read, per-attempt, and total-deadline bounds.
- Propagate one end-to-end deadline through queueing, backoff, attempts, providers, and MCP. Retry layers MUST NOT multiply into an uncontrolled total deadline.
- Retries are bounded, jittered, deadline-aware, classified, and limited to idempotent operations. Do not stack independent retry loops at HTTP, router, adapter, and worker layers.
- Blocking I/O and CPU-heavy work MUST NOT run on the event loop. Offload it to a bounded executor or worker with cancellation and overload behavior.
- Generic untracked `asyncio.create_task` is forbidden. Every task has an owner/registry, a bound, error observation, and shutdown drain/cancel behavior.
- Every queue and fan-out has a maximum size/concurrency plus an explicit overflow policy: backpressure, durable spill, bounded best-effort drop, or an HTTP `429`/local `503` as appropriate. Never allow an ever-growing queue.
- Streaming measurements separate queue time, time to first byte/token, full duration, disconnects, and cleanup.

For hot-path work, record a reproducible constant-arrival test with raw samples, offered and received rates, in-flight/queue slope, dependency call counts, provider time, and p50/p95/p99. Separate gateway certification with a fixed local provider mock from real-provider latency.

The current proposed release-certificate target is a 10-minute 50 RPS run of a fixed one-token non-streaming local-mock profile with at least 99.9% success, client end-to-end p95 at or below 150 ms, p99 at or below 300 ms, and no positive queue slope. It is **not enforced** until the load harness, workload definition, baseline artifact, and SLO owner are checked in and approved. Until then, 50 RPS without queue growth is the direction; per-change before/after dependency counts and latency distributions are the enforceable ratchet.

## 8. OpenAI and provider compatibility

- Preserve supported OpenAI paths, JSON shapes, omitted-versus-null behavior, status codes, error envelopes, headers, streaming framing, and `[DONE]` behavior unless a versioned change explicitly says otherwise.
- Additive fields need typed backend models, API tests, UI contracts where used, and docs. Breaking behavior needs a migration/deprecation path.
- Provider-specific authentication, request translation, capabilities, response translation, streaming, and error classification stay in provider adapters. Do not scatter provider-name conditionals through generic handlers.
- Strip DeltaLLM-only fields before upstream calls. Forward only explicitly supported headers and parameters.
- Preserve unknown/server-owned fields during read-modify-write flows where forward compatibility requires it; never accidentally erase hidden pricing, metadata, or credential references.
- Test provider changes with deterministic mocks for success, error mapping, timeout, cancellation, malformed output, streaming close, and failover. Tests MUST NOT require live third-party credentials.

## 9. PostgreSQL and migrations

- PostgreSQL is the durable truth. Put invariants in foreign keys, unique constraints, checks, transactions, and idempotency keys instead of relying only on Python checks.
- Every query and mutation derives tenant scope from the verified principal and server-side relationships. A client-supplied organization/team/user/key ID is a lookup request, never proof of access.
- Transactions are short and have explicit ownership. Never hold a transaction or row lock across provider, MCP, webhook, email, or other network I/O.
- Prisma migrations are append-only once shared. Never edit an applied migration and never use `prisma db push` for a committed/shared/production environment.
- Use expand/backfill/contract across releases. Dropping/renaming data, tightening nullability, changing a large type, or building a blocking index requires a rollout, compatibility, lock-duration, backfill, rollback, and data-verification plan.
- Production DDL runs through one coordinated, retry-safe release/migration workflow before application rollout. Migration history/locking makes retries idempotent; API replicas MUST NOT race DDL on startup.
- Commit schema, migration, repository/query changes, generated-client impact, fixtures, and tests together.
- Growing lists use server-side filtering and cursor-based or otherwise bounded pagination. New hot/high-volume queries need index and query-plan reasoning; avoid N+1 reads and per-row writes.
- Raw SQL values are parameterized. Static allowlists are required for table, column, direction, or expression fragments.
- Use UTC instants at persistence boundaries and make interval/reset semantics explicit.
- New monetary values use `Decimal` with declared rounding or integer atomic units. Do not introduce new binary-float money logic. Migrating existing float fields requires an explicit data migration and compatibility plan.
- CI MUST prove both fresh installation and upgrade from the last supported release for migration-sensitive changes.

## 10. Billing, budgets, usage, and audit

Economic effects require stronger guarantees than best-effort telemetry.

- Every charge has a stable event ID and exactly-once economic effect under retries and process death.
- Persist the spend event and all ledger deltas transactionally, or use a durable outbox with idempotent consumers and replay. Never use fire-and-forget tasks for spend, budget state, or required audit.
- Do not increment key/user/team/organization/model ledgers as unrelated best-effort operations that can leave partial state.
- Freeze attribution, pricing source/version, currency, token accounting, and rounding inputs with the accepted event so replay produces the same result.
- Finalize usage once across non-streaming success, streaming completion, cache hits, errors, cancellation, and disconnects. Make the owner of that side effect explicit.
- Budget and hard-quota dependency failures use a documented production fail mode. Never silently turn an economic/security control into per-process enforcement in a multi-replica deployment.
- Audit storage is bounded, redacted, access-controlled, and retained for a declared period. Prompt/body/tool content is opt-in and must obey content settings before enqueue.
- A successful mutation does not become “failed” merely because a reporting refresh failed; expose reconciliation as a separate, observable state.

## 11. Redis, cache, and distributed coordination

Use one centrally configured Redis pool per process role with bounded connect/read/write/pool timeouts, connection count, retry/backoff policy, TLS/auth, health checks, and pool-saturation metrics. Never construct a Redis client per request.

All keys and channels use a shared builder with application + environment + schema-version + capability namespace. Include tenant scope where needed. Do not expose raw internal cache keys in response headers. Hash sensitive identifiers and custom cache keys.

- Every ephemeral key has a justified TTL. Memory and Redis TTLs are not substitutes for invalidation when authorization or economic correctness changes.
- `KEYS` is forbidden. `SCAN`/wildcard deletion is allowed only in bounded admin/background maintenance, never on a request path. Prefer exact invalidation or generation/version bumps.
- Use `MGET`, pipelines, or Lua for multi-entity operations. A method named “batch” MUST NOT loop over individual Redis commands.
- Redis-backed counters, quotas, admission, leases, locks, and claims use a reviewed Lua script/transaction, never `GET` followed by `SET`. PostgreSQL worker claims continue to use transactional `SKIP LOCKED` and fencing.
- Locks/leases use a unique owner token, TTL, guarded refresh/release, cancellation cleanup, and fencing epoch when stale owners could commit work.
- Declare supported Redis topology. Multi-key Lua MUST share a Redis Cluster hash slot if Cluster is supported.
- Critical Redis logic needs real-Redis tests for the applicable concurrency, TTL, reconnect, outage, and recovery semantics; Lua/script-cached changes also test `NOSCRIPT` recovery. Fake-only tests are insufficient for distributed claims.

Each Redis-backed feature declares and tests its failure policy:

| Capability | Redis outage behavior |
| --- | --- |
| Response/prompt cache | Fail as a cache miss to the durable/upstream source, with bounded single-flight and an observable degraded state |
| API-key/auth cache | Fall back to PostgreSQL; never authorize because Redis failed, and return deny/unavailable if the durable check also fails; a cache write failure alone must not fail auth |
| Config/governance invalidation | Reconcile from PostgreSQL/outbox/generation with a declared maximum staleness; listener restarts with backoff |
| Routing/cooldown/health | Use an explicit bounded degraded policy and report it; do not pretend local state is cluster-wide |
| Rate/concurrency/tier/quota | Obey an explicit fail-open/fail-closed setting; hard security/economic controls default closed in production |
| Billing/audit/durable jobs | Redis is never the only record; use PostgreSQL/outbox durability |

Any degraded mode emits bounded metrics, structured logs, readiness/degraded detail, and an audit event where appropriate. It must distinguish “disabled,” “empty,” “stale,” “partial,” and “unavailable.”

## 12. Batch and background work

The existing batch subsystem is the reliability pattern to copy: durable state, atomic claims, leases, fencing, bounded workers, idempotent transitions, outboxes, and recovery.

- Work that must survive process death uses the existing PostgreSQL row/outbox claim-and-lease pattern or an equivalent atomic durable queue. Where ownership can outlive a transaction, use a unique worker ID, expiring lease, heartbeat, ownership/fencing epoch, bounded jittered retries, terminal failure state, and idempotency key.
- Every state transition validates the current state and owner in the same transaction. Stale workers MUST NOT commit after losing a lease.
- In-memory queues may hold best-effort telemetry or transient execution buffers only when authoritative work remains durably claimed and reclaimable. They are bounded, measured, drained/cancelled on shutdown, and have a declared queue-full policy.
- Background loops have one lifecycle owner, a startup-ready signal with timeout, supervised liveness, cancellation-safe shutdown, bounded restart backoff/jitter, and reconciliation after interruption.
- Split API/worker deployments require shared object storage. Local disk is temporary staging only and has size, path, cleanup, and retention bounds.
- Required business webhook/callback delivery uses durable enqueue, HMAC or equivalent signing, replay-safe IDs, encrypted secrets, SSRF policy, bounded bodies/timeouts/redirects, and observable terminal failure. Optional telemetry integrations may use an owned bounded best-effort queue with drop/failure metrics and MUST NOT affect request correctness.
- Kubernetes termination grace MUST exceed the application's bounded drain deadline. Mark readiness false while draining.

## 13. Dynamic configuration

- Add application settings through typed Pydantic models with bounds, enums, and cross-field validation. Do not read environment variables directly outside settings/bootstrap, except inside the dedicated secret resolver or provider adapters implementing a documented SDK credential chain.
- Classify every setting as startup-only or hot-reloadable. Startup-only changes clearly report that a restart is required.
- In the same change update all applicable surfaces: `src/config.py`, runtime/dynamic models, `config.example.yaml`, `.env.example`, `deploy/kubernetes/helm/values*.yaml`, `deploy/kubernetes/helm/values.schema.json`, docs, and tests.
- Build and validate a complete immutable runtime snapshot off the request path, then atomically swap one reference. Never `clear()` and repopulate a registry visible to concurrent requests.
- Reload is idempotent: callbacks, routes, guardrails, providers, and subscriptions replace or deduplicate the previous generation by stable identity.
- Redis pub/sub only accelerates reload. PostgreSQL polling/generation/outbox reconciliation provides recovery and a declared maximum staleness.
- Defaults preserve current behavior and are safe for local use. Risky behavior needs an explicit production-safe default, rollback flag, and bounded observability.
- Required secret resolution failure fails production startup/readiness. Optional failure produces an explicit degraded/disabled state; it never silently changes policy.
- Do not proliferate environment aliases. One canonical setting owns parsing and precedence.
- Config and Helm schemas SHOULD reject unknown keys at governed boundaries so misspelled production values fail validation rather than being ignored.
- Every new Helm value MUST be declared in `values.schema.json`; critical runtime/security blocks SHOULD reject unknown properties in CI.

## 14. Security, tenancy, and privacy

- Backend authorization is the security boundary. UI visibility is convenience only. Every mutation and scoped read re-checks permissions server-side.
- Default deny. Derive tenant scope from the authenticated principal, apply it in the repository query, and test cross-tenant denial and non-enumeration.
- Production mode, proxy trust, and cookie security are explicit settings, not hosting-vendor heuristics. Production cookies use `Secure`, `HttpOnly`, intentional `SameSite`, and bounded lifetime.
- Honor `Forwarded`/`X-Forwarded-For` only when the direct peer is in configured trusted proxy CIDRs. Use one resolver for auth, rate limits, audit, and logs.
- Required master/salt secrets—and encryption, signing, or webhook secrets when their feature is enabled—have no fallback/default. Compare secret tokens in constant time where applicable.
- Secrets are write-only. APIs/UI/logs/audit/metrics return only redacted or `configured` state, never decrypted values or credentialed URLs. Missing/redacted update fields preserve the existing secret.
- Return an allowlisted safe configuration DTO; never serialize the full settings model and try to remove a few known secrets afterward. Extend one central, tested redactor for structured logs and audit.
- Persist secret references where possible. If durable secret storage is unavoidable, use envelope encryption with key IDs and rotation; do not add plaintext credential JSON or MFA secrets.
- Raw generated API keys are shown once, kept only in ephemeral UI state, and stored server-side only as an approved hash. Never put API/master/provider keys or session tokens in URLs, browser `localStorage`, cache keys, analytics, or error telemetry.
- Single-use invite, reset, and verification tokens may use a URL only when the flow requires it. They MUST be high-entropy, purpose-bound, short-lived, one-time, absent from analytics/referrers/logs, protected by no-store responses, and removed from browser history/address state after consumption.
- Prefer secure session-cookie authentication. The existing master-key `sessionStorage` fallback is a contained legacy/emergency path: do not add new consumers or broaden its persistence, and never move it to `localStorage`.
- Cookie-authenticated state-changing requests require CSRF protection through validated Origin/Referer policy or a CSRF token in addition to `SameSite`. CORS and trusted-host settings are explicit allowlists; never combine wildcard origins with credentialed requests.
- Security headers have one documented owner in the app or ingress: HSTS on TLS, CSP/frame-ancestors, `nosniff`, Referrer-Policy, and host validation. Do not apply conflicting policies in multiple layers.
- Key, membership, role, grant, tier, budget, and credential mutations commit durable state and complete required cross-replica invalidation—or durably enqueue it—before reporting full success. TTL alone is insufficient for revocation.
- All user/operator-controlled outbound URLs reuse the batch webhook resolution and rebinding machinery: scheme/port policy, DNS checks, metadata blocking, redirect revalidation, TLS policy, and bounded time/body. Tenant-controlled callbacks default to denying loopback/link-local/private targets; privileged provider/MCP endpoints may reach explicitly allowlisted private/VPC destinations under a separate egress policy.
- Render untrusted provider, model, prompt, audit, tool, and error content as text. No unsafe HTML, `eval`, dynamic imports from input, or unsafe deserialization.
- Bound upload size, decompression, row count, path resolution, parsing time, and retained content. Prevent traversal and symlink escape.
- Public liveness reveals only coarse process state. Detailed dependencies, deployments, config, fallback events, metrics, and errors require operator authentication or documented network protection.

## 15. Observability and health

- Generate or validate a bounded correlation ID at ingress, return/propagate it, and include it in structured redacted logs and audit. Do not trust arbitrary client IDs as durable idempotency keys.
- Logs and errors never contain authorization headers, keys, credentials, session/reset/invite tokens, prompts, tool arguments/results, provider payloads, webhook secrets, decrypted config, or credentialed URLs by default.
- Prometheus labels come only from fixed enums or deliberately bounded configured sets. Never label by request/batch ID, raw URL, free-form error text, API key/hash, user, team, organization, tenant, or attacker-controlled value.
- One component owns each request, token, spend, audit, callback, health, and cache side effect. Avoid double-counting across middleware, failover, provider adapters, and callbacks.
- Liveness performs no dependency I/O. Readiness uses short independent timeouts and fails when a configured critical client or supervised task is missing/dead. “Unavailable” and “intentionally disabled” are different states.
- Keep `/metrics` and detailed diagnostics internal or authenticated. Public health MUST NOT expose provider errors or topology.
- New critical paths and workers ship with bounded RED/USE metrics, actionable error classification, degraded/readiness semantics, and tests for timeout, cancellation, dependency outage, and recovery.
- Never swallow an operational error into an empty list or zero. Preserve last-known-good data with freshness and explicitly label partial/stale/unavailable state.
- Local pool saturation, queue overload, or gateway-capacity rejection MUST NOT mark a provider deployment unhealthy or trigger provider cooldown; classify local and provider failures separately.

## 16. Frontend (`ui/`)

### Architecture and contracts

- The UI is a control plane. Route pages orchestrate route state, queries, and layout; pure validation, form-to-payload conversion, normalization, and reconciliation belong in tested domain helpers.
- Reuse and extend the existing admin shells, modal, confirmation, toast, table, status, form, and authorization primitives rather than copying behavior or Tailwind blocks.
- Split `ui/src/lib/api.ts` by domain as it is touched: one shared transport/auth/error layer, typed domain contracts, and domain endpoint modules, with a stable barrel during migration.
- The shared transport imports neither React nor pages. Domain API modules depend on transport plus their contracts; pages/components consume domain APIs and MUST NOT import raw transport primitives. Avoid cyclic API-domain imports.
- Every endpoint has typed request, success, pagination, and known-error contracts. No new `any`; use `unknown` plus narrowing for dynamic metadata.
- Prefer generated TypeScript contracts from FastAPI OpenAPI. Until generation exists, an API change updates the backend model/test, TypeScript contract/adapter test, UI consumer, and docs atomically.
- Use `resolveUiAccess`/`hasPermission` and server capabilities for UX gating. Do not add ad-hoc role comparisons.
- Preserve omitted-versus-null and unknown/server-owned fields in editors. Follow the existing tier/model pure-helper pattern.

### Fetching and operational semantics

- Every new or materially changed network read accepts and passes `AbortSignal` end-to-end, aborts on unmount/query-key change, and rejects stale results by request identity/generation. Mutations prevent double submission and stale completion after route/entity changes.
- A shared query abstraction has stable keys, deduplication, bounded cache, last-good data, explicit initial/background/error state, and a `refetch()` promise that settles after the network request. Do not `await` the current nonce-only `useApi.refetch` as if it did so.
- Browser query/cache identity includes the authenticated principal and auth mode. Clear protected cached state on logout, principal change, or auth-mode change; never persist one-time keys, credentials, or sensitive audit/body data in a query cache.
- Central transport preserves error meaning: `401` enters one bounded re-auth/sign-out path and clears scoped cache; `403` remains permission denied. Neither becomes empty data or `404`, and public auth endpoints MUST NOT create redirect loops.
- Parallelize independent reads; avoid waterfalls and overlapping polls. Polling is visibility-aware, abortable, skips while busy, has a minimum interval, and backs off after failure.
- Use server-side search and pagination for growing collections. Do not fetch hundreds of entities or call `listAll` for an interactive selector unless the data is explicitly bounded and truncation is visible.
- Distinguish initial loading, empty, permission denied, partial, stale, and unavailable. Never coerce missing/failed Redis or health telemetry to zero.
- A successful mutation followed by a failed refresh remains a successful mutation with a separate refresh warning.
- Ordinary calls go through the shared API client. Direct `fetch` is reserved for transport needs such as streams, blobs, audio, or `FormData`, and must reuse central auth, abort, timeout, redaction, and error behavior.

### UX, accessibility, and bundle health

- Destructive actions use the shared confirmation dialog, name the consequence, lock while pending, and show a success/error toast. Do not add native `confirm` or `alert`.
- Icon-only controls need accessible names. Inputs need labels and associated errors. Dialogs/drawers trap focus, support safe Escape, prevent background interaction, and restore focus. Tabs implement tab semantics and keyboard navigation.
- Status never relies on color alone; charts have textual summaries and motion respects user preference.
- Lazy-load route pages and heavy libraries with a route-level loading/error boundary. Keep auth and the shell in the initial chunk; defer Recharts, Playground, and large editors.
- Do not increase the current initial gzip baseline. Target an initial bundle below 250 KB gzip and individual lazy route chunks below 150 KB gzip unless a measured exception is documented.
- Route/dependency changes report Vite's gzip output and compare the initial chunk with the audit baseline. Treat the 250 KB target as a direction until a checked-in automated budget makes it a CI gate.
- Keep Vite base paths, backend SPA fallback, assets, and router paths aligned. Test login, `/`, authenticated 404, and direct refresh of a nested production route.
- Hashed assets use long-lived immutable caching; SPA HTML revalidates.
- Interactive changes verify loading, empty, error, stale, and permission states below and above the `768px` breakpoint. Shared dialog/drawer/tab/table/navigation changes require an automated keyboard/focus regression; page-local work documents a keyboard smoke test until a component/browser harness exists.

## 17. Production, dependencies, and releases

- `pyproject.toml` plus `uv.lock` are the Python dependency source of truth. Production images install the frozen lock without dev dependencies. Any `requirements.txt` export is generated from and verified against that lock, never hand-maintained independently.
- Commit dependency manifest and lock changes together. Align Python, Node, PostgreSQL, and Redis versions across CI, Docker, local docs, and Helm. Pin optional/build inputs.
- Maintain one canonical container build; avoid hand-maintained Dockerfile variants drifting by platform.
- Production images run as a non-root UID/GID, use an immutable version/digest, drop capabilities, enable seccomp, and use a read-only root filesystem where practical. Do not add a runtime dependency on root or mutable application files.
- Production Kubernetes uses explicit resources, probes, PDB/rolling safety, topology, least-privilege service accounts, external secrets, NetworkPolicy, and a drain-aligned termination grace.
- Liveness is process-only; readiness gates traffic on required dependencies/tasks and becomes false during drain.
- Production PostgreSQL and Redis are authenticated, TLS-protected where supported, backed up, restore-tested, and externally capacity-managed. Docker Compose defaults and exposed ports are development-only.
- When batch is enabled in multi-replica production, use split API/workers and durable shared object storage.
- Release CI builds and smoke-tests the exact artifact from the same commit. Prefer immutable action/base-image references, vulnerability/secret scans, SBOM/provenance, and signed published images.
- CI/release workflows use least-privilege permissions. Third-party release actions and production base images SHOULD be pinned to immutable commit/digest references and updated through reviewed dependency automation.
- Do not edit generated `ui/dist/`, `site/`, caches, Prisma client output, or vendored artifacts directly. Change sources and regenerate with the documented tool.

## 18. Verification matrix

Run the smallest focused checks first, then the broader gate required by risk.

| Change | Required verification |
| --- | --- |
| Python behavior | `uv run ruff check <touched paths>`, `uv run ruff format --check <touched paths>`, and focused `uv run pytest ...`; full relevant suite for shared code |
| Auth, tenant scope, billing, routing, config, cache, streaming | Focused failure/denial/cancellation tests plus the full affected integration suite |
| Redis atomicity/degradation | Unit tests and real Redis concurrency/TTL/outage/recovery coverage |
| Database/schema | `uv run prisma generate --schema=./prisma/schema.prisma`, migration on fresh and upgrade DBs, repository/integration tests |
| UI | Touched-file ESLint with zero errors, `npm --prefix ui run test:unit`, `npm --prefix ui run build`, and full `npm --prefix ui run lint` compared with the recorded baseline |
| API shape used by UI | Backend contract test, frontend type/adapter test, and production UI build |
| Helm/config | Helm lint/template for base, eval, and production values plus relevant `tests/helm` tests |
| Container/release | Image build, non-root startup, migration-job behavior, health smoke, graceful termination |
| Docs only | Link/path review, command/config parity check, and `git diff --check` |

Additional rules:

- Test discovery SHOULD be automatic and new runners MUST NOT introduce a hard-coded filename list. Until the current UI runner is migrated, register every new UI test in `ui/scripts/run-unit-tests.mjs` in the same change and verify that it actually executes.
- Distributed correctness uses real PostgreSQL/Redis integration tests in addition to fakes.
- Tests cover success, authorization/tenant denial, invalid input, timeout, cancellation, partial failure, retry/failover, and recovery as applicable.
- A flaky timing test is a product signal: assert invariants with deterministic clocks/barriers where possible; do not add arbitrary sleeps or wide retries.
- If a repository-wide gate has known pre-existing failures, run it, record the baseline, require zero new failures in touched files, and reduce the count where practical. Do not hide the result.
- Never delete or skip a regression test without proving the behavior is obsolete and updating its governing contract/docs.
- Every change runs `git diff --check`. Dependency changes also run `uv lock --check` or the package-manager lock-integrity equivalent. A migration-sensitive change adds a reusable last-release upgrade fixture/script if the repository does not yet provide one.

## 19. Current audit snapshot and ratchets

Audit date: **2026-08-16**. Code revision inspected: **`e1e7420cf9be`**. This snapshot records why several rules exist; re-run measurements rather than treating it as a permanent guarantee. Update it when the underlying debt is removed.

### Strengths to preserve

- Ordered `AsyncExitStack` lifecycle and shared, separately bounded provider/control HTTP pools.
- Revalidation and final-model authorization after request mutation; cache lookup after auth/preflight.
- First-chunk streaming failover with explicit upstream close.
- Atomic Lua admission and stream-lived leases with real Redis coverage.
- PostgreSQL migrations, database constraints, and extensive backend integration tests.
- Batch HA patterns: `SKIP LOCKED`, leases, fencing, heartbeats, bounded workers, and idempotent outboxes.
- Strong batch webhook encryption, signing, SSRF, metadata-IP, and DNS-rebinding controls.
- Helm production topology with HPA/PDB/topology options and split workers.
- Central UI authorization, reusable admin shells, typed pure domain helpers, and race-safe newer pages.

### Debt that MUST NOT get worse

- Oversized change magnets include `src/batch/repositories/job_repository.py`, `src/batch/worker.py`, large admin endpoints, `ui/src/lib/api.ts`, and several 1,000+ line UI pages. New feature concerns require extraction.
- The audited hot path amplifies prompt/budget lookups into sequential SQL/Redis calls and showed queue growth at the measured higher offered rate. New round trips are prohibited; touched paths should batch, safely negative-cache, and single-flight.
- Spend/accounting currently contains untracked tasks and partial best-effort ledger updates. Do not copy this pattern; billing work moves toward durable idempotent outbox/transaction semantics.
- Some Redis “batch” reads are sequential and some outage paths are inconsistent. New code uses true batching and explicit per-feature failure behavior.
- Dynamic reload mutates some live registries in place and can duplicate callbacks. Touched reload paths move toward immutable generation swaps.
- Broad `app.state` access and catch-all exception handling are common. New dependencies are typed at the edge and new failures are classified.
- Static bundle/SPA serving is split between `src/ui/routes.py` and `src/main.py`; do not add a third path, and consolidate ownership when this boundary is touched.
- UI contracts contain substantial `any`; `useApi.refetch` is synchronous/non-awaitable; routes are eagerly bundled. New code adds no `any`, no false `await refetch`, and no initial-bundle growth.
- At the audit revision, `npm --prefix ui run test:unit` passed 62 tests, `npm --prefix ui run build` produced an initial bundle about 409 KB gzip, and `npm --prefix ui run lint` reported 295 findings. These are dated baselines, not current claims. Changed UI files MUST have zero lint errors; record full-lint and bundle deltas until checked-in automated budgets replace the manual baseline.
- CI has strong backend/PostgreSQL/Redis/Helm coverage but currently builds the UI without running its tests/lint, and the UI runner lists test files manually. Do not add undiscovered tests; move toward automatic discovery and full UI gates.
- Docker/Railway dependency installation can drift from the tested `uv.lock`; container variants are duplicated. Dependency/build changes MUST reduce or at least not widen that gap.
- Production hardening gaps include root containers, mutable default image tags, per-pod migration startup, incomplete proxy/cookie trust, unauthenticated detailed diagnostics, and high-cardinality identity metrics. Do not copy these defaults into new surfaces.
- Some persisted credential/config fields are plaintext or insufficiently redacted. New secrets use references/encryption and write-only APIs; touching an old secret path must not expose it further.

## 20. Definition of done

A change is done only when:

- its source of truth, owner, tenant scope, failure mode, retry/idempotency behavior, and latency budget are explicit;
- there is one implementation of the policy and its lifecycle is owned;
- public/API/provider behavior remains compatible or has a documented migration;
- state changes are atomic or durably replayable under process death and retries;
- multi-replica, Redis outage, cancellation, shutdown, and overload behavior are considered;
- secrets and tenant data are redacted, scoped, and absent from unbounded metrics/logs;
- configuration, UI contracts, docs, deployment manifests, and generated outputs are synchronized where applicable;
- focused tests and proportionate production gates pass, with pre-existing failures reported; and
- the final diff is smaller and clearer than an equivalent copy-pasted implementation would be.
