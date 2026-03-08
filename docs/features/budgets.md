# Budgets & Spend Tracking

DeltaLLM records usage cost for proxied requests and can stop traffic when a budget is exhausted.

## Quick Path

For a safe first rollout:

1. Make sure each deployed model has pricing metadata
2. Set a hard budget on the API key you want to protect
3. Send test traffic through the gateway
4. Check the [Usage & Spend](../admin-ui/usage.md) page or spend APIs to confirm cost is being recorded

Example API key with a hard cap:

```bash
curl -X POST http://localhost:8000/ui/api/keys \
  -H "Authorization: Bearer YOUR_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "key_name": "budget-key",
    "max_budget": 50.0
  }'
```

When the key reaches its configured budget, DeltaLLM rejects new requests with a `budget_exceeded` error.

## How Spend Is Calculated

Spend is based on the pricing metadata attached to the served model deployment.

```yaml
model_list:
  - model_name: gpt-4o-mini
    deltallm_params:
      model: openai/gpt-4o-mini
      api_key: os.environ/OPENAI_API_KEY
    model_info:
      input_cost_per_token: 0.00000015
      output_cost_per_token: 0.0000006
```

DeltaLLM also supports other pricing units when they fit the model type:

- `input_cost_per_character`
- `input_cost_per_second`
- batch pricing fields such as `batch_input_cost_per_token`

## Budget Levels

Hard budgets can be enforced at these levels:

```text
API Key -> User -> Team -> Organization
```

There is also support for team-per-model hard budgets, which is useful when one team can use several models but one of them needs its own cap.

## View Spend

Use the Admin UI for the fastest view, or query the admin endpoints directly.

Summary:

```bash
curl http://localhost:8000/ui/api/spend/summary \
  -H "Authorization: Bearer YOUR_MASTER_KEY"
```

Breakdown report:

```bash
curl "http://localhost:8000/ui/api/spend/report?group_by=model" \
  -H "Authorization: Bearer YOUR_MASTER_KEY"
```

The legacy master-key spend endpoints also exist under `/global/spend`, `/global/spend/report`, `/global/spend/keys`, and `/global/spend/teams`.

## Soft Budgets and Resets

DeltaLLM also supports soft-budget alerting for keys, users, and teams. A soft budget does not block traffic; it triggers an alert through the configured alert service.

If an entity has both `budget_duration` and `budget_reset_at`, the runtime can reset tracked spend automatically when the reset window is reached.

## Admin UI

The [Usage & Spend](../admin-ui/usage.md) page is the main operator view for:

- total spend
- request volume
- per-model and per-key breakdowns
- detailed request logs

## Related Pages

- [Usage & Spend](../admin-ui/usage.md)
- [API Keys](../admin-ui/api-keys.md)
- [Model Deployments](../configuration/models.md)
