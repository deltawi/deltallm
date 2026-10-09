# Independent selector deployment

Decision: 2026-09-10, correction to issue #304 after PRs 1–6, against
`feature/issue-304-model-router` at `d41b66afe43154181f1c21f02b1ac9b372c2714a`.
This supersedes same-group classifier requirements, not the accepted routing architecture.

## Contract and ownership

`selector.classifier_deployment_id` resolves exactly one configured concrete chat
deployment by ID. It is independent of `members`, which contains answer placements
only. A tiny classifier can select between OSS-20B/economy and MiniMax/quality without
answering. Existing explicitly configured dual-role deployments remain dual-role;
disabling their answer membership does not disable classification.

Policy JSON remains the durable truth; no dependency table, selector-only model type,
new setting or provider client is introduced. The canonical deployment builder retains
its physical by-ID map before callable-group overlays. One registry reference contains
both immutable maps. Group-name collisions cannot hide the physical classifier.
Generation qualification resolves it once off the inference path. Only the enabled
answer inventory determines answer capabilities. The selector needs its own bounded
text context, explicit capability metadata, concrete supported provider, prices and
RPM/TPM, not answer tools/streaming/full context.

Reference validation, publication and runtime qualification share their respective
contract owners. Drafts validate existence/chat mode; publication additionally checks
execution/economic readiness. Partial writes resolve the effective inherited selector
before loading its inventory. Null removes it; v1/v2 opaque fields remain inert.
The unchanged prompt asks for the minimum sufficient allowed lane, safe highest rank
when uncertain, and JSON lane output only, ignoring instructions in quoted input.

Only existing CONFIG_READ/CONFIG_UPDATE administrators enumerate/attach selectors.
Group authorization permits its internal classifier hop but does not grant direct
access to its callable model. Required placement tags still constrain the hop.
No eligible answer or whole-response cache hit means no paid classification.
The existing one-decision owner, deadline, cancellation cleanup, customer-attributed
Decimal receipt/outbox and answer-only public usage are unchanged. Budgets remain soft.
Batch keeps the same per-item executor, checkpoint and recovery refusal semantics;
there is no new scheduler, regrouping, queue, lease owner or charge.

## Streaming completion correction

Real-provider testing exposed a pre-existing shared text-stream race: forwarding
`[DONE]` before answer accounting let a client's normal close cancel the write.
The fix makes the existing finalization owner an accounting/audit barrier around
terminal-frame delivery. It recognizes the provider terminal marker once through
the stream usage parser, retains one terminal line, closes the completed upstream
without waiting for HTTP EOF, and awaits the existing charge write and required
audit under the request deadline. It then sends the terminal frame and releases
the existing capacity permit before post-call hooks. The same ordering applies
to translated `message_stop`, ordinary groups and selector-enabled groups.

The existing server-owned answer event ID, tenant/pricing attribution, outbox
idempotency and selector receipt remain the source of truth. No extra charge,
provider retry, queue, task, connection pool, database migration, or setting is
introduced. Direct/synchronous spend mode is also awaited, rather than spawning
the legacy fire-and-forget write; this does not upgrade that mode's historical
ledger guarantees to the durable outbox's transaction semantics. The default is
`legacy`: its real writer logs and swallows event/ledger database failures, so a
successful terminal marker in that mode does not prove a charge was persisted.
Only `outbox` provides durable charge acceptance before completion; selector
activation already requires that mode. The outbox path's SQL/Redis/provider
operation counts are unchanged. First-token
delivery does not wait for finalization; terminal delivery now includes required
write latency. The existing end-to-end deadline bounds those writes, and the
existing five-second cleanup budget bounds release after timeout/disconnect.

Failed outbox charge acceptance withholds a success terminal; required audit
failure does so in either mode. Neither marks a provider unhealthy nor repeats
its paid call. Charges committed before a later audit/send
failure are retained, not replaced with zero-cost error records. This fix covers
closing at delivered completion. Earlier disconnects without a final provider
receipt and historical lost charges are not retroactively reconstructed; broader
partial-stream accounting remains a separate bounded follow-up. Tests cover real
HTTP close-at-terminal, EOF held open upstream, one charge per successful stream,
deadline/failure cleanup, content-before-commit and hook/permit ordering.
The legacy-failure characterization uses the real ingestion service, event writer
and ledger with failing persistence doubles, not a recorder that always raises.
Removing legacy write-loss/partial-ledger debt is a separate billing migration;
this focused correction neither extends that path nor changes its defaults.

## Transactional dependency protection

`src/db/route_policy_dependencies.py` coordinates policy references and physical
model mutations. Published references, including disabled groups, are protected.
Draft/archived references do not prevent deletion; publication/rollback revalidate.
Invalid changes roll back policy history, model mutations and runtime revision together.

