# API Keys

The API Keys page is where operators issue credentials for applications, teams, and controlled integrations.

![API Keys](images/api-keys.png)

## What this page is for

- Create a new key for a team-owned application or integration
- Limit which models or route groups a key can call
- Apply budgets and RPM/TPM rate limits
- Regenerate or revoke keys without changing the integration pattern

## Quick steps

1. Select the team that should own the key.
2. Choose who owns it in the admin UI:
   `You` for a human-owned key, or `Service account` for automation.
3. If you need a new service account, create it directly from the same dialog after selecting a team.
4. Set optional limits such as models, budget, RPM, or TPM.
5. Create the key and copy the raw secret immediately. It is only shown once.

## Key fields

- **Key name**: human-readable label shown in the table
- **Team**: required ownership boundary for spend, permissions, and reporting
- **Owned by**: who the key belongs to in the admin UI
- **Models**: optional allowlist of deployments or route groups
- **Max budget**: hard spend ceiling for that key
- **RPM / TPM**: request and token throttles

## Important behavior

- The raw key is shown once at creation time
- The table keeps the hashed token, owner, status, budget progress, and allowlist
- Scoped access still applies: org users only see keys inside their organizations
- Service accounts are non-login owners for shared services, jobs, or automations
