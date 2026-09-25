# Admin access and permission matrix

Use this reference to identify the minimum access required for an Admin UI page. For help signing
in or diagnosing missing access, start with [Sign in and get the right
access](../admin-ui/access-requirements.md).

The server makes the final access decision. Seeing a page or button does not grant access to another
organization or bypass an endpoint permission.

| Page | Minimum access | What additional access controls | Reference |
| --- | --- | --- | --- |
| Dashboard | Platform administrator or `spend.read` | Totals include only authorized spending data | [Spend API](../api/admin.md#spend) |
| Models | Any signed-in account | Creating, editing, and deleting require platform administrator access | [Model deployments](../configuration/models.md) |
| Tiers | Platform administrator | All tier, version, capacity, and assignment changes | [Tier API](../api/admin.md#organization-tiers) |
| Provider credentials | Platform administrator | Secret values remain hidden after they are saved | [Provider credential API](../api/admin.md#named-credentials) |
| Route groups | Platform administrator | Group, member, and routing-policy changes | [Route-group API](../api/admin.md#route-groups) |
| Prompts | Platform administrator | Template, version, label, and binding changes | [Administration API](../api/admin.md) |
| MCP servers | Platform administrator or `key.read` | Changes require `org.update` for the affected organization | [MCP API](../api/mcp.md) |
| Tool approvals | Platform administrator or `key.update` | Results remain limited to authorized scope | [MCP API](../api/mcp.md) |
| Application keys | `key.read`, `key.update`, or eligible `key.create_self` | Ownership, team policy, and scope limit each action | [Authentication](../features/authentication.md) |
| Organizations | Platform administrator or `org.read` | Changes require the relevant organization permission | [Accounts and access](../concepts/tenancy-and-access.md) |
| Teams | Platform administrator or `team.read` | Changes require the relevant organization or team permission | [Accounts and access](../concepts/tenancy-and-access.md) |
| People and permissions | Platform administrator | Accounts, invitations, and memberships affect platform access | [Authentication](../features/authentication.md) |
| Usage and spending | Platform administrator or an enabled spending-reader role | Reports are limited to platform, organization, team, or personal scope | [Budgets and spending](../features/budgets.md) |
| Audit logs | Platform administrator or `audit.read` | Results are limited to authorized scope | [Audit records](../features/audit-log.md) |
| Batch jobs | Platform administrator or `key.read` | Cancel, retry, and replay require update access in the batch's scope | [Batch processing](../features/batching.md) |
| Guardrails | Platform administrator | Definitions and assignments are platform administration actions | [Guardrails](../features/guardrails.md) |
| Playground | Any signed-in account | Requests require an application key allowed to use the selected model | [Playground](../admin-ui/playground.md) |
| Settings | Platform administrator | Changes affect the full installation | [General settings](../configuration/general.md) |

Permissions from platform, organization, and team roles are combined, but the server checks them
again for the specific organization, team, key, or batch. For example, permission to update one
organization does not allow changes to another.
