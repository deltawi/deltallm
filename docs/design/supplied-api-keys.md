# Supplied API keys

## Contract and scope

`POST /ui/api/keys` accepts optional `key`. An omitted or null value keeps
random key generation. A supplied value is used without a change. It must
start with `sk-`, contain 16 to 8192 printable ASCII characters, contain no
spaces, and differ from the active master key. Invalid input returns 400.
An active or previously used value returns a generic 409 without owner or
tenant information. A retry after a successful create also returns 409;
creation does not update an existing key.

The response keeps `raw_key` for the one-time secret and `token` for its salted
hash. Only the hash is stored. All current permissions, owner checks, team
locks, active organization checks, self-service quotas, budgets, expiry rules,
and rate limits still apply. Supplied values are available through the API
only. The UI continues to generate keys. Editing and regeneration keep their
current contracts. LiteLLM endpoint aliases and configuration options are
outside this change.

## Ownership and durable invariants

The key-material service owns selection and validation. The creation repository
owns insertion. PostgreSQL remains the source of truth. A hash history table
and a trigger on key insertion or token replacement prevent reuse across all
replicas and all writers, including older application versions. A failed
transaction rolls back both the key and its hash reservation. Deletion and
regeneration retain old hashes. This prevents old clients, cache records,
invalidation jobs, or accounting records from acquiring a new key identity.
No new SQL or Redis calls are added to inference authentication.

The history table stores one salted hash and timestamp for each issued key.
It has one primary index, no tenant references, and no public read endpoint.
Keep these records permanently. Use normal PostgreSQL vacuum and analyze;
no partitioning is needed for the control-plane key issuance rate. Capacity
grows with issued keys, not requests. Key issuance keeps the current database
pool and transaction limits. An unavailable database fails creation closed.

Audit redaction covers `key`, `raw_key`, and `new_key` on success and failure.
Errors do not echo input or database details. API responses expose the secret
only at creation and use `Cache-Control: no-store`. The UI does not offer an
input for supplied values.

## Implementation plan

1. Add the key-material service, typed insertion record, and creation repository.
2. Add append-only hash history and the key-write trigger. Backfill current
   keys and recoverable hashes from retained audit, usage, and invalidation
   history. Map the trigger conflict to a safe 409.
3. Connect creation to the service without a second authorization path.
   Extend central audit redaction.
4. Add the typed API request and update the OpenAPI artifact. Keep generated
   keys as the default and keep all UI code unchanged.
5. Document the contract and import procedure. Add service, route,
   PostgreSQL concurrency, rollback, deletion, rotation, and migration tests.
6. Run focused checks, the affected application and dependency suites, UI
   tests/build, migration verification, and a review loop. Fix all P2
   or higher findings before delivery.

## Migration, deployment, and rollback

Apply the additive migration before the new application. The migration uses
a short key-table write lock while it installs the trigger and copies active
hashes. Inference reads remain available. Historical scans run outside that
lock. Use the existing coordinated migration job, never per-replica DDL.
The trigger permits unrelated edits and rejects reuse by older writers too.

History that was deleted before this migration cannot be recovered. Import
only active source credentials. Copy the original secret from LiteLLM;
DeltaLLM cannot use a LiteLLM hash because its salt and hashing differ.
Check migration duration on a production-size copy before rollout.

Rollback the application while keeping the additive table and trigger.
Stop supplied-key creation clients before rollback because the old API
ignores that field. Existing supplied keys still authenticate through the
old hash path. Do not drop history or disable the trigger during rollback.
No compatibility lifecycle needs later removal.

## Acceptance evidence

Verify omitted/null generation, exact supplied values, authentication, invalid
types and formats, master-key rejection, unchanged tenant policy, safe audit
data, and no secret in validation or database errors. Against real PostgreSQL,
prove one winner under concurrent duplicate creation, rollback release,
permanent refusal after delete and rotation, and fresh/upgrade migration paths.
Verify that no UI source or UI API client changes are included. Record
commands and results at delivery.

## Verification record

The implementation and review loop are complete. The final source review
has no remaining P2 or higher findings. No UI source or UI API client changes
are included.

The review found one P2 issue: a request with a secret inside an array or
string body caused a validation error that echoed the secret. The request
model now refuses a body with the wrong shape with a safe 400 response.
Three regression cases failed before the fix and pass after it.

- `python -m pytest -q tests/test_supplied_api_keys.py`: 33 passed.
- `python -m pytest -q -m app`: 1928 passed.
- `python -m pytest -q -m redis`: 261 passed, 1 skipped.
- The first affected PostgreSQL and Redis selection: 33 passed. This covered
  concurrent duplicate writes, deletion, rotation, transaction rollback,
  legacy hash recovery, last-use tracking, and auth revocation.
- The review selection against native PostgreSQL 15: 73 passed. This covered
  supplied-secret authentication through the repository, conflict mapping,
  legacy upgrade, concurrency, rollback, key removal, organization removal,
  coding-agent keys, budgets, output policy, and SSO group changes.
- `python -m scripts.verify_migration_paths` against native PostgreSQL 15:
  fresh installation, last-release upgrade, shared-feature upgrade, and
  model-identity recovery checks all passed.
- `npm --prefix ui run test:unit`: 533 passed on the unchanged UI source.
- `npm --prefix ui run build`: passed.
- Ruff check and format check on all changed Python files: passed.
- `python -m scripts.docs.export_openapi --check`: passed.
- Full Python collection and dependency-lane checks: passed.
- `git diff --check`: passed.

Earlier full PostgreSQL runs did not complete with a clean result. Runs had
local database connection failures and batch test failures. The batch owner
query-plan failure also occurs on unchanged main. The native PostgreSQL
review run confirms the affected key behavior and all migration paths that
the earlier Docker runs could not verify. Run the full PostgreSQL CI gate
before rollout. Full UI lint has the same existing 27 errors and 1 warning
as main.
