# Managing API Keys

The API Keys page lets you create, manage, and revoke virtual API keys.

## Creating a Key

Click **Create Key** and configure:

| Field | Description |
|-------|-------------|
| Key Name | A human-readable label for the key |
| Max Budget | Maximum spend allowed (USD). Leave empty for unlimited |
| Models | Restrict which models the key can access. Empty = all models |
| Team | Assign the key to a team |
| RPM Limit | Requests per minute limit |
| TPM Limit | Tokens per minute limit |
| Expires | Expiration date (optional) |

After creation, the raw API key is shown once — copy it immediately as it cannot be retrieved later.

## Key Details

The keys table shows:

- Key name and truncated token hash
- Associated team and user
- Current spend vs. budget
- Rate limits
- Creation date and expiry

## Operations

| Action | Description |
|--------|-------------|
| **Edit** | Update budget, rate limits, models, or team assignment |
| **Regenerate** | Issue a new raw key while keeping the same configuration |
| **Revoke** | Permanently disable the key |
| **Delete** | Remove the key entirely |

## Scoped Access

Key visibility depends on the user's role:

- **Platform admins** see all keys
- **Organization users** see keys belonging to teams within their assigned organizations
