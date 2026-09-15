# PR5: bounded budget and prompt dependencies

Tracks concurrency roadmap [#320](https://github.com/deltawi/deltallm/issues/320).
Base: `9445da6c`. This change preserves the existing soft admission checks; it
does not implement atomic monetary reservations or certify production throughput.

## Implementation slices

1. Preserve key/user/team/organization/team-model decision order. Remove historical
   aggregation from both budget query modes, reject unavailable economic state,
   bound guarded due resets, and qualify the combined snapshot against real PostgreSQL.
2. Provide explicit maintenance reconciliation of team/model counters. Keep
   recipient discovery and notification delivery outside admission using durable
   PostgreSQL intents and the existing claim, lease, fencing and lifecycle pattern.
3. Pipeline cold binding cache fills through the existing bounded Redis client.
   Preserve tenant scope, implicit bindings, negative caching and epoch invalidation.
4. Record query plans, dependency counts and constant-arrival comparisons, enable
   the qualified combined query in the production overlay, and complete review and
   the affected verification gates.

## Invariants and ownership

PostgreSQL primary remains authoritative for policy and spend. Missing or corrupt
budgeted counters are unavailable, never zero. Repair requires authoritative
history (including archived amounts) and explicit maintenance ownership. It cannot
run from admission or silently reconstruct only retained events.

Budget reads use the foreground allocation and one operation deadline. The normal
combined decision has one SQL round trip; due resets have a fixed four-scope bound.
Notifications use control-plane capacity and a bounded acceptance deadline, with
durable delivery outside the request. Optional alert failure does not change a
budget decision. No provider operation is retried to deliver an alert.

Prompt fills borrow the existing ordinary-cache Redis pool. One cold binding chain
has at most the five server-derived scopes and uses one MGET, one SQL query, and one
pipeline. Redis failure is an observable cache miss/write failure. Invalidation
must prevent a fill from being reused under a newer namespace epoch.

## Rollout and rollback

Apply additive migrations through the coordinated migration workflow before API
rollout. Qualify legacy/combined parity before selecting combined in production.
Retain legacy mode as an explicit rollback for query execution; it must retain the
same missing-state safety and cannot restore historical request-path scans.
Accepted notification work survives process shutdown. Application rollback must
retain a compatible delivery owner until accepted work has drained.

Measurements and completed gates are recorded in the [evidence directory](../project/benchmarks/budget-prompts-2026-09-15/README.md).

## Reset compatibility boundary

Normal combined reads use one SQL RPC. An expired reset still uses a guarded CAS
and rereads on a lost CAS before evaluating that scope. At most four scopes can
reset: a fixed upper bound of nine SQL RPCs including the snapshot, all inside the
same budget-operation deadline. Keeping this exceptional sequential transition
preserves first-denial ordering and prevents resetting later scopes after an earlier
scope rejects. This is the retained bounded compatibility step, owned by billing;
the ordinary-route reservation protocol must replace it with a single atomic
multi-scope period transition before claiming hard-cap enforcement. No reset loop
scales with elapsed time or event history. A lateral top-one lookup keeps both
team and model in the counter index condition, preventing team-only scans when
the desired counter appears late among many models.

## Durable notifications

Only organizations currently expose an enforced soft-budget field. A successful
threshold check accepts one coalesced intent on the existing control database
allocation, with a 100-ms caller deadline, no Redis and no recipient lookup.
PostgreSQL uses nonblocking capacity admission and caps retained intents at 10,000,
including completed/failed records. Contention/full/unavailable outcomes shed this
optional work with bounded metrics; they never change a budget decision. Repeated
checks within the existing silence window avoid writes. Accepted rows are durable.

Each enabled API process owns one supervised worker, one leased record and no
memory queue. It borrows the existing control pool: no new connections are added.
A record has a 30-second token-fenced lease and preparation/delivery each have a
5-second deadline. Preparation retries at most five times with bounded jitter.
Recipient discovery precedes a persisted dispatch boundary. After that boundary,
crash or timeout is explicitly `delivery_unknown`, with no automatic external replay.
This preserves the existing email outbox's delivery ownership and avoids blind
Slack duplication. Operators reconcile unknown delivery before any manual retry. Unknown outcomes retain
their dedupe window for at least seven days.

Intents contain bounded identifiers and monetary snapshots, no emails or prompt
content. There is one row per organization, a due-work partial index, and bounded
cleanup of terminal rows older than seven days after their silence window. Apply
normal autovacuum/analyze; partitioning is unnecessary for the hard 10,000-row
limit. Organization deletion cascades these rows and dispatch rechecks lifecycle.
Changing budget_notifications_enabled or budget_alert_ttl_seconds requires a restart
to establish the worker and its silence window; the global governance switch can
still suppress pending sends at runtime. Rollback must keep an enabled
compatible worker until accepted work has drained. Optional worker degradation is
visible in protected readiness details and does not withdraw inference capacity.

## Bounded prompt SQL

Cold binding SQL resolves at most seven canonical/legacy alias candidates using
indexed lateral top-one lookups, then ranks that bounded result to preserve alias
precedence. The partial enabled-binding index includes the complete priority,
creation-time and ID ordering, avoiding a scan/sort of every equal-priority binding
on a hot scope. This adds one index per enabled binding on a control-plane table;
there is no spend/audit event write amplification. The coordinated additive index
migration has five-second lock and 30-second statement limits; retry a failed
migration in a maintenance window. Older readers remain compatible and rollback
can retain the index. Ordinary autovacuum/analyze and existing binding lifecycle
remain the owners of retention; this bounded lookup does not add historical rows.

## Notification contract migration

The prior direct `AlertService.send_budget_alert` and its Redis silence-slot path
are retired: production admission now has one durable producer and delivery has
one worker. Recipient/channel rendering stays in `AlertService`. The former
Redis-slot regression tests are replaced by real PostgreSQL coalescing, capacity,
lease/fence tests and worker/channel tests. Failed or uncertain external delivery
no longer releases a Redis key for blind replay. Tests retain opt-in behavior,
email/audit payloads, no-recipient and failed/cancelled email outcomes, recipient
lookup failure, Slack-only delivery and Redis-outage behavior under this contract.
Existing scheduled reporting remains on its background owner; admission contains
no report refresh.
