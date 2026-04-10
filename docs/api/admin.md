# Admin Endpoints

The admin API manages the gateway runtime, control-plane configuration, and operator workflows.

These endpoints back the Admin UI, but you can also use them directly for automation.

## Quick Success Path

Most operators use the admin API in this order:

1. Log in with the master key or an authenticated admin session
2. Create a model deployment
3. Grant the callable targets an organization, team, or key should see
4. Create a virtual API key
5. Adjust settings, guardrails, or route groups as needed
6. Inspect spend, audit history, or batch activity

## Authentication

Admin endpoints require either:

- a master key in `Authorization: Bearer ...`
- or an authenticated session cookie from the `/auth/*` login flow

Some endpoints require specific admin permissions, so a valid session does not automatically mean full access.

If the account has MFA enabled, session-authenticated admin access requires an MFA-verified session.

## Auth and Account Lifecycle

These endpoints back browser login, invitation acceptance, password recovery, MFA, and SSO.

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `POST` | `/auth/internal/login` | Email/password login for admin sessions |
| `POST` | `/auth/internal/logout` | End the current admin session |
| `GET` | `/auth/me` | Inspect the current session |
| `POST` | `/auth/internal/change-password` | Change password for the current session |
| `POST` | `/auth/internal/forgot-password` | Request a password reset link |
| `GET` | `/auth/internal/reset-password/{token}` | Validate a reset token |
| `POST` | `/auth/internal/reset-password` | Complete a password reset |
| `GET` | `/auth/invitations/{token}` | Validate an invitation token |
| `POST` | `/auth/invitations/accept` | Accept an invitation |
| `POST` | `/auth/mfa/enroll/start` | Start MFA enrollment |
| `POST` | `/auth/mfa/enroll/confirm` | Confirm MFA enrollment |
| `POST` | `/auth/mfa/verify` | Verify the current session for MFA-enabled accounts |
| `GET` | `/auth/sso-config` | Inspect whether SSO is enabled |
| `GET` | `/auth/login` | Start the SSO login flow |
| `GET` | `/auth/callback` | Complete the SSO callback |

## Governance Notes

- Callable-target and MCP runtime checks are enforced from in-memory snapshots, not per-request database reads.
- In multi-instance deployments, admin writes publish governance invalidation events so other instances reload their local snapshots asynchronously.
- MCP binding and tool-policy listing endpoints return **enabled** rows by default. Platform admins can opt in to disabled rows with `include_disabled=true`.

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
| `POST` | `/ui/api/provider-models/discover` | Discover provider model suggestions for the UI |

Model create and update payloads accept custom upstream auth-header overrides inside `deltallm_params` for these OpenAI-compatible providers:

- `openai`
- `openrouter`
- `groq`
- `together`
- `fireworks`
- `deepinfra`
- `perplexity`
- `vllm`
- `lmstudio`
- `ollama`

Relevant fields:

- `deltallm_params.auth_header_name`
- `deltallm_params.auth_header_format`

`auth_header_format` must contain the exact `{api_key}` placeholder, and reserved header names such as `Content-Type` are rejected. If a deployment uses `named_credential_id` and also carries overlapping local connection fields, the named credential values win.

List, detail, create, and update responses continue to redact secrets such as `api_key`. When custom upstream auth is configured, `connection_summary` may include the effective `auth_header_name` plus a compact `custom_auth_label` such as `X-API-Key` or `Authorization (Token)`, but not the rendered header value.

Example inline model create payload:

```json
{
  "model_name": "support-vllm",
  "deltallm_params": {
    "provider": "vllm",
    "model": "vllm/meta-llama/Llama-3.1-8B-Instruct",
    "api_key": "gateway-key",
    "api_base": "https://vllm.example/v1",
    "auth_header_name": "X-API-Key",
    "auth_header_format": "{api_key}"
  },
  "model_info": {
    "mode": "chat"
  }
}
```

`POST /ui/api/provider-models/discover` accepts the same connection fields, including `auth_header_name` and `auth_header_format`, so the UI can probe OpenAI-compatible gateways before saving a deployment.

