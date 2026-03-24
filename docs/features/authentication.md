# Authentication & SSO

DeltaLLM supports API authentication, browser-based admin sessions, MFA, SSO, and RBAC.

## Choose the Simplest Working Method

| Need | Start with |
|------|------------|
| Call the proxy API quickly | Master key |
| Give an app limited access | Virtual API key |
| Sign in to the Admin UI | Bootstrap admin account |
| Use company identity | SSO |

## Quick Success Path

1. Set a valid `DELTALLM_MASTER_KEY`
2. Start the gateway
3. Use the master key to make one successful API call
4. Create a scoped virtual API key for real applications
5. Add session login, MFA, or SSO only when you need them

## Master Key

The master key is the fastest way to get started. It has full access to proxy and admin endpoints.

```yaml
general_settings:
  master_key: os.environ/DELTALLM_MASTER_KEY
```

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer YOUR_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Hello"}]}'
```

## Virtual API Keys

Virtual keys are the right choice for applications because they can be scoped and limited.

```bash
curl -X POST http://localhost:8000/ui/api/keys \
  -H "Authorization: Bearer YOUR_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "key_name": "production-app",
    "team_id": "team-production",
    "max_budget": 50.00
  }'
```

Virtual keys can include:

- spend limits
- RPM and TPM limits
- team or organization ownership

Runtime model and route-group access is governed separately through callable-target bindings and scope policies. Keys no longer carry model allowlists on the key record.

### Rotation and Revocation

DeltaLLM stores virtual keys hashed in the database and caches auth lookups in Redis.

- regenerate replaces the current key and invalidates the old cache entry
- revoke removes the key and invalidates that cache entry
- delete removes the key record and invalidates that cache entry

## Session-Based Login for the Admin UI

The Admin UI uses a secure `deltallm_session` cookie.

To create the first admin account, set:

```yaml
general_settings:
  platform_bootstrap_admin_email: admin@example.com
  platform_bootstrap_admin_password: os.environ/ADMIN_PASSWORD
```

That bootstrap account is created automatically on first startup when both values are present.

### Login Endpoint

```text
POST /auth/internal/login
```

The login response sets the session cookie used by the UI.

### Password Change

```text
POST /auth/internal/change-password
```

Use this when an account is marked to change its password after first login.

## Multi-Factor Authentication

DeltaLLM supports TOTP-based MFA for session logins.

1. `POST /auth/mfa/enroll/start`
2. `POST /auth/mfa/enroll/confirm`
3. Include `mfa_code` during `/auth/internal/login`

## Single Sign-On

DeltaLLM supports:

- Microsoft Entra
- Google
- Okta
- generic OIDC

### Minimal SSO Configuration

```yaml
general_settings:
  enable_sso: true
  sso_provider: microsoft
  sso_client_id: os.environ/SSO_CLIENT_ID
  sso_client_secret: os.environ/SSO_CLIENT_SECRET
  sso_authorize_url: https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize
  sso_token_url: https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token
  sso_userinfo_url: https://graph.microsoft.com/oidc/userinfo
  sso_redirect_uri: https://your-domain.com/auth/callback
```

### How the SSO Flow Works

1. The user starts login from the UI
2. DeltaLLM redirects to the identity provider
3. The provider redirects back to `/auth/callback`
4. DeltaLLM creates or updates the platform account and sets a session cookie

### Auto-Assign Platform Admins

Add emails to `sso_admin_email_list` to grant `platform_admin` on first SSO login.

## Role-Based Access Control

DeltaLLM separates platform roles, organization roles, and team roles.

### Platform Roles

| Role | Meaning |
|------|---------|
| `platform_admin` | Full platform access |
| `org_user` | Access limited to assigned organizations |

### Organization Roles

| Role | Typical access |
|------|----------------|
| `org_owner` | Full organization control |
| `org_admin` | Manage teams, users, and keys |
| `org_billing` | Spend-focused visibility |
| `org_auditor` | Read-only operational visibility |
| `org_member` | Basic organization membership |

### Team Roles

| Role | Typical access |
|------|----------------|
| `team_admin` | Manage the team and its keys |
| `team_developer` | Use and create keys, self-service key creation (`key.create_self`) |
| `team_viewer` | Read-only access |

The `team_developer` role includes the `key.create_self` permission, which allows developers to create, regenerate, revoke, and delete their own API keys when the team has self-service enabled. See [API Keys: Self-Service](../admin-ui/api-keys.md#self-service-key-creation) for details.

### Important Note on `user_role`

`deltallm_usertable.user_role` is metadata for user profile types in the UI. It is not the main authorization source of truth.

## Related Pages

- [Admin UI](../admin-ui/index.md)
- [API Reference](../api/index.md)
- [Rate Limiting](rate-limiting.md)
