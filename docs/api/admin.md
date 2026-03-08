# Admin Endpoints

The admin API manages the gateway runtime, control-plane configuration, and operator workflows.

These endpoints back the Admin UI, but you can also use them directly for automation.

## Quick Success Path

Most operators use the admin API in this order:

1. Log in with the master key or an authenticated admin session
2. Create a model deployment
3. Create a virtual API key
4. Adjust settings, guardrails, or route groups as needed
5. Inspect spend, audit history, or batch activity

## Authentication

Admin endpoints require either:

- a master key in `Authorization: Bearer ...`
- or an authenticated session cookie from the `/auth/*` login flow

Some endpoints require specific admin permissions, so a valid session does not automatically mean full access.

## Runtime Configuration

### Models

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/ui/api/models` | List deployments |
| `GET` | `/ui/api/models/{deployment_id}` | Get one deployment |
| `POST` | `/ui/api/models` | Create a deployment |
| `PUT` | `/ui/api/models/{deployment_id}` | Update a deployment |
| `DELETE` | `/ui/api/models/{deployment_id}` | Delete a deployment |
| `GET` | `/ui/api/provider-presets` | List provider presets for the UI |

### Route Groups

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/ui/api/route-groups` | List route groups |
| `GET` | `/ui/api/route-groups/{group_key}` | Get one route group |
| `POST` | `/ui/api/route-groups` | Create a route group |
| `PUT` | `/ui/api/route-groups/{group_key}` | Update a route group |
| `DELETE` | `/ui/api/route-groups/{group_key}` | Delete a route group |
| `GET` | `/ui/api/route-groups/{group_key}/members` | List group members |
| `POST` | `/ui/api/route-groups/{group_key}/members` | Add a member |
| `DELETE` | `/ui/api/route-groups/{group_key}/members/{deployment_id}` | Remove a member |

### Route Group Policy

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/ui/api/route-groups/{group_key}/policy` | Read current policy |
| `GET` | `/ui/api/route-groups/{group_key}/policies` | Read policy history |
| `POST` | `/ui/api/route-groups/{group_key}/policy/validate` | Validate a policy payload |
| `POST` | `/ui/api/route-groups/{group_key}/policy/draft` | Save a draft policy |
| `POST` | `/ui/api/route-groups/{group_key}/policy/publish` | Publish a policy |
| `POST` | `/ui/api/route-groups/{group_key}/policy/rollback` | Roll back to an earlier policy |
| `POST` | `/ui/api/route-groups/{group_key}/policy/simulate` | Simulate routing behavior |
| `PUT` | `/ui/api/route-groups/{group_key}/policy` | Replace the active policy |

### Prompt Registry

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/ui/api/prompt-registry/templates` | List prompt templates |
| `GET` | `/ui/api/prompt-registry/templates/{template_key}` | Get one template with versions, labels, and bindings |
| `POST` | `/ui/api/prompt-registry/templates` | Create a template |
| `PUT` | `/ui/api/prompt-registry/templates/{template_key}` | Update a template |
| `DELETE` | `/ui/api/prompt-registry/templates/{template_key}` | Delete a template |
| `POST` | `/ui/api/prompt-registry/templates/{template_key}/versions` | Create a version |
| `POST` | `/ui/api/prompt-registry/templates/{template_key}/versions/{version}/publish` | Publish a version |
| `GET` | `/ui/api/prompt-registry/templates/{template_key}/labels` | List labels |
| `POST` | `/ui/api/prompt-registry/templates/{template_key}/labels` | Create or move a label |
| `DELETE` | `/ui/api/prompt-registry/templates/{template_key}/labels/{label}` | Delete a label |
| `GET` | `/ui/api/prompt-registry/bindings` | List bindings |
| `POST` | `/ui/api/prompt-registry/bindings` | Create a binding |
| `DELETE` | `/ui/api/prompt-registry/bindings/{binding_id}` | Delete a binding |
| `POST` | `/ui/api/prompt-registry/render` | Preview prompt rendering |
| `POST` | `/ui/api/prompt-registry/preview-resolution` | Preview prompt resolution |

### Settings and Routing Config

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/ui/api/routing` | Read routing config |
| `PUT` | `/ui/api/routing` | Update routing config |
| `GET` | `/ui/api/settings` | Read gateway settings |
| `PUT` | `/ui/api/settings` | Update gateway settings |

## Access and Identity

### API Keys

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/ui/api/keys` | List keys |
| `POST` | `/ui/api/keys` | Create a key |
| `PUT` | `/ui/api/keys/{token_hash}` | Update a key |
| `POST` | `/ui/api/keys/{token_hash}/regenerate` | Regenerate a key |
| `POST` | `/ui/api/keys/{token_hash}/revoke` | Revoke a key |
| `DELETE` | `/ui/api/keys/{token_hash}` | Delete a key |

