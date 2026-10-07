# Trusted external customer sign-in: implemented design

Status: Gateway implementation and local verification complete, 2026-10-06. External sign-in is disabled by default. The Replit Console agent owns the matching backend integration; connected staging and deployment qualification remain release gates.

## Decision and trust boundary

Use short assertions signed by the Console backend to exchange a verified Clerk identity for an opaque gateway session. The Console owns Clerk proof, the encrypted shared vault, the browser reference cookie, same-origin proxy, renewal and logout delivery. The gateway owns trusted signer configuration, existing-tenant registration, stable subject/account/runtime mapping, live authorization, replay, parent/child sessions, revocation and audit.

The [production plan](issue-344-trusted-external-sign-in-production-plan.md) remains the release specification. The concrete [Console connection contract](../docs/guides/console-gateway-connection.md) is ready for the agent in Replit. [Verification evidence](issue-344-verification-evidence.json) records the disposable-service results and their limits.

Use fixed RS256 keys with RSA of at least 2,048 bits, exact signer issuer/audience, separate exact Clerk identity issuer, verified email, purpose, fixed time bounds and a strong fresh nonce. Reject extra claims, duplicate JSON fields, token-directed key URLs, raw tenant/role/billing claims and browser exchange requests. Keys load at startup; no Clerk or JWKS request occurs in gateway verification or inference.

Direct Clerk token acceptance would add remote identity-provider/session semantics to the gateway. A new general OIDC flow would duplicate Console login. Neither is part of this release. Email equality is a collision requiring an audited operator link, never linking authority.

## Identity, ownership and compatibility

One exact external issuer/subject has one registered customer binding and one dedicated ordinary gateway account. Its runtime user and existing organization/team stay fixed. This limit follows from private asset ownership by account ID: session filtering cannot isolate one account's private assets across several customer workspaces. No new asset ownership scheme is introduced.

First provisioning creates only account/identity/runtime/missing membership state in the registered existing tenant. It does not create another organization/team. Repeat exchange preserves existing email, roles, removed memberships, MFA, password-change requirements and all economic fields. Eligible explicit links preserve passwords, MFA, assets, permissions and billing data. Privileged, unrelated-workspace or conflicting runtime/asset accounts are ineligible.

`PlatformSessionRepository` owns session SQL. `PlatformSessionService` owns opaque tokens, the existing salt/hash contract, context and revocation, and delegates external validation to the owned external service. `PlatformIdentityService` retains its existing interface for operator login, SSO, invitations, recovery and MFA. `SSOAccountService` retains the operator policy and has an explicit strict external-customer resolution policy. No email-based external fallback is used. A configured session salt is required; there is no `change-me` fallback.

External children use `psk_ext1_` and the existing salted session-hash format. This recognizable prefix lets middleware deny mixed master/customer credentials even after a child has expired. Ordinary session tokens and responses remain compatible. `/auth/me` adds optional session source, workspace and expiry fields. Customer scope is intersected with live membership permissions and a fixed ceiling; shared auth/scope helpers enforce the same binding. Existing managed-asset authorization remains authoritative. UI and proxy grant no permissions.

Customers can create private credentials/models, share within the fixed workspace, read their own usage, and manage their own permitted keys. They cannot create public grants, alter budgets/ownership, regenerate keys, administer other tenants or gain operator access. Playground uses an explicitly selected API key owned by the same account and exact runtime user/team. Browser sessions never authorize inference.

## Durable state and transaction boundaries

Seven additive migrations provide integrations, immutable bindings, subject mappings, replay claims, parent sessions, child scope/epoch fields, a maintenance lease, MFA proof, lifecycle guards and rollback indexes. PostgreSQL is the authority. Redis owns distributed admission and key-cache enforcement, not replay or identity durability.

A verified nonce and required attempt audit commit in their own short transaction before provisioning. Account, identity, runtime mapping, missing memberships, subject, parent, child and required success audit commit atomically. A conflict, cancellation or audit failure rolls back provisioning but leaves the nonce consumed. Retry needs a fresh assertion. Real PostgreSQL tests cover races, deferred constraints and this failure boundary.

Parent expiry is fixed at the earlier of the original Clerk expiry and 12 hours after authentication. Children last at most 300 seconds; renewal returns a generation and refresh delay, keeps at most two unexpired generations, and permits at most 30 seconds of previous-token overlap. A revoked parent is a durable tombstone and cannot reopen. MFA proof carries only within the same live parent/epochs and unchanged factor.

Revocation before first exchange creates a tombstone; subject suspension can create a suspended shell before provisioning. Integration/binding/subject state changes use version checks, epochs and required audit. Logout and account-session revocation mark legacy child `revoked_at` as well as parent state. Resume requires explicit live-reference revalidation and does not restore a revoked parent.

