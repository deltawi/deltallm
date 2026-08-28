# Tenancy and Access

DeltaLLM has two related authorization planes: bearer authentication for application traffic
and session-based role access for operators. The backend is authoritative in both cases; UI
visibility is only a convenience.

## Tenant hierarchy

```text
Platform
├── Platform accounts
├── Organizations
│   ├── Organization memberships
│   └── Teams
│       ├── Team memberships
│       ├── Runtime users
│       └── Virtual API keys
└── Platform-wide provider, model, route, tier, and configuration administration
```

An account can belong to more than one organization or team. A virtual key can carry user,
team, and organization ownership, and runtime policy uses only scope resolved from the
verified credential and server-side relationships. A client-supplied organization or team ID
is never proof of access.

## Application authentication

Proxy requests use `Authorization: Bearer <token>`. The token can represent:

| Credential | Scope behavior | Recommended use |
| --- | --- | --- |
| Master key | Platform-wide and unscoped | Bootstrap and tightly controlled emergency administration |
| Virtual API key | API-key, user, team, and organization scope stored with the key | Normal application traffic |
| JWT | User/team/organization claims mapped by configured JWT policy | Existing identity-aware application infrastructure |
| Custom authentication | A server-side hook returns a typed runtime identity | Integrations that cannot use native virtual keys or JWT |

After authentication, DeltaLLM constructs the applicable runtime scope chain from user, API
key, team, and organization identifiers. Model access, prompts, MCP tools, tiers, budgets,
rate limits, batches, and audit attribution consume that verified context.

Virtual keys are stored as hashes. Redis may cache validation results, while PostgreSQL remains
the durable source of truth. Revocation and scope-changing mutations must invalidate shared
state before being treated as fully effective.

## Operator authentication

The Admin UI normally uses the secure `deltallm_session` cookie created by internal login,
invitation acceptance, or SSO. The master-key login flow creates a separate bounded master
session. Operator context includes the platform role plus organization and team memberships.

Authentication answers who the operator is. Authorization is checked again for every scoped
read or mutation. Missing authentication produces `401`; an authenticated caller without the
required permission receives `403` or a non-enumerating scoped result as appropriate.

## Platform roles

| Role | Effective access |
| --- | --- |
| `platform_admin` | Platform-wide administration, including models, credentials, tiers, route groups, accounts, guardrails, and settings |
| `org_user` | No platform-wide administrative grant; access comes from organization and team memberships |

The legacy `platform_co_admin` value is normalized to `platform_admin` for compatibility.

## Organization roles

| Role | Effective organization permissions |
| --- | --- |
| `org_owner` | Read/update organization; manage teams, users, and keys; revoke keys; create self-service keys; read organization and personal spend; read audit events |
| `org_admin` | Read/update organization; manage teams, users, and keys; revoke keys; create self-service keys; read personal spend and audit events |
| `org_billing` | Read organization, teams, and keys; read organization and personal spend |
| `org_auditor` | Read organization, teams, users, keys, organization/personal spend, and audit events |
| `org_member` | Read organization and teams; read personal spend |

Organization permissions apply only to the organization named by the verified membership.
For example, `org_admin` in one organization does not grant access to another organization.

## Team roles

| Role | Effective team permissions |
| --- | --- |
| `team_admin` | Read/update the team; manage users and keys; revoke keys; create self-service keys; read team and personal spend |
| `team_developer` | Read the team, users, and keys; create a self-service key when team policy allows it; read personal spend |
| `team_viewer` | Read the team and personal spend |

Organization owners/admins with team-update permission can manage teams inside their
organization even without a separate team-admin membership.

## Keys, callable targets, and tiers

Key ownership does not by itself make every configured model visible. Runtime model and route
group access is governed by callable-target bindings and scope policy, with optional
organization-tier policy layered on top. This separation lets operators reuse a key's tenant,
budget, and rate-limit scope while changing model access centrally.

## Organization lifecycle

An organization-scoped credential is accepted only while the organization is active. During
scheduled deletion or after deactivation, the gateway rejects application authentication for
that scope. If lifecycle state cannot be established, the gateway returns unavailable rather
than assuming the organization is active.

See [Organization deletion](../features/organization-deletion.md) for the state transition and
recovery workflow.

## Design checklist

When creating an access model:

1. Use a separate virtual key per application or workload.
2. Attach the narrowest useful user/team/organization scope.
3. Grant callable targets centrally instead of embedding provider credentials in applications.
4. Apply budgets and limits at every scope that needs an independent boundary.
5. Give human operators membership roles instead of sharing the master key.
6. Test cross-organization denial and key revocation before production rollout.

Continue with [Authentication and SSO](../features/authentication.md) for setup and the
[Admin UI access guide](../admin-ui/people-and-access.md) for operator workflows.