### Named Credentials

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/ui/api/named-credentials` | List named credentials with usage counts |
| `GET` | `/ui/api/named-credentials/{credential_id}` | Get one named credential and linked deployments |
| `POST` | `/ui/api/named-credentials` | Create a named credential |
| `PUT` | `/ui/api/named-credentials/{credential_id}` | Update a named credential |
| `DELETE` | `/ui/api/named-credentials/{credential_id}` | Delete a named credential when not linked |
| `GET` | `/ui/api/named-credentials/inline-report` | Report repeated inline credential groups |
| `POST` | `/ui/api/named-credentials/convert-inline-group` | Convert repeated inline credentials into a shared named credential |

Named credentials are the reusable provider connection objects that model deployments can reference through `named_credential_id`.

Use them when you want to:

- rotate one provider key and have multiple deployments pick it up
- reduce duplicated inline secrets in model payloads
- centralize shared connection settings such as `api_key`, `api_base`, `api_version`, or Bedrock fields

For the same OpenAI-compatible providers listed above, named-credential `connection_config` also supports:

- `auth_header_name`
- `auth_header_format`

Read responses always redact secret-bearing fields. Updating an in-use named credential triggers a runtime reload so linked deployments pick up the new connection settings. The raw secret value is never readable back out of the admin API.

For full UI and `curl` examples, see [Admin UI: Named Credentials](../admin-ui/named-credentials.md).

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

### Callable Target Governance

Callable targets are the public runtime names that callers can use, including both model names and route-group keys.

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/ui/api/callable-targets` | List callable targets from the live catalog |
| `GET` | `/ui/api/callable-targets/{callable_key}` | Get one callable target with current bindings |
| `GET` | `/ui/api/callable-target-bindings` | List callable-target bindings |
| `POST` | `/ui/api/callable-target-bindings` | Create or update a binding |
| `DELETE` | `/ui/api/callable-target-bindings/{binding_id}` | Delete a binding |
| `GET` | `/ui/api/callable-target-scope-policies` | List scope policies such as `inherit` or `restrict` |
| `POST` | `/ui/api/callable-target-scope-policies` | Create or update a scope policy |
| `DELETE` | `/ui/api/callable-target-scope-policies/{policy_id}` | Delete a scope policy |
| `GET` | `/ui/api/callable-target-migration/report` | Report rollout and migration readiness |
| `POST` | `/ui/api/callable-target-migration/backfill` | Backfill explicit bindings from legacy data |

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

### MCP Management

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/ui/api/mcp-servers` | List MCP servers |
| `POST` | `/ui/api/mcp-servers` | Create an MCP server |
| `GET` | `/ui/api/mcp-servers/{server_id}` | Get one server with visible tools, bindings, and policies |
| `GET` | `/ui/api/mcp-servers/{server_id}/operations` | Server-level usage and approval summary |
| `PATCH` | `/ui/api/mcp-servers/{server_id}` | Update an MCP server |
| `DELETE` | `/ui/api/mcp-servers/{server_id}` | Delete an MCP server |
| `POST` | `/ui/api/mcp-servers/{server_id}/refresh-capabilities` | Refresh upstream tool capabilities |
| `POST` | `/ui/api/mcp-servers/{server_id}/health-check` | Run an upstream health check |
| `GET` | `/ui/api/mcp-bindings` | List MCP bindings |
| `POST` | `/ui/api/mcp-bindings` | Create or update an MCP binding |
| `DELETE` | `/ui/api/mcp-bindings/{binding_id}` | Delete an MCP binding |
| `GET` | `/ui/api/mcp-scope-policies` | List MCP scope policies |
| `POST` | `/ui/api/mcp-scope-policies` | Create or update an MCP scope policy |
| `DELETE` | `/ui/api/mcp-scope-policies/{policy_id}` | Delete an MCP scope policy |
| `GET` | `/ui/api/mcp-tool-policies` | List MCP tool policies |
| `POST` | `/ui/api/mcp-tool-policies` | Create or update an MCP tool policy |
| `DELETE` | `/ui/api/mcp-tool-policies/{policy_id}` | Delete an MCP tool policy |
| `GET` | `/ui/api/mcp-approval-requests` | List approval requests |
| `POST` | `/ui/api/mcp-approval-requests/{approval_request_id}/decision` | Approve or reject a pending request |
| `GET` | `/ui/api/mcp-migration/report` | Report MCP rollout readiness by organization |
| `POST` | `/ui/api/mcp-migration/backfill` | Backfill explicit org ceilings and child scope policies for MCP |

## Access and Identity

### API Keys

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/ui/api/keys` | List keys |
| `POST` | `/ui/api/keys` | Create a key |
| `PUT` | `/ui/api/keys/{token_hash}` | Update a key |
| `GET` | `/ui/api/keys/{token_hash}/asset-visibility` | Preview effective callable-target visibility for a key |
| `GET` | `/ui/api/keys/{token_hash}/asset-access` | Read scoped callable-target access config for a key |
| `PUT` | `/ui/api/keys/{token_hash}/asset-access` | Update scoped callable-target access config for a key |
| `POST` | `/ui/api/keys/{token_hash}/regenerate` | Regenerate a key |
| `POST` | `/ui/api/keys/{token_hash}/revoke` | Revoke a key |
| `DELETE` | `/ui/api/keys/{token_hash}` | Delete a key |

