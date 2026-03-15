# Quick Start

Use the gateway with `curl`, Python, or JavaScript once DeltaLLM is running and at least one model is available.

This page is the main "how do I call the gateway?" guide for first-time users.

## 1. Start the Gateway

After completing [installation](installation.md), make sure the backend is running:

```bash
# Optional: start Redis for distributed caching and rate limiting
redis-server --daemonize yes

python -m uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

## 2. Verify It's Running

```bash
curl http://localhost:8000/health/liveliness
```

Expected response:

```json
{
  "status": "ok"
}
```

## 3. List Available Models

```bash
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer YOUR_MASTER_KEY"
```

!!! note
    If the response contains no models, either:
    1. enable `general_settings.model_deployment_bootstrap_from_config: true` in `config.yaml` and restart once, or
    2. create a model deployment from the Admin UI first.

## 4. Make a Chat Request

Use the standard OpenAI chat completions format:

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer YOUR_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {"role": "user", "content": "Hello!"}
    ]
  }'
```

## 5. Use with the OpenAI SDK

Point any OpenAI SDK client at DeltaLLM:

=== "Python"

    ```python
    from openai import OpenAI

    client = OpenAI(
        base_url="http://localhost:8000/v1",
        api_key="YOUR_MASTER_KEY",
    )

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Hello!"}],
    )
    print(response.choices[0].message.content)
    ```

=== "JavaScript"

    ```javascript
    import OpenAI from "openai";

    const client = new OpenAI({
      baseURL: "http://localhost:8000/v1",
      apiKey: "YOUR_MASTER_KEY",
    });

    const response = await client.chat.completions.create({
      model: "gpt-4o-mini",
      messages: [{ role: "user", content: "Hello!" }],
    });
    console.log(response.choices[0].message.content);
    ```

## 6. Create a Virtual API Key

Instead of sharing the master key, create scoped virtual keys:

```bash
curl -X POST http://localhost:8000/ui/api/keys \
  -H "Authorization: Bearer YOUR_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "key_name": "my-app-key",
    "max_budget": 10.00
  }'
```

The response includes a `raw_key` — use this as the API key for your application.

## Next Steps

- [Try MCP tools through DeltaLLM](mcp-quickstart.md)
- [Configure models and providers](../configuration/models.md)
- [Set up authentication and SSO](../features/authentication.md)
- [Explore the Admin UI](../admin-ui/index.md)