### Teams

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/ui/api/teams` | List teams |
| `GET` | `/ui/api/teams/{team_id}` | Get one team |
| `POST` | `/ui/api/teams` | Create a team |
| `PUT` | `/ui/api/teams/{team_id}` | Update a team |
| `DELETE` | `/ui/api/teams/{team_id}` | Delete a team |
| `GET` | `/ui/api/teams/{team_id}/members` | List team members |
| `GET` | `/ui/api/teams/{team_id}/member-candidates` | List addable team members |
| `POST` | `/ui/api/teams/{team_id}/members` | Add a member |
| `DELETE` | `/ui/api/teams/{team_id}/members/{user_id}` | Remove a member |

### Organizations

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/ui/api/organizations` | List organizations |
| `GET` | `/ui/api/organizations/{organization_id}` | Get one organization |
| `POST` | `/ui/api/organizations` | Create an organization |
| `PUT` | `/ui/api/organizations/{organization_id}` | Update an organization |
| `GET` | `/ui/api/organizations/{organization_id}/members` | List organization members |
| `GET` | `/ui/api/organizations/{organization_id}/member-candidates` | List addable organization members |
| `POST` | `/ui/api/organizations/{organization_id}/members` | Add a member |
| `DELETE` | `/ui/api/organizations/{organization_id}/members/{membership_id}` | Remove a member |
| `GET` | `/ui/api/organizations/{organization_id}/teams` | List teams in the organization |

### RBAC

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/ui/api/rbac/accounts` | List platform accounts |
| `GET` | `/ui/api/principals` | List assignable principals |
| `POST` | `/ui/api/rbac/accounts` | Create a platform account |
| `DELETE` | `/ui/api/rbac/accounts/{account_id}` | Delete a platform account |
| `GET` | `/ui/api/rbac/organization-memberships` | List org memberships |
| `POST` | `/ui/api/rbac/organization-memberships` | Create org membership |
| `DELETE` | `/ui/api/rbac/organization-memberships/{membership_id}` | Delete org membership |
| `GET` | `/ui/api/rbac/team-memberships` | List team memberships |
| `POST` | `/ui/api/rbac/team-memberships` | Create team membership |
| `DELETE` | `/ui/api/rbac/team-memberships/{membership_id}` | Delete team membership |

## Safety and Operations

### Guardrails

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/ui/api/guardrails` | List global guardrail configuration |
| `PUT` | `/ui/api/guardrails` | Update global guardrail configuration |
| `GET` | `/ui/api/guardrails/scope/{scope}/{entity_id}` | Read scoped assignment |
| `PUT` | `/ui/api/guardrails/scope/{scope}/{entity_id}` | Update scoped assignment |
| `DELETE` | `/ui/api/guardrails/scope/{scope}/{entity_id}` | Remove scoped assignment |

### Spend

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/ui/api/spend/summary` | Platform or scoped spend summary |
| `GET` | `/ui/api/spend/report` | Spend report and optional request logs |

Supported report parameters include:

- `group_by=model|api_key|team|user`
- `start_date`
- `end_date`
- `include_logs`
- `page`
- `page_size`

### Batches

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/ui/api/batches/summary` | Batch counts by status |
| `GET` | `/ui/api/batches` | List batches |
| `GET` | `/ui/api/batches/{batch_id}` | Get one batch with items |
| `POST` | `/ui/api/batches/{batch_id}/cancel` | Cancel a batch |

### Audit

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/ui/api/audit/events` | List audit events |
| `GET` | `/ui/api/audit/events/{event_id}` | Fetch one audit event |
| `GET` | `/ui/api/audit/timeline` | Timeline by request or correlation |
| `GET` | `/ui/api/audit/export` | Export events as JSONL or CSV |

Audit read access is limited to the roles that hold `audit.read`.

## Session and Login Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `POST` | `/auth/internal/login` | Log in with email and password |
| `POST` | `/auth/internal/logout` | Log out and clear the session |
| `GET` | `/auth/me` | Inspect current session state |
| `POST` | `/auth/internal/change-password` | Change password |
| `POST` | `/auth/mfa/enroll/start` | Start MFA enrollment |
| `POST` | `/auth/mfa/enroll/confirm` | Confirm MFA enrollment |
| `GET` | `/auth/sso-config` | Read SSO configuration |
| `GET` | `/auth/login` | Start SSO login |
| `GET` | `/auth/callback` | Complete SSO login |

## Related Pages

- [Admin UI](../admin-ui/index.md)
- [Authentication & SSO](../features/authentication.md)
