# Set up plans and tiers

A tier is a reusable plan for organizations. It can define which models are available, their prices
and limits, and how shared capacity is protected.

Use tiers when several organizations should receive the same plan. For a small installation, direct
organization access and limits may be simpler.

## Before you start

You need platform administrator access and at least one model that organizations can use.

## Create a tier

1. Open **AI Gateway**, then **Tiers**.
2. Select **Create Tier** and give the plan a clear name, such as `Starter`.
3. Create a draft version.
4. Add the models included in the plan.
5. Add prices or request and token limits only when the plan needs them.
6. Preview the changes, then activate the version.

An organization cannot use a draft version. Activate it only after checking the included models and
limits.

## Assign it to an organization

1. Open **Organizations** and select the organization.
2. Choose the active tier as its primary plan.
3. Save the assignment.
4. Confirm that an application key in the organization can use an allowed model.
5. Confirm that the same key cannot use a model excluded by the tier.

Team, user, and application-key restrictions can make access narrower, but they cannot expand beyond
the organization's plan.

## Learn more

- [Tier behavior and capacity pools](../admin-ui/tiers.md)
- [Organizations](../admin-ui/organizations.md)
- [Set rate limits](admin-rate-limits.md)
