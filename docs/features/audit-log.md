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
| `GET /ui/api/audit/timeline` | Show all events for a request or correlation id |
| `GET /ui/api/audit/export?format=jsonl|csv` | Export filtered results |

Useful filters on `GET /ui/api/audit/events` include:

- `action`
- `status`
- `actor_id`
- `organization_id`
- `request_id`
- `correlation_id`
- `start_date`
- `end_date`

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

## Payload Storage and Redaction

Audit payloads are handled differently depending on the event type:

- control-plane payloads are redacted for sensitive values such as passwords, tokens, and secrets
- data-plane payload content is stored only when the organization has `audit_content_storage_enabled = true`
- when content storage is disabled, the event is still recorded but request and response bodies are omitted or marked redacted

## Retention

Audit retention runs in the background.

Global defaults:

- `general_settings.audit_metadata_retention_days`
- `general_settings.audit_payload_retention_days`

Per-organization overrides:

- `metadata.audit_metadata_retention_days`
- `metadata.audit_payload_retention_days`

## Related Pages

- [Admin UI: Audit Logs](../admin-ui/audit-logs.md)
- [Admin Endpoints](../api/admin.md)
- [Observability](observability.md)
