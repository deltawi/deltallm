# Set budgets

Budgets help control application spending. A hard budget stops new requests when the available
amount is used. A soft budget sends an alert but does not stop traffic.

## Before you start

Make sure each model has correct pricing information. Without pricing, DeltaLLM cannot calculate
spending accurately.

## Choose where to set the budget

| Goal | Set the budget on |
| --- | --- |
| Protect one application | Application key |
| Share one limit across several applications | Team |
| Limit the full customer or tenant | Organization |

Start with an application-key budget unless several keys need to share one limit.

## Set a hard budget

1. Open **API Keys**, **Teams**, or **Organizations**.
2. Create or edit the item you want to limit.
3. Enter the maximum budget.
4. Save the change.
5. Send test requests and check **Usage** to confirm that spending is recorded against the expected
   key, team, and organization.

When the hard budget is exhausted, new requests are rejected with a `budget_exceeded` error.

## Add alerts or resets

Soft-budget email alerts require email and governance notifications to be configured. Organization
budgets can also reset monthly. Test alert delivery and the next reset time before relying on either
feature in production.

## Learn more

- [Budget calculation, alerts, and resets](../features/budgets.md)
- [Usage and spending](../admin-ui/usage.md)
- [Application keys](../admin-ui/api-keys.md)
