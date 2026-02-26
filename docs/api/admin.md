# Admin Endpoints

The admin API provides endpoints for managing the gateway through the UI or programmatically.

All admin endpoints require authentication with either a master key or an active session.

## Models

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/ui/api/models` | List all model deployments |
| `POST` | `/ui/api/models` | Create a model deployment |
| `PUT` | `/ui/api/models/{deployment_id}` | Update a model deployment |
| `DELETE` | `/ui/api/models/{deployment_id}` | Delete a model deployment |

## API Keys

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/ui/api/keys` | List all API keys |
| `POST` | `/ui/api/keys` | Create a new API key |
| `PUT` | `/ui/api/keys/{token}` | Update an API key |
| `DELETE` | `/ui/api/keys/{token}` | Delete an API key |
| `POST` | `/ui/api/keys/{token}/revoke` | Revoke an API key |
| `POST` | `/ui/api/keys/{token}/regenerate` | Regenerate an API key |

## Teams

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/ui/api/teams` | List all teams |
| `POST` | `/ui/api/teams` | Create a team |
| `PUT` | `/ui/api/teams/{team_id}` | Update a team |
| `DELETE` | `/ui/api/teams/{team_id}` | Delete a team |
| `POST` | `/ui/api/teams/{team_id}/members` | Add a team member |
| `DELETE` | `/ui/api/teams/{team_id}/members/{user_id}` | Remove a team member |

## Organizations

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/ui/api/organizations` | List all organizations |
| `POST` | `/ui/api/organizations` | Create an organization |
| `PUT` | `/ui/api/organizations/{org_id}` | Update an organization |
| `DELETE` | `/ui/api/organizations/{org_id}` | Delete an organization |

## Users

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/ui/api/users` | List all users |
| `POST` | `/ui/api/users` | Create a user |
| `PUT` | `/ui/api/users/{user_id}` | Update a user |
| `POST` | `/ui/api/users/{user_id}/block` | Block a user |

`user_role` on users is a profile type metadata field (non-RBAC). Allowed values are:
- `internal_user`
- `internal_user_viewer`
- `team_admin`

Authorization is enforced through platform/org/team RBAC memberships, not `user_role`.

## Spend

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/ui/api/spend/summary` | Total spend summary |
| `GET` | `/ui/api/spend/report` | Spend breakdown by group |

Query parameters for `/ui/api/spend/report`:

| Parameter | Description |
|-----------|-------------|
| `group_by` | Group results by: `model`, `api_key`, `team`, `user` |
| `start_date` | Filter start date (ISO 8601) |
| `end_date` | Filter end date (ISO 8601) |
| `include_logs` | Include individual request logs |
| `page` | Page number for logs pagination |
| `page_size` | Number of logs per page |

## Guardrails

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/ui/api/guardrails` | List guardrail configurations |
| `GET` | `/ui/api/guardrails/scope/{scope}/{entity_id}` | Get scoped guardrail assignment |
| `PUT` | `/ui/api/guardrails/scope/{scope}/{entity_id}` | Set scoped guardrail assignment |
| `DELETE` | `/ui/api/guardrails/scope/{scope}/{entity_id}` | Remove scoped guardrail assignment |

## Settings

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/ui/api/settings` | Get current gateway settings |
| `PUT` | `/ui/api/settings` | Update gateway settings (platform admin only) |

## RBAC

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/ui/api/rbac/accounts` | List platform accounts |
| `POST` | `/ui/api/rbac/accounts` | Create a platform account |
| `PUT` | `/ui/api/rbac/accounts/{account_id}` | Update a platform account |
| `GET` | `/ui/api/rbac/organization-memberships` | List org memberships |
| `POST` | `/ui/api/rbac/organization-memberships` | Create org membership |
| `DELETE` | `/ui/api/rbac/organization-memberships/{id}` | Remove org membership |
| `GET` | `/ui/api/rbac/team-memberships` | List team memberships |
| `POST` | `/ui/api/rbac/team-memberships` | Create team membership |
| `DELETE` | `/ui/api/rbac/team-memberships/{id}` | Remove team membership |

## Authentication

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/auth/internal/login` | Login with email/password |
| `POST` | `/auth/internal/logout` | Logout (clear session) |
| `GET` | `/auth/me` | Get current auth status |
| `POST` | `/auth/internal/change-password` | Change password |
| `POST` | `/auth/mfa/enroll/start` | Start MFA enrollment |
| `POST` | `/auth/mfa/enroll/confirm` | Confirm MFA enrollment |
| `GET` | `/auth/sso-config` | Get SSO configuration (public) |
| `GET` | `/auth/login` | Initiate SSO login flow |
| `GET` | `/auth/callback` | SSO callback handler |
