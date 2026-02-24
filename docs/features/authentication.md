# Authentication & SSO

DeltaLLM supports multiple authentication methods that can be used independently or combined.

## Authentication Methods

### Master Key

The simplest auth method. Set a master key in your config and pass it as a Bearer token:

```yaml
general_settings:
  master_key: os.environ/DELTALLM_MASTER_KEY
```

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-your-master-key" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Hello"}]}'
```

The master key has full access to all endpoints, including admin operations.

### Virtual API Keys

Create scoped keys with budgets and rate limits through the admin UI or API. Virtual keys are hashed and stored in the database.

```bash
curl -X POST http://localhost:8000/ui/api/keys \
  -H "Authorization: Bearer MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"key_name": "production-app", "max_budget": 50.00, "models": ["gpt-4o-mini"]}'
```

#### Key Rotation, Revocation, and Auth Cache

DeltaLLM caches API key authentication lookups in Redis using a per-key cache entry (`key:{token_hash}`).

- **Regenerate** (`POST /ui/api/keys/{token_hash}/regenerate`) replaces the stored key hash in-place and invalidates the old key cache entry immediately.
- **Revoke** (`POST /ui/api/keys/{token_hash}/revoke`) deletes the key record and invalidates that key cache entry immediately.
- **Delete** (`DELETE /ui/api/keys/{token_hash}`) deletes the key record and invalidates that key cache entry immediately.

Invalidation is targeted to the affected key only; DeltaLLM does not flush unrelated Redis cache data for key lifecycle operations.

### Session-Based Login

The admin UI uses session-based authentication with email and password. On login, a secure `deltallm_session` cookie is set.

![Login Page](../admin-ui/images/login.png)

#### Bootstrap Admin Account

Set the initial admin credentials in your config:

```yaml
general_settings:
  platform_bootstrap_admin_email: admin@example.com
  platform_bootstrap_admin_password: os.environ/ADMIN_PASSWORD
```

The admin account is created on first startup.

#### Force Password Change

New accounts can be flagged to require a password change on first login:

```
POST /auth/internal/change-password
{
  "current_password": "temporary",
  "new_password": "secure-new-password"
}
```

### Multi-Factor Authentication (MFA)

Users can enroll in TOTP-based MFA for additional security.

1. **Start enrollment**: `POST /auth/mfa/enroll/start` — returns a TOTP secret and QR code URI
2. **Confirm enrollment**: `POST /auth/mfa/enroll/confirm` — verify with a TOTP code to activate
3. **Login with MFA**: Include the `mfa_code` field in the login request

## Single Sign-On (SSO)

DeltaLLM supports SSO with multiple identity providers.

### Supported Providers

| Provider | Config Value | Notes |
|----------|-------------|-------|
| Microsoft Entra (Azure AD) | `microsoft` | Uses Microsoft identity platform |
| Google Workspace | `google` | Uses Google OAuth 2.0 |
| Okta | `okta` | Uses Okta OIDC |
| Generic OIDC | `oidc` | Any OpenID Connect provider |

### SSO Configuration

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
  sso_scope: "openid email profile"
  sso_admin_email_list:
    - admin@company.com
```

### SSO Flow

1. User clicks "Sign in with SSO" on the login page
2. Redirected to the identity provider for authentication
3. After authentication, redirected back to `/auth/callback`
4. DeltaLLM validates the token, creates/links the account, and sets a session cookie
5. User is redirected to the dashboard

### Admin Auto-Assignment

Emails listed in `sso_admin_email_list` are automatically assigned the `platform_admin` role on their first SSO login.

## Role-Based Access Control (RBAC)

DeltaLLM uses a three-level RBAC hierarchy:

### Platform Roles

| Role | Description |
|------|-------------|
| `platform_admin` | Full access to all resources and settings |
| `platform_co_admin` | Administrative access without platform settings |
| `org_user` | Access scoped to assigned organizations only |

### Organization Roles

| Role | Permissions |
|------|-------------|
| `org_owner` | Full org management, keys, users |
| `org_admin` | Manage teams, keys, and users within org |
| `org_billing` | View keys and spend data |
| `org_auditor` | Read-only access to keys and users |
| `org_member` | Basic membership |

### Team Roles

| Role | Permissions |
|------|-------------|
| `team_admin` | Manage team members and keys |
| `team_developer` | Create and use API keys |
| `team_viewer` | Read-only access |

### Scope Resolution

- **Platform admins** see all resources across the entire platform
- **Org users** see only organizations, teams, keys, and users within their assigned organizations
- Resources are filtered automatically based on the user's role and assignments
