# Administration workflows

Use these checklists for common account, credential, and usage-management work.

## Add a new organization

1. Create an [organization](../admin-ui/organizations.md) and set its budget and rate limits.
2. Give it access only to the models or access groups it needs.
3. Create a [team](../admin-ui/teams.md) and decide whether members may create their own keys.
4. Invite people through [People and permissions](../admin-ui/people-and-access.md).
5. Create a team-owned application key with an expiry and appropriate limits.
6. Test one allowed model and one model outside the organization's access.

Success means the allowed request works, the other request is denied, and the new users cannot see
another organization.

## Rotate a provider credential

1. Open [Provider credentials](admin-provider-credentials.md) and note every linked deployment.
2. Create the new secret at the provider without revoking the old one.
3. Update the saved credential in DeltaLLM.
4. Wait for the change to reach the runtime and test every affected model type or provider region.
5. Revoke the old provider secret only after the tests succeed.

Success means linked deployments stay healthy and no raw credential appears in responses, logs, or
screenshots.

## Investigate unusual usage or spending

1. Use the [Dashboard](../admin-ui/dashboard.md) to choose the time period and confirm the spike.
2. Open [Usage and spending](../admin-ui/usage.md) and select one reporting scope.
3. Narrow the results by application key, team, model, or provider to identify the source.
4. Use request and correlation IDs to compare provider, cache, and rate-limit behavior.
5. Open [Audit logs](../admin-ui/audit-logs.md) only when you need authorized event details.
6. Contain the issue with the narrowest suitable key, budget, rate, or deployment change.

Success means the totals reconcile to one scope, the source is identified, and the containment stops
the unexpected activity.
