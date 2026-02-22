# Organizations & Teams

DeltaLLM uses a two-level grouping structure for access control and resource management.

## Organizations

Organizations are the top-level grouping. They contain teams and provide shared rate limits and budget tracking.

### Creating an Organization

Click **Create Organization** (platform admins only) and set:

- **Name** — Organization display name
- **RPM Limit** — Requests per minute for the entire organization
- **TPM Limit** — Tokens per minute for the entire organization

### Organization Details

The organization table shows:

- Organization name and ID
- Current spend
- RPM/TPM limits
- Number of teams
- Creation date

## Teams

Teams exist within organizations and group related API keys and users.

### Creating a Team

Click **Create Team** and configure:

- **Team Name** — Display name for the team
- **Organization** — Parent organization (optional)
- **RPM Limit** — Requests per minute for the team
- **TPM Limit** — Tokens per minute for the team

### Managing Team Members

Open a team's detail view to manage members:

- Add users to the team with a specific team role (admin, developer, viewer)
- Remove users from the team
- View all API keys assigned to the team

## Scoped Access

- **Platform admins** see all organizations and teams
- **Organization users** see only organizations they are members of, and the teams within those organizations
- The "Create Organization" button is hidden for non-platform-admins
