# Set up sign-in and SSO

Use named accounts for normal administration. Keep the master key for initial setup and emergency
recovery.

## Choose a sign-in method

| Need | Start with |
| --- | --- |
| First local administrator | Bootstrap email and password |
| Invite a few administrators | Email invitations |
| Use company accounts | Single sign-on (SSO) |
| Require an extra login check | Multi-factor authentication (MFA) |

## Create the first administrator

Set the bootstrap administrator email and password before the first startup. DeltaLLM creates that
account when both values are present. Sign in with it, then use **People & Access** for later account
and membership changes.

The exact settings and environment-variable examples are in the [authentication and SSO
reference](../features/authentication.md#session-based-login-for-the-admin-ui).

## Invite another person

1. Open **People & Access**.
2. Invite the person's email address.
3. Choose the required organization or team membership and role.
4. Ask the person to accept the email invitation.
5. Confirm that they can see only the intended pages and organization data.

## Use company sign-in

Configure Microsoft Entra, Google, Okta, or another OpenID Connect provider in `general_settings`.
Test sign-in with a non-administrator account first, then confirm administrator assignment and sign-
out behavior before making SSO the normal path.

## Diagnose access problems

Use [Sign in and get the right access](../admin-ui/access-requirements.md) when a page is missing or
an action returns `403`. Use the [access and permission matrix](../reference/admin-access.md) when you
need the exact permission for a page.

## Learn more

- [Authentication and SSO reference](../features/authentication.md)
- [People and permissions](../admin-ui/people-and-access.md)
- [Accounts, teams, and access](../concepts/tenancy-and-access.md)