Scope-wide revocation changes only the durable control row and its epoch. It does not rewrite retained parents or children. Live validation compares child epochs with current control epochs. Renewal uses the exact last child generation as the parent's durable epoch record, in the same SQL statement that advances the parent. The existing parent/generation index bounds that lookup. A stale or missing record prevents issuance and returns `external_reauthentication_required`. First issuance is permitted only for generation zero. Child retention is seven days; parent life is at most twelve hours. Cleanup cannot remove the epoch record of a live parent. This design adds no dependency round trip or worker. Required audit failure rolls back the state change. Existing integration/binding/subject locks serialize exchange and revocation across replicas. Resume can issue a fresh parent, but never advances a parent from an earlier epoch.

External scope requests fail when their required permission has no live grant. Batch administration and batch create-session routers deny external customer sessions before their handlers run. This matches the existing customer UI capability. Operator routes retain their current scope and permission rules.

Five historical tenant/identity foreign keys were replaced by active-state deferred checks and deletion-retirement triggers. Deleting a mapped account, identity, runtime user or team, or starting organization deletion, suspends access and revokes parents/children in the same transaction while preserving immutable historical IDs. Cancellation does not resume access. Session/parent foreign keys and uniqueness remain. Ordinary operator membership changes retain their prior locking; only active external mappings take the extra account lock.

Account disable or missing memberships immediately deny live validation and exchange; sign-in never repairs those controls. Operators use explicit subject suspension/session revocation when permanently closing existing external parents, and explicit account/membership recovery when restoring eligibility.

## Owners, capacity and failures

One `ExternalAuthRuntime` is created and closed by existing auth/infrastructure bootstrap. It owns the verifier/executor, ingress gate, typed services, dedicated primary database pool, and cleanup task. Lower layers accept typed dependencies and transaction repositories, not FastAPI requests or deep application state.

The four-connection pool reserves two mutation connections, one validation connection and one maintenance connection. Mutation/validation queues hold at most eight each with a 50-ms acquisition limit. Locks are limited to 100 ms, statements 250 ms and transactions 750 ms. Requests have a one-second end-to-end deadline. Four ingress slots have no queue; two crypto workers have eight queued submissions. Maintenance cannot consume reserved validation capacity. UTC is set transaction-locally.

Validation uses two statements: UTC/deadline setup and one indexed authorization join; there is no last-seen write. Real tests enforce replay at most four statements, first exchange at most sixteen and repeat exchange at most ten, including setup/required audit. The one-read target was adjusted to include explicit safe transaction setup.

Enabled startup requires the acknowledged `external_customer_v1` protocol, all seven completed migration ledger rows, configured trust, explicit Console origin, primary PostgreSQL, Redis, durable audit/outbox worker and cache invalidation worker. Readiness checks dependencies; private diagnostics report protocol/state/health and bounded queue counters without trust or customer data. Missing capacity/dependencies fail closed; no master or local durable fallback exists.

Successful mutations require atomic durable audit. Admitted invalid-signature denials use sanitized required audit bounded globally to 60/minute. Oversized/ingress-overload traffic uses counters. Metrics use fixed labels and never identities, nonces, sessions, raw URLs or secrets.

A shared PostgreSQL lease coordinates cleanup with indexed bounded `SKIP LOCKED` pages. Replay retention is at least fifteen minutes; expired children retain seven days and parent tombstones at least thirty days beyond expiry. Mappings remain durable. Default cleanup capacity is 200 rows/second per kind, with configuration enforcing 25% nominal headroom over the aggregate admitted integration rate. Sustained throughput, audit backlog, indexes/vacuum and deployment headroom require production qualification.

## API-key cache and billing boundary

Own-key removal atomically deletes the row, commits required audit, and enqueues exact-hash revocation work. An immediate marker attempt has a 100-ms deadline; failure returns a safe pending enforcement result. The worker retries durably. Private status polling and the UI expose pending/enforced state without a key hash.

`KeyAuthCache` owns `key:v5:` lookup/fill/revocation. Atomic Lua reads Redis time; fill has a one-second deadline and never overwrites a revocation marker. Routine invalidation preserves markers. Redis-outage primary reads are bounded and create no process-local allow cache. The touched lookup/fill positions use the same Redis call count. Tests cover delayed/conflicting fills, marker expiry, recovery and multiple clients. With enabled cache lifetime at most sixty seconds, pending enforcement is bounded by sixty-one seconds.

