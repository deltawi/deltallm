# Teams

Teams are the working unit for developers, applications, keys, and team-level budgets.

![Teams](images/teams.png)

## What this page manages

- Team identity and parent organization
- Team-level budgets and rate limits (RPM, TPM, RPH, RPD, TPD)
- Team memberships
- Team runtime access mode: inherit the organization set or restrict to selected callable targets

## Typical workflow

1. Create the team inside an organization
2. Set team-specific budgets and rate limits across all time windows
3. Add team members with the correct role
4. Choose whether the team inherits the organization asset set or narrows it to selected assets

## Rate limit fields

| Field | Description |
| --- | --- |
| RPM | Maximum requests per minute across all keys in the team |
| TPM | Maximum tokens per minute across all keys in the team |
| RPH | Maximum requests per hour across all keys in the team |
| RPD | Maximum requests per day across all keys in the team |
| TPD | Maximum tokens per day across all keys in the team |

All limits are optional. Only configured limits are enforced. Team limits act as a shared cap — all keys within the team contribute to the same counters. Team limits must fall within the parent organization's limits.

## Why this matters

Team scope is where most day-to-day ownership lives. API keys, usage, and user access are typically understood in team context.

The team record itself no longer stores model allowlists. The create/edit UI writes callable-target bindings and scope policies so the team can inherit the organization set or narrow it for day-to-day ownership.
