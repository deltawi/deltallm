# Alert destinations

DeltaLLM sends governance alerts to email. It can also send them to Slack, Microsoft Teams, Discord, or a signed webhook that you operate. Each destination has a name and a list of the alert types that it receives.

## Alert types

| Alert type | When DeltaLLM sends it |
| --- | --- |
| `budget_threshold` | An organization reaches its soft budget. |
| `api_key_lifecycle` | An API key is created, regenerated, revoked, or deleted. |

The alert switches control which alerts DeltaLLM makes. Destinations control where the alerts go. Set the switches first:

```yaml
general_settings:
  governance_notifications_enabled: true
  budget_notifications_enabled: true
  key_lifecycle_notifications_enabled: true
```

## Configure destinations

Add destinations to `general_settings.alert_destinations` in `config.yaml`. Use `os.environ/NAME` for each URL and signing secret. Do not write a webhook URL in the file, because the URL is a credential.

```yaml
general_settings:
  alert_destinations:
    - name: finance-teams
      type: teams
      url: os.environ/TEAMS_FINANCE_WEBHOOK_URL
      alert_types: [budget_threshold]
    - name: platform-discord
      type: discord
      url: os.environ/DISCORD_PLATFORM_WEBHOOK_URL
      alert_types: ["*"]
    - name: ops-pager
      type: webhook
      url: os.environ/OPS_ALERT_WEBHOOK_URL
      signing_secret: os.environ/OPS_ALERT_SIGNING_SECRET
      alert_types: [budget_threshold, api_key_lifecycle]
```

| Field | Description |
| --- | --- |
| `name` | 1 to 32 characters: lowercase letters, digits, `-`, and `_`. It must start with a letter or digit. Names must be unique. You cannot use `email`, `slack`, or `none`. |
| `type` | `slack`, `teams`, `discord`, or `webhook`. |
| `url` | The incoming webhook URL. It must start with `https://`. |
| `signing_secret` | Required for `webhook`, with 32 or more characters. Do not set it for the other types. |
| `alert_types` | The alert types that this destination receives. Use `"*"` for all types. |

You can configure a maximum of 32 destinations. Destinations are read only at startup. Restart DeltaLLM after you change them.

The legacy `slack_alerting_enabled`, `slack_webhook_url`, and `slack_alert_kinds` settings continue to work. For new Slack routes, use a destination with `type: slack`.

### Message formats

| Type | Body |
| --- | --- |
| `slack` | A Slack incoming webhook message with a `text` field. |
| `teams` | A message with one Adaptive Card. Use the URL of a Teams workflow that starts with "When a Teams webhook request is received". |
| `discord` | A Discord webhook message, a maximum of 2000 characters. Mentions are disabled. |
| `webhook` | A signed JSON event. See [Verify webhook events](#verify-webhook-events). |

## Verify webhook events

A `webhook` destination receives a JSON event:

```json
{
  "id": "alert_3f0c6b0e9c1d4b52a1e0b0f6d2a4c7e9",
  "type": "budget_threshold",
  "kind": "budget_threshold",
  "created_at": "2026-10-30T09:15:02.481000+00:00",
  "data": {"entity_type": "org", "entity_id": "org-1", "current_spend": 81.5, "soft_budget": 80, "hard_budget": 100}
}
```

Each request has these headers:

| Header | Value |
| --- | --- |
| `X-DeltaLLM-Event-Id` | The event `id`. `Idempotency-Key` has the same value. |
| `X-DeltaLLM-Event-Type` | The alert type. |
| `X-DeltaLLM-Timestamp` | Unix time in seconds when DeltaLLM signed the event. |
| `X-DeltaLLM-Signature` | `v1=` followed by the hex HMAC-SHA256 of `<timestamp>.<raw body>`, with the signing secret as the key. |

Batch webhooks use the same signature format. To verify an event:

1. Read the raw request body before you parse it.
2. Calculate the HMAC-SHA256 of the timestamp, a `.`, and the raw body.
3. Compare the result with the signature in constant time.
4. Reject the event if the timestamp is more than 5 minutes old.
5. Use the event `id` to ignore a duplicate. DeltaLLM can send an event again after a failure.

```python
import hashlib
import hmac
import time


def verify(body: bytes, headers: dict[str, str], secret: str) -> bool:
    timestamp = headers["X-DeltaLLM-Timestamp"]
    if abs(time.time() - int(timestamp)) > 300:
        return False
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return hmac.compare_digest(f"v1={expected.hexdigest()}", headers["X-DeltaLLM-Signature"])
```

## Delivery

- DeltaLLM sends an alert to all matching destinations at the same time.
- Each attempt has a 2 second limit. DeltaLLM tries again one time after a timeout, a connection error, or status 408, 429, or 5xx.
- Only a 2xx status is a success. DeltaLLM does not follow redirects. A redirect is a failure.
- A failed destination does not stop delivery to email or to other destinations.
- Each send writes a notification audit record with the destination name, the type, and the HTTP status. The URL is not written to logs or audit records.
- The `deltallm_notification_enqueue_total` metric counts sends. The `channel` label is the destination name.

## Outbound network policy

Destination URLs go through the same outbound checks as other operator-configured URLs. DeltaLLM resolves the host name, refuses private, loopback, link-local, and cloud metadata addresses, and connects only to the address that it checked.

| Setting | Default | Description |
| --- | --- | --- |
| `alert_destinations_allow_http` | `false` | Permit `http://` URLs. Use this only for a local test receiver. |
| `alert_destinations_allowed_ports` | `[443]` | Ports that destination URLs can use. |
| `alert_destinations_allowed_private_cidrs` | `[]` | Private networks that destinations can reach, for example an internal receiver at `10.20.0.0/16`. Cloud metadata addresses stay blocked. |

These settings are read only at startup.

## Send a test alert

Platform administrators can send a test alert to one destination. The test does not use routing or deduplication.

```bash
curl -X POST https://gateway.example.com/ui/api/alerts/test \
  -H "Authorization: Bearer $DELTALLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"destination": "finance-teams"}'
```

```json
{"delivered": true, "destination": "finance-teams", "type": "teams", "status_code": 202, "error": null}
```

If delivery fails, `delivered` is `false` and `error` gives the reason, for example `http_403`, `timeout`, or `policy_resolved_address_not_allowed`. A test event to a `webhook` destination has the type `alert_test`. Each test writes an `ADMIN_ALERT_TEST` audit record.

`GET /ui/api/alerts/destinations` lists the configured destinations with their names, types, and alert types. It does not show URLs or secrets.

## Settings API

`GET /ui/api/settings` masks the `url` and `signing_secret` of each destination. A `PUT /ui/api/settings` that sends the masked list back without changes is accepted. A changed list returns `409` with the code `restart_required`.
