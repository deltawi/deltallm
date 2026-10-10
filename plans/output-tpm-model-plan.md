# Output TPM by model

## Scope and owner

Tier model policies set one output TPM allowance per organization and callable
model. The tier compiler selects the source with its existing assignment rules.
Team and key maps add limits for exact callable IDs. Each configured counter is
checked; a child limit cannot remove its parent limit. Existing caller totals
still cover all models. Shared capacity pools are a separate change.

## State and failure rules

Add nullable `output_tpm_limit` to tier model policies and nullable
`model_output_tpm_limit` maps to teams and keys. A map has at most 64 entries,
with at most 256 UTF-8 bytes per exact model ID. Values are integers from 1 to
2,147,483,647. Null clears the map. Updates keep the existing transaction and
cache invalidation outbox. Tier versions keep draft revisions and immutable
activation. The auth cache version changes when its projection changes.

Counter IDs contain the scope, entity, and callable ID. They exclude tier
versions and limit values. Existing scalar counter IDs stay the same. Admission
uses the final caller-facing model after request hooks. Provider fallbacks and
retries charge that model. Every dispatched provider attempt records its own
output once. Cache hits record no output. Streams use no per-frame I/O.

Use the existing fixed UTC minute, Redis Lua admission, completion accounting,
receipt, unknown-usage state, and cleanup owner. An admitted call can exceed the
limit; the next call is denied until the minute resets. Unknown dispatched
output closes the affected scopes for that minute. This remains a soft limit.

Enforced tier output limits require the existing tier and Redis failure modes
to be `fail_closed`. Shared limits require stored API-key authentication.
Startup, reload, admin writes, unsupported batch routes, and Realtime must
observe model-only policies as well as scalar policies.

## Work and verification

1. Add an append-only migration, typed validation, repository projections,
   tier clone/bulk support, and compiled limits.
2. Extend the one output policy builder and Lua bounds from four to seven
   scopes. Use local O(1) lookups. Add no hot-path SQL or Redis round trips.
3. Add Output TPM beside RPM and TPM in the tier editor. Add a shared small
   model-limit editor to team and key forms. Show the effective tier source.
   Preview separates admission from output exhaustion after completion.
4. Test model separation, parent sharing, explicit clears, tier changes,
   auth cache invalidation, unknown usage, retry, and unsupported endpoints.
   Run affected application, Redis, and PostgreSQL lanes, migration paths,
   UI checks, and before/after dependency and latency profiles.

The request bound is seven output scopes, one existing admission Lua call, and
at most one normal completion Lua write per positive or unknown attempt.
Redis keys remain bounded at 512 bytes and receipts at 4 KiB. No new tasks,
connections, pools, configuration switches, or dependencies are needed.
