# Budgets & Spend Tracking

DeltaLLM tracks spending per API key, team, and model, with optional budget enforcement to prevent overspending.

## Budget Enforcement

Set a maximum budget when creating or updating an API key:

```bash
curl -X POST http://localhost:8000/ui/api/keys \
  -H "Authorization: Bearer MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "key_name": "budget-key",
    "max_budget": 50.00
  }'
```

When an API key's accumulated spend reaches its `max_budget`, further requests are rejected with HTTP 403.

## Spend Tracking

Every proxied request is logged with cost data calculated from the model's configured token pricing.

### Cost Calculation

Costs are calculated using the pricing configured in `model_info`:

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

For audio models, pricing uses `input_cost_per_second` or `input_cost_per_character`.

### Spend Summary

View total spend across the platform:

```bash
curl http://localhost:8000/ui/api/spend/summary \
  -H "Authorization: Bearer MASTER_KEY"
```

```json
{
  "total_spend": 12.45,
  "total_tokens": 1250000,
  "prompt_tokens": 1000000,
  "completion_tokens": 250000,
  "total_requests": 5000
}
```

### Spend Breakdown

Break down spend by model, key, team, or time period:

```bash
# By model
curl "http://localhost:8000/ui/api/spend/report?group_by=model" \
  -H "Authorization: Bearer MASTER_KEY"

# By API key
curl "http://localhost:8000/ui/api/spend/report?group_by=api_key" \
  -H "Authorization: Bearer MASTER_KEY"

# By team
curl "http://localhost:8000/ui/api/spend/report?group_by=team" \
  -H "Authorization: Bearer MASTER_KEY"
```

### Request Logs

View individual request logs with cost and token details:

```bash
curl "http://localhost:8000/ui/api/spend/report?include_logs=true&page=1&page_size=50" \
  -H "Authorization: Bearer MASTER_KEY"
```

## Admin UI

The Usage page in the admin dashboard provides visual spend analytics:

- Total spend summary cards
- Daily spend trend chart
- Per-model and per-key cost breakdowns
- Searchable request log table
