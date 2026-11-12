# Audit Log

DeltaLLM writes audit events to Postgres for admin actions and selected runtime operations so operators can investigate changes, access, and failures later.

## Quick Path

Audit logging is enabled by default. To confirm it is working:

1. Sign in to the Admin UI or use the master key
2. Perform an action such as creating a key or running a batch job
3. Open [Audit Logs](../admin-ui/audit-logs.md) or call the audit API
4. Filter by `request_id`, actor, or action

List recent events:

```bash
curl http://localhost:8000/ui/api/audit/events \
  -H "Authorization: Bearer YOUR_MASTER_KEY"
```

## What Gets Audited

DeltaLLM records:

- control-plane actions from the Admin UI and `/ui/api/*`
- selected data-plane actions such as files, batches, rerank, images, audio, and spend access
- self-service key operations performed by team developers

The audit log is meant for operational review, security investigations, and compliance workflows.

For production, set `audit_ingestion_mode: outbox`.
In the default `legacy` mode, the system stores required audit events and prompt-render records synchronously.
It fails closed when persistence is unavailable.
Only best-effort audit events use the bounded, non-durable compatibility queue.

Durable required events keep a server-owned ID and use renewable fenced claims.
After all retry attempts fail, these events move to `blocked`. The system does not delete them.
Platform administrators can replay an investigated blocked record through the telemetry-ingestion replay endpoint.
See the [rollout guide](../deployment/telemetry-ingestion-rollout.md).
The replay and its required operator audit commit in the same database transaction.

Best-effort audit records are not authoritative.
If queue capacity or an audit dependency is unavailable, the system drops and counts the best-effort record.
This does not change a request, MCP tool, notification, or callback result.
Required audit still fails closed with a controlled service-unavailable response.
Before it does, a required enqueue that hit a lock or connection-pool wait past its bound is retried once after a short random delay.
The event ID stays the same, so the retry cannot create a duplicate. Outages, such as a lost database connection, are not retried.
Each retry increments `deltallm_audit_write_failures_total` with a `*_contention_retry` path.
MCP tool attempts require audit events.
If the audit service is unavailable, the gateway rejects the attempt before it calls the remote tool transport.

### Self-Service Key Audit Actions

When team developers create or manage their own keys, DeltaLLM records distinct audit actions:

| Action | When |
| --- | --- |
| `ADMIN_KEY_SELF_CREATE` | Developer creates a self-service key |
| `ADMIN_KEY_SELF_ROTATE` | Developer regenerates their own key |
| `ADMIN_KEY_SELF_REVOKE` | Developer revokes their own key |

