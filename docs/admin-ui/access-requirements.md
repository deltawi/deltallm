# Admin UI Access Requirements

The UI uses access flags calculated by the server from the current authentication mode, platform
role, organization/team memberships, and effective permissions. The API remains authoritative:
seeing a page or button does not grant access to another tenant or bypass an endpoint permission.

The master-key session has platform-admin access and should be reserved for bootstrap or break-
glass work. Normal operators should use named accounts and the narrowest suitable membership.

## Page access matrix

| Page | Minimum visibility rule | Important mutation boundary | Reference |
| --- | --- | --- | --- |
| Dashboard | Platform admin or `spend.read` | Read-only summary follows authorized spend data | [Spend API](../api/admin.md#spend) |
| Models | Any authenticated account | Create/edit/delete is platform admin | [Model deployments](../configuration/models.md) |
| Tiers | Platform admin | All tier/version/pool/assignment actions are platform admin | [Tier API](../api/admin.md#organization-tiers) |
| Named Credentials | Platform admin | Secrets remain write-only/redacted | [Named credential API](../api/admin.md#named-credentials) |
| Route Groups | Platform admin | Group/member/policy publication is platform admin | [Route-group API](../api/admin.md#route-groups) |
| Prompt Registry | Platform admin | Template/version/label/binding mutation is platform admin | [Admin API](../api/admin.md) |
| MCP Servers | Platform admin or `key.read` | Server/binding/policy mutation requires `org.update`; approvals require `key.update` | [MCP API](../api/mcp.md) |
| API Keys | `key.read`, `key.update`, or eligible `key.create_self` | Scope and ownership filter every action; self-service policy can narrow creation | [Authentication](../features/authentication.md) |
| Organizations | Platform admin or `org.read` | Create/delete is platform admin; scoped edits require `org.update` | [Tenancy](../concepts/tenancy-and-access.md) |
| Teams | Platform admin or `team.read` | Create/edit requires platform or applicable organization/team update capability | [Tenancy](../concepts/tenancy-and-access.md) |
| People & Access | Platform admin | Account, invite, and membership administration is platform-wide | [Authentication](../features/authentication.md) |
| Usage & Spend | Platform admin or enabled spend-read scope | Server filters platform, organization, team, or self views | [Budgets and spend](../features/budgets.md) |
| Audit Logs | Platform admin or `audit.read` | Results are filtered to authorized scope | [Audit log](../features/audit-log.md) |
| Batch Jobs | Platform admin or `key.read` | Cancel/retry/replay requires update permission in the batch's tenant scope | [Batch API](../features/batching.md) |
| Guardrails | Platform admin | Definition and scoped-assignment mutations are platform admin in the UI | [Guardrails](../features/guardrails.md) |
| Playground | Any authenticated account | Each request also needs a valid API key allowed to call the selected target | [Playground](playground.md) |
| Settings | Platform admin | Global config/theme writes are platform-wide | [General settings](../configuration/general.md) |

## Scope behavior

Permissions are accumulated from the platform role and current organization/team memberships, but
resource capabilities are recalculated for the actual organization, team, key, or batch. For
example, `org.update` in one organization does not authorize editing another organization.

The UI may omit an action, render it read-only, or redirect from an inaccessible route. Treat a
server `403` as the final decision and investigate membership/scope rather than trying a broader
credential. See [Tenancy and access](../concepts/tenancy-and-access.md) for the role model.

## Procedure author checklist

Every Admin UI procedure should state:

- the page and minimum role/permission;
- the organization/team/key scope affected;
- whether the change is global, inherited, or narrowing;
- the API/config contract that persists the change; and
- a success check plus an intentional denied check when authorization changes.
