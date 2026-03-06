# People & Access

People & Access is the RBAC control surface for platform accounts and memberships.

![People & Access](images/people-and-access.png)

## What this page manages

- Platform accounts
- Platform roles such as `platform_admin` and `org_user`
- Organization memberships
- Team memberships

## How the page works

- The top list shows platform accounts and their current role
- Expanding an account reveals its organization and team memberships
- Modals let admins add accounts or attach memberships without leaving the page

## Recommended model

- Use **platform role** for top-level authority
- Use **organization membership** for tenant scope
- Use **team membership** for the most specific working access

This keeps the access model explicit and traceable.