These events include the `actor_id` (the developer's account ID) and `resource_id` (the key token hash), allowing admins to trace all self-service activity.

## Who Can Read It

Audit access requires the master key or an authenticated admin session with `audit.read`.

That includes:

- platform admins
- org owners
- org admins
- org auditors

Team-only roles do not get audit access by themselves.

## Main Endpoints

All audit endpoints are under `/ui/api/audit/*`.

| Endpoint | Purpose |
| --- | --- |
| `GET /ui/api/audit/events` | List events with filters and pagination |
| `GET /ui/api/audit/events/{event_id}` | Get one event, including stored payload records |
| `GET /ui/api/audit/events/{event_id}/payloads` | Get only the stored payload records of one event |
| `GET /ui/api/audit/events/{event_id}/related` | Events around one event: same request, same resource, recent sign-ins |
| `GET /ui/api/audit/actions` | Every known action with its category |
| `GET /ui/api/audit/organizations` | Organizations whose audit log the caller can read |
| `GET /ui/api/audit/timeline` | Show all events for a request or correlation id |
| `GET /ui/api/audit/export?format=jsonl|csv` | Export filtered results |

Useful filters on `GET /ui/api/audit/events` include:

- `category` (`admin`, `auth`, `api`, `mcp`, `spend`, `system`)
- `action`
- `status`
- `actor` (an email, API key name, key hash, or actor ID) and `actor_type`
- `organization_id`
- `resource_type` and `resource_id`
- `ip`
- `request_id`
- `correlation_id`
- `search` (an event ID, request ID, or resource ID)
- `since` and `until` (timestamps), or `start_date` and `end_date` (whole days)

Pass `count=false` to skip the total count and page with `cursor`. See the
[Audit API](../api/admin.md#audit) for paging and the `enrich` and `context` options.

## Event Shape

Common event fields include:

- `event_id`
- `occurred_at`
- `organization_id`
- `actor_type`
- `actor_id`
- `action`
- `resource_type`
- `resource_id`
- `request_id`
- `correlation_id`
- `status`
- `latency_ms`
- `input_tokens`
- `output_tokens`
- `error_type`
- `error_code`
- `metadata`
- `content_stored`

When you fetch one event directly, DeltaLLM also returns any stored `payloads`.

### Failed provider calls with accounting v2

With accounting v2, each accounting operation writes one `ACCOUNTING_PROVIDER_FINALIZATION`
event when it closes. One operation can include more than one provider call, for example a
retry, a failover to another deployment, or the model calls of one MCP tool request. They
share one event.

When the operation succeeds, the event has `status` `success`. Its `input_tokens` and
`output_tokens` are the `prompt_tokens` and `completion_tokens` of the charge, from the
response usage or, if the usage does not have them, from the billing usage snapshot. A count
that the charge does not have stays empty (`null`). It is not shown as 0. This applies to
every charge: live calls, Realtime sessions, batch items and model selector calls.

When the operation fails, the event has `status` `error`, and `error_type` tells why:

- `AccountingProviderNotDispatched`: the gateway sent none of the calls, so it released
  the reserved amount.
- `AccountingProviderRefused`: the provider refused each call that the gateway sent,
  with status 400, 401, 403, 404 or 422, and the gateway read each error body. The
  provider did no work, so the gateway released the reserved amount.
  `metadata.failure_reason` tells why, for example `invalid_request_error`, and
  `metadata.refused_attempts` is the number of refused calls. A content-policy
  refusal is not in this group. An error whose body the gateway could not read (for
  example a compressed or oversized body) is not in this group either, because the
  gateway cannot rule out a content-policy refusal. See
  [Failure semantics](../deployment/accounting-v2.md#failure-semantics).
- `AccountingOutcomeUnknown`: the gateway sent a call, and it does not know the reason,
  for example for a batch item whose result is unknown. It keeps the reserved amount as a
  provisional charge.
- Any other value: the gateway sent a call, and the call failed or its charge could not be
  proven. The value is the reason, for example `service_unavailable` for a provider 503,
  and `metadata.uncertainty_reason` has the same value. The gateway cannot prove whether
  the provider charged for the call, so it keeps the reserved amount as a provisional
  charge.

If a pod stops after it sent a call and before it records the result, the operation has no
`ACCOUNTING_PROVIDER_FINALIZATION` event:

- For a stored operation, the gateway closes it when its reservation expires, with an
  `ACCOUNTING_PROVIDER_OUTCOME_UNKNOWN` event whose `error_type` is
  `AccountingReservationExpired`. It keeps the reserved amount as a provisional charge.
- With `accounting_execution_mode: local_journal`, the operation can exist only in the
  stopped pod. Then the gateway closes its budget grant, keeps the remainder in the
  grant's `unknown_provisional_exact`, and writes no audit event. See
  [Provisional reconciliation](../deployment/accounting-v2.md#provisional-reconciliation).

So the two actions show the failed calls that the gateway stored, but not these grant
remainders.

## Payload Storage and Redaction

Audit payloads are handled differently depending on the event type:

- control-plane payloads are redacted for sensitive values such as passwords, tokens, and secrets
- data-plane payload content is stored only when the organization has `audit_content_storage_enabled = true`
- One PostgreSQL transaction disables content storage and redacts active or blocked outbox envelopes.
  Legacy and durable writers acquire the same organization policy lock.
  Writers that only read the policy share that lock, so a worker batch never delays a request's audit event.
  Changing the policy takes the lock exclusively and waits for those readers to finish.
  After lock acquisition, they read policy in a fresh statement.
  Durable workers also remove prohibited content from claimed envelopes before completion.
- Redis invalidation removes stale policy-cache entries across replicas, but privacy correctness does not depend on Pub/Sub availability
- when content storage is disabled, the event is still recorded but request and response bodies are omitted or marked redacted

### Content that arrives after its event

With accounting v2, the accounting worker writes the audit event of a data-plane request, and that event
has no request or response body. The bodies arrive separately as an `audit_content` outbox record that
names the event. Each API pod sends these records from a buffer, as described below.

The audit worker processes the record in the same batched transaction as other audit records:

- If the event exists and belongs to the record's organization, the worker adds the bodies to it as
  payloads and sets `content_stored` to `true`.
- If the event does not exist yet, the worker puts the record back in the queue. This wait does not use
  up a delivery attempt. The first wait is 2 seconds. Each later wait is half the age of the record, at
  most 30 seconds, so a record whose event never arrives is claimed about 18 times.
- If the event does not exist after 5 minutes, the worker writes the bodies as a separate event with the
  record's own ID, so the content is not lost.
- If the event belongs to a different organization, the worker never adds the bodies to it. It writes
  them at once as a separate event in the record's organization.
- If the organization does not have `audit_content_storage_enabled = true`, the worker writes nothing.
- If the event stored in the record names an organization other than the record's organization, the
  worker rejects the record before it writes anything. The record goes back to the queue with an error
  and fails after its delivery attempts. Bodies never go into an organization whose content policy the
  worker did not check.

`deltallm_audit_content_records_total{outcome}` counts the records by `attached`, `deferred`,
`fallback_event` and `organization_mismatch`. A high `deferred` count means that the accounting
projection is slow. An `organization_mismatch` count is not expected; it shows a defect in the sender.

Each API pod sends these records from a small in-memory buffer, so the request does not wait for the
database:

- The request only adds its bodies to the buffer. It does no database I/O for this step, and this step
  never fails the request. When the pod already knows that the organization has content storage off, the
  bodies do not go into the buffer.
- The stored request is the request as the gateway sent it to the provider: after prompt
  templates, pre-call hooks and guardrails. For chat, completions, Responses, Messages and
  embeddings requests, the request checks already turn this request into JSON text for the token
  estimate; the buffer keeps that text and counts its length. So a large request (many tools, a long
  conversation, a long system prompt added by a template) is counted at its real size and costs no
  extra work.
- The buffer keeps its own copy of the bodies. A post-call hook or guardrail that changes the request
  or response after the hand-off (with streaming, post-call hooks run after it) does not change what
  is stored or how much memory is counted. Other bodies, for example responses, are measured and
  copied in one pass that visits at most 16,384 values (strings, numbers and other JSON values; a
  copied list or object counts twice) for each body. Strings are shared, not copied. This takes about
  2 to 3 ms for large real bodies (embeddings responses, long document lists), and at most about 5 ms
  for a body made only of empty lists or objects.
- A body with more values than that is replaced by a placeholder, and the other body is kept. When
  the lengths of its lists and objects show that the body is surely larger than the payload limit
  (1 MiB by default), for example an embeddings response with many vectors, it gets the same
  placeholder that compaction would write. Otherwise it gets
  `{"omitted": "payload_too_many_values", "max_values": 16384}`.
- Response cache hits send content too: the request as the request checks left it, and the cached
  response (for a replayed stream, the complete chat completion that the cache stored). The audit
  worker attaches them to the event of the cached charge.
- One background task per pod sends the buffer every second, or earlier when 500 items wait. It reads the
  content policy of uncached organizations in one query, then writes one statement for each organization
  (at most 100 records or 1 MiB each). The 1 MiB limit applies to the statement as it is sent. When the
  bodies of one request are together too large for one statement, the pod compacts them again with a
  smaller limit, so a large body becomes a placeholder. It runs one statement at a time, so it uses at
  most one connection of the audit pool.
- The content is best effort. It uses the best-effort queue capacity, which keeps the required reserve
  free for required audit events. The pod does not retry a failed send.

Content is dropped, and the audit event stays without bodies, in these cases. Each drop increments
`deltallm_audit_content_dropped_total{reason}`:

| Reason | Cause |
| --- | --- |
| `buffer_full` | 2,000 items already wait in the buffer of this pod. |
| `buffer_bytes` | The bodies that wait or are being sent on this pod already have about 128 MiB of characters, measured before compaction. |
| `flush_bytes` | One send is larger than 64 MiB after compaction. |
| `record_bytes` | The record of one request does not fit in one 1 MiB statement, even after the pod compacts it again. |
| `payload_values` | A body that the request checks did not turn into JSON text has more than 16,384 values. Only this body is replaced by a placeholder; the rest of the content is sent. |
| `policy_unavailable` | The policy read failed. |
| `enqueue_failed` | The database write failed, for example because the database is busy. The pod drops the rest of the send. |
| `queue_full` | The audit queue has no best-effort capacity. The pod then stops the send and drops the rest of it. |
| `duplicate` | The same audit event already has a content record. Only the first one is kept. |
| `closed`, `shutdown` | The pod is stopping. It tries one final send of at most 2 seconds. |

### Inline files and large payloads

Before an audit event is accepted, DeltaLLM shrinks its request and response payloads. This keeps the
write inside the short request-path database budget, so vision, OCR and audio requests are not refused
with `audit_persistence_unavailable`:

- inline base64 data (data URLs such as `data:image/png;base64,...`, and long raw base64 values such as
  Anthropic `source.data`, `input_audio.data` or image `b64_json`) is replaced by a short marker:
  `[omitted inline data: image/png, 5242904 chars, sha256=...]`. The SHA-256 is computed over the original
  string, so you can still match a stored event to a known file. Remote URLs and normal text are kept as-is.
- if a payload is still larger than `general_settings.audit_payload_max_bytes` (default 1 MiB, measured as
  an approximate JSON size), it is replaced by
  `{"omitted": "payload_too_large", "approx_size_bytes": ..., "max_bytes": ...}`.

The audit event itself (actor, model, status, tokens, latency) is always recorded.

## Retention

Audit retention runs in the background.

Global defaults:

- `general_settings.audit_metadata_retention_days`
- `general_settings.audit_payload_retention_days`

Per-organization overrides:

- `metadata.audit_metadata_retention_days`
- `metadata.audit_payload_retention_days`

Each pass deletes up to `general_settings.audit_retention_scan_limit` expired
payloads and up to the same number of expired events, oldest first. Passes run
every `general_settings.audit_retention_interval_seconds`. Each delete is one
SQL statement. It works when the audit ID columns are `uuid` (created by
migrations) or `text` (created by `prisma db push`). More than one replica can
run retention at the same time, because the statement skips rows that another
replica has locked.

If a pass fails, the worker logs `audit retention pass failed` with the error
and tries again at the next interval. A failed pass does not stop retention.

## Upgrade Note: Resource History Index

The Audit Logs event panel lists recent events on the same resource. A migration adds the index
`deltallm_auditevent_resource_occurred_at_idx` on `(resource_type, resource_id, occurred_at)`.
The migration runs in a transaction and blocks audit writes while it builds. On a large audit table,
create the index concurrently before you deploy, so the migration finds it and does nothing:

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS "deltallm_auditevent_resource_occurred_at_idx"
  ON "deltallm_auditevent"("resource_type", "resource_id", "occurred_at");
```

If the migration times out because the table is busy, create the index as above, mark only that
attempt rolled back, and deploy again:

```bash
uv run prisma migrate resolve --rolled-back 20261014120000_audit_resource_occurred_index \
  --schema prisma/schema.prisma
uv run prisma migrate deploy --schema prisma/schema.prisma
```

## Related Pages

- [Admin UI: Audit Logs](../admin-ui/audit-logs.md)
- [Admin Endpoints](../api/admin.md)
- [Observability](observability.md)
