# Audit Log

DeltaLLM writes an append-only audit log to Postgres for:

- Control-plane actions (Admin UI + `/ui/api/*` endpoints)
- Selected data-plane actions (for example: batch/files, rerank, images, audio, spend)

Audit events are designed for operational troubleshooting and security review, and can be filtered/exported via the Admin Audit API.

## API Access

Audit read endpoints require an authenticated admin session or master key, and `Permission.AUDIT_READ` (`audit.read`).

`Permission.AUDIT_READ` is granted to:

- `platform_admin`
- `org_owner`
- `org_admin`

Access is denied to:

- `org_billing`
- `org_auditor`
- `org_member`
- Team-only memberships (`team_admin`, `team_developer`, `team_viewer`) without org admin/owner membership.

## Admin Audit API

All endpoints are under `/ui/api/audit/*`.

### List Events

`GET /ui/api/audit/events`

Query parameters:

| Parameter | Notes |
|----------|-------|
| `action` | Filter by action name (example: `BATCH_CREATE_REQUEST`) |
| `status` | Filter by status (example: `success`, `error`) |
| `actor_id` | Filter by actor identifier |
| `organization_id` | Filter to one organization |
| `request_id` | Filter by request id (from `X-Request-Id`) |
| `correlation_id` | Filter by correlation id (currently equals `request_id`) |
| `start_date` | Inclusive (UTC date) |
| `end_date` | Inclusive (UTC date) |
| `limit` | Default `100`, max `500` |
| `offset` | Default `0` |

Response shape:

```json
{
  "events": [{ "...": "..." }],
  "pagination": { "total": 0, "limit": 100, "offset": 0, "has_more": false }
}
```

### Fetch One Event (With Payloads)

`GET /ui/api/audit/events/{event_id}`

Returns a single event plus `payloads` (request/response payloads, if stored).

### Timeline View

`GET /ui/api/audit/timeline?request_id=...` (or `correlation_id=...`)

Returns all events in chronological order for a request/correlation.

### Export

`GET /ui/api/audit/export?format=jsonl|csv`

Supports the same filters as `GET /ui/api/audit/events`, with a higher default `limit` (and max `10000`).

`action` values map to the centralized registry in `src/audit/actions.py` (`AuditAction`).
Prefer referencing that registry over documenting scattered literal action strings.

## Event Fields

Common fields include:

- `event_id`, `occurred_at`
- `organization_id`
- `actor_type`, `actor_id`, `api_key`
- `action`
- `resource_type`, `resource_id`
- `request_id`, `correlation_id`
- `ip`, `user_agent`
- `status`, `latency_ms`
- `input_tokens`, `output_tokens`
- `error_type`, `error_code`
- `metadata`
- `content_stored` (whether any request/response payload content was stored)

When fetching a single event, you also get:

- `prev_hash`, `event_hash` (reserved for future tamper-evidence)
- `payloads[]` with:
  - `kind` (`request` or `response`)
  - `storage_mode` (currently `inline`)
  - `content_json` (may be `null` if content is not stored)
  - `storage_uri`, `content_sha256`, `size_bytes`
  - `redacted` (true when content was intentionally omitted)

## Payload Storage + Redaction

- Control-plane audit payloads (Admin UI and authentication flows) are stored with sensitive fields redacted (passwords, tokens, secrets, etc).
- Data-plane payload storage is gated per organization:
  - If the organization has `audit_content_storage_enabled = true`, request/response payloads may be stored.
  - If it is `false` (default), payloads are recorded as metadata-only and `content_json` is omitted/redacted.

## Retention

Audit retention is enforced by a background cleanup loop.

Global defaults are configured in `general_settings`:

- `audit_metadata_retention_days`
- `audit_payload_retention_days`

These can be overridden per organization via org metadata keys:

- `metadata.audit_metadata_retention_days`
- `metadata.audit_payload_retention_days`