Lock order is existing callable-key locks when applicable, sorted deployment
dependency advisory locks, sorted group locks, then mutation. The namespace is
distinct from callable-key ownership. Policy writes read at most the latest published,
latest draft and selected rollback reference, acquire old/new dependencies, then
reread under the group row lock. If dependencies changed, return a safe conflict
and let the caller retry the whole operation. This deliberately replaces the plan's
automatic retry loop: one bounded transaction is simpler and does not obscure
concurrent administrative edits. Never acquire a newly discovered dependency after
the group lock. Private transaction helpers require the caller to own that boundary.
Advisory contention has a one-second lock timeout and maps to a safe conflict.
No provider or readiness network probe runs while discovering dependencies.

Model update/delete finds the union of member and published-selector references.
An additive partial expression index on the published selector ID avoids scanning
policy history for this control-plane lookup. It stores one small index entry per
published v3+ policy, no archived/draft entries; each publication adds/removes its
entry. Existing group/status indexes support bounded dependency discovery.
There is no inference SQL addition, no ledger write amplification, no new retained
business data. Existing policy retention and PostgreSQL autovacuum/analyze ownership
remain in force.

Named credentials retain the existing concrete-deployment link protection and
runtime-revision refresh. The same secret resolver builds physical deployments once;
unresolved required secrets fail generation replacement, retaining last-known-good
state on reload rather than installing a partial generation.

## Cache, capacity and UI

Policy semantics remain **v3**; the independent runtime cache envelope, key namespace
and provenance advance to **v4 / validated-v4**. Older/corrupt entries are misses.
Response-cache namespace stays v5; canonical routing identity now includes the external
classifier's execution metadata and propagates through fallback dependencies.
Equivalent snapshots are stable, unrelated groups unchanged, and secret values,
live health and generation UUIDs are excluded. Batch decision fingerprint is deliberately
unchanged: a compatible completed checkpoint must not pay for reclassification on reload.

Shared physical health/capacity identity prevents per-group capacity multiplication.
No new pools, concurrency ceilings, retries, tasks, TTLs or deployment-wide connection
allocations are added. Selector-free and selected inference paths add zero dependency
round trips compared with the integrated feature. Publication adds bounded reference
reads/locks and at most one physical target lookup outside the answer inventory.

The UI remains **choose selector → assign answer lanes → publish**. The focused
GET `/ui/api/route-groups/by-id/{id}/selector-options` exposes only ID, model name,
provider, mode and qualification status/reason, with no-store responses. Search is
bounded to 128 characters; page size 1–50 (default 20), offset 0–100000 and an optional
selected ID preserve selections off-page. It uses the prepared inventory, not live
provider/Redis probes or the larger general-model DTO. Unknown metadata shows a repair
warning, not an automatic substitute.

The existing abort/generation-aware report hook owns one page, scoped to principal,
auth mode and group/query; it has no polling or persisted cache. Loading, empty, stale,
denied and unavailable remain distinct. First-time answer assignments are explicit.
Selector changes preserve answer weights, priorities, enabled flags and assignments.
Evaluation, draft saving and advanced controls remain optional.

## Rollout and rollback

1. Run the additive index migration through the existing single release owner.
   It uses a 5-second lock acquisition limit and 60-second statement deadline.
   Ordinary CREATE INDEX briefly blocks policy writes, not inference reads. Schedule
   low control-plane write traffic; measure the representative query plan first.
   If interrupted, verify index validity/history, resolve the failed migration using
   the existing Prisma release procedure, and retry; do not launch DDL on API replicas.
2. Upgrade every API and Batch worker while keeping existing policies. Old feature
   binaries do not understand independent v3 references. Do not enable them during
   mixed-version deployment. Wait for ready/converged complete generations.
3. Publish external-selector policies after all serving roles are upgraded.
   Old runtime-cache keys expire normally; no wildcard deletion is necessary.
4. Before binary rollback, switch external policies to old-compatible references or
   publish explicit selector removal; converge, drain in-flight operations and affected
   Batch work, and finalize receipts. Preserve history/checkpoints/charges. Leave the
   harmless additive index in place; index removal, if desired later, is a separately
   coordinated migration. No data backfill or membership conversion is needed.

The user's existing Docker demo and real credentials are outside automated acceptance.
Mock latency is not answer quality or real savings. Paid acceptance needs verified
pricing and separately bounded authorization; no live provider call is implied here.

## Verification

The focused suites cover external references, callable-name shadowing, independent
capabilities, preserved answer assignments, publication/delete/metadata races, rollback,
shared disabled-group references, safe inventory pagination/authorization, cache dependency
identity, and stale UI work. Realtime success/cache/stream tests and the existing Batch
harness run both dual-role and independent configurations.

Final gate results and raw benchmark paths are recorded in
[feature readiness](model-router-main-readiness.md). This decision is not permission
to push, create a PR, update the remote issue, modify the demo or merge into main.