### Service Accounts

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/ui/api/service-accounts` | List service accounts |
| `POST` | `/ui/api/service-accounts` | Create a service account |

### Teams

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/ui/api/teams` | List teams |
| `GET` | `/ui/api/teams/{team_id}` | Get one team |
| `GET` | `/ui/api/teams/{team_id}/asset-visibility` | Preview effective callable-target visibility for a team |
| `GET` | `/ui/api/teams/{team_id}/asset-access` | Read scoped callable-target access config for a team |
| `PUT` | `/ui/api/teams/{team_id}/asset-access` | Update scoped callable-target access config for a team |
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
| `GET` | `/ui/api/organizations/{organization_id}/asset-visibility` | Preview effective callable-target visibility for an organization |
| `GET` | `/ui/api/organizations/{organization_id}/asset-access` | Read scoped callable-target access config for an organization |
| `PUT` | `/ui/api/organizations/{organization_id}/asset-access` | Update scoped callable-target access config for an organization |
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
| `GET` | `/ui/api/users/{user_id}/asset-visibility` | Preview effective callable-target visibility for a runtime user |
| `GET` | `/ui/api/users/{user_id}/asset-access` | Read scoped callable-target access config for a runtime user |
| `PUT` | `/ui/api/users/{user_id}/asset-access` | Update scoped callable-target access config for a runtime user |
| `POST` | `/ui/api/rbac/accounts` | Create a platform account |
| `DELETE` | `/ui/api/rbac/accounts/{account_id}` | Delete a platform account |
| `GET` | `/ui/api/rbac/organization-memberships` | List org memberships |
| `POST` | `/ui/api/rbac/organization-memberships` | Create org membership |
| `DELETE` | `/ui/api/rbac/organization-memberships/{membership_id}` | Delete org membership |
| `GET` | `/ui/api/rbac/team-memberships` | List team memberships |
| `POST` | `/ui/api/rbac/team-memberships` | Create team membership |
| `DELETE` | `/ui/api/rbac/team-memberships/{membership_id}` | Delete team membership |

### Invitations

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/ui/api/invitations` | List invitations visible to the caller |
| `POST` | `/ui/api/invitations` | Create an invitation |
| `POST` | `/ui/api/invitations/{invitation_id}/resend` | Resend an invitation |
| `POST` | `/ui/api/invitations/{invitation_id}/cancel` | Cancel an invitation |

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

### Email Operations

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/ui/api/email/outbox/summary` | Operator summary of outbox status and recent email records |
| `POST` | `/ui/api/email/test` | Queue a test email and verify delivery is possible |
| `GET` | `/ui/api/email/suppressions` | List suppressed email recipients |
| `DELETE` | `/ui/api/email/suppressions/{email_address}` | Remove a suppressed recipient |
| `POST` | `/webhooks/email/resend` | Ingest Resend delivery feedback and suppression events |
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
- [MCP Gateway & Tooling](mcp.md)
- [Authentication & SSO](../features/authentication.md)
