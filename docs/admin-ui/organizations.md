# Organizations

Organizations are the top-level tenant and budget boundary in the admin UI.

![Organizations](images/organizations.png)

## What this page manages

- Organization identity and display name
- Top-level budgets and RPM / TPM limits
- Audit content storage behavior
- Team count and ownership context
- The tenant boundary that callable-target access and lower-scope restrictions inherit from

## Typical workflow

1. Create the organization
2. Set the broad budget and rate limits
3. Choose which models and route groups the organization is allowed to use
4. Add teams inside the organization
5. Assign platform accounts through People & Access

## Why it matters

Organization scope controls how teams, keys, and access are partitioned. It is the right place for tenant-wide spending and policy boundaries.

Platform admins can bootstrap runtime model and route-group visibility directly in the create/edit dialog through the organization asset access section. Teams, keys, and users can only inherit or narrow from that organization set.