Sign-in and browser logout do not change application-key lifetime or payment entitlement. Existing blocking, budgets, spend, reservations, rates, tiers, pricing and margins remain authoritative for inference. Gateway economic snapshots, customer budget-denial-after-renewal and existing inference enforcement checks verify that boundary. Console pricing and checkout stay outside this feature.

## UI and Console contract

The gateway owns one static route/config seam and runtime mount/basename. `/gateway/` uses the same built assets under `/gateway/ui/`, including deep links/lazy chunks, with safe uncached HTML and reserved API/asset 404 behavior. Strict Console mode accepts only an external customer session, clears saved master credentials, offers the Console-owned login route, and invalidates stale logout/principal reads. Playground key selection uses Console vault endpoints; inference sends no browser bearer credential.

The user confirmed the Console is in Replit and its own agent will implement signer, encrypted shared store, generation compare-and-set renewal, proxy, verified Clerk events and logout. Gateway tests do not establish that deployed integration. The handoff specifies exact claims, endpoints, CSRF/origin rules, reference-cookie policy, secret redaction, streaming/cancellation, bounded retries and joint staging evidence.

## Deployment and rollback

Helm validates startup DTOs, required API workers, peak replicas plus rolling surge, main/telemetry pools and separate worker capacity. Enabled deployment requires an explicit certified PostgreSQL budget; defaults stay off. A fifteen-API-process example reserves sixty incremental connections but does not approve total database capacity. The production image runs as UID/GID 10001; required mounts must be readable by that identity.

Deploy compatible binaries to every gateway replica with external sign-in disabled, then configure/register disabled trust/bindings, deploy the Console, qualify staging, and enable approved cohorts. Old binaries ignore external scope, so none may serve issued customer sessions. New cache readers/fillers select `key:v5:`; old binaries use `key:v4:`. Complete this rollout before customer key operations.

`scripts/external_auth_rollback.py` previews by default. After stopping Console exchange/renewal, customer traffic and key mutations, `--apply --approval-reference` disables integrations, revokes parents and legacy child rows in bounded audited pages, and reconciles retained exact hashes against current/old cache namespaces. Retain additive tables, identity mappings, tombstones, audit and outbox. Retry safely after an outage; restore old traffic only with zero enabled integrations/live parents/live children and completed cache reconciliation. No wildcard scan or destructive down migration is used.

The local rehearsal verified the actual baseline session reader accepted the child before preparation and denied it afterward; current validation also denied it, the exact v4 entry was removed, and the v5 denial marker remained.

## Verification and release limits

Python 3.11 and frozen dependencies/generated Prisma were used. Native disposable PostgreSQL ran final SQL checks; Redis and production-image services used disposable containers and were stopped before another task's exclusive benchmark. Direct virtual-environment binaries avoided a sandboxed macOS `uv run` failure; frozen sync and lock checking later passed with approved dependency access.

All five dependency lanes were exercised. Latest full results: hermetic 4,067 passed/3 skipped; application 1,585 passed, plus sixteen focused final API/diagnostic cases; PostgreSQL 409 passed/23 skipped with Redis unset; Redis 101 passed/1 skipped; Helm 80 passed. Combined PostgreSQL/Redis feature runtime tests passed separately before Redis was stopped. The sixteen focused cases include the additional API schema case. Three final customer-inference/economic cases pass against PostgreSQL: the normal bearer route accepts an owned private model, denies another account before provider dispatch, permits authorized team sharing, preserves usage attribution, and still denies a zero-budget team after session renewal. UI: 280 passed, production build passed, initial bundle 1,381.71 kB (gzip 360.88 kB) versus baseline 1,463.87 kB (gzip 377.54 kB). UI lint preserves 116 baseline errors with zero new normalized findings; full Python Ruff passes; touched Python formatting passes, with 226 existing full-tree formatting findings versus 231 at baseline.

Fresh installation, last-supported v0.1.49 upgrade, shared-feature upgrade and model-identity migration recovery passed with the complete migration chain. Generated OpenAPI/config/provider checks, nine documentation tests, strict site build and public-artifact containment pass. The live in-app browser check could not run because its enforced security policy could not be verified. UI unit and HTTP mount checks passed; deployed browser acceptance remains unverified.

The 30,000-session/30,000-replay-row profile measured validation p95 4.88 ms and cleanup of 1,000 assertions plus 1,000 children in 25.21 ms. A non-root production image completed fresh migrations, readiness, exchange, replay denial and renewal. Short 50-RPS inference smoke runs completed all 500 requests in each mode without drops. These local measurements do not certify multi-replica load, deployed secret handling, production PostgreSQL headroom or the connected new-customer journey. Production enablement and issue closure remain gated on the Replit and operator evidence recorded in the plan.
