# Sign in and get the right access

What you can see and change in the Admin UI depends on your account and the organizations or teams
you belong to.

## Sign in

Open the address that matches your setup:

- Docker quickstart: `http://localhost:4002`
- Local development: `http://localhost:5000`

Use an administrator email and password or your company sign-in provider. During initial local
setup, you can also use the master key. Reserve the master key for setup and emergency recovery;
use a named account for normal work.

## If a page is missing

DeltaLLM hides pages that your account cannot use. Ask a platform administrator to check:

1. your platform role
2. your organization membership
3. your team membership
4. the permissions attached to those memberships

For example, access to one organization does not give you access to every organization.

## If an action is unavailable

A page may be visible even when its create, edit, or delete actions are unavailable. This normally
means you have permission to view the information but not change it.

Do not switch to the master key simply to avoid a permission error. Ask for the narrowest role that
supports the task you need to complete.

## If you receive a 403 error

A `403` response means the server rejected the action. Check that you selected the intended
organization, team, key, or batch and that your membership covers it. The server makes the final
access decision even if a page or button is visible.

Administrators can use the [access and permission matrix](../reference/admin-access.md) to find the
permission required by each page.

## Related pages

- [People and permissions](people-and-access.md)
- [Set up sign-in and SSO](../guides/admin-authentication.md)
- [Accounts, teams, and access](../concepts/tenancy-and-access.md)
