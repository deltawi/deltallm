# Choose and connect a model

A model deployment tells DeltaLLM which provider to call. Your application uses a public model
name, so you can change the provider later without changing application code.

## Before you start

You need:

- administrator access to DeltaLLM
- an API key or endpoint for the provider you want to use
- DeltaLLM running locally or in a deployed environment

## Choose the simplest setup

| What you need | Start with |
| --- | --- |
| One provider and model | One model deployment |
| A backup provider | Two deployments with the same public name |
| A planned traffic split or standby | A route group |
| Credentials shared by several deployments | A named credential |
| A self-hosted server | An OpenAI-compatible provider and its API address |

Start with one deployment. Add routing or shared credentials only when you need them.

## Add the model

1. Open the Admin UI and sign in as an administrator.
2. Open **AI Gateway**, then **Models**.
3. Select **Add model**.
4. Keep **Model type** set to **Chat** for a normal text model.
5. Enter a **Model name**. This is the public name your application will call, such as
   `support-chat`.
6. Choose the provider and its provider model.
7. Enter the provider API key, or choose a named credential that already contains it.
8. For a self-hosted or compatible provider, enter its API base URL.
9. Leave routing, capacity, and pricing fields at their defaults for the first test.
10. Select **Create** and wait for the health status to become healthy.

Before testing with an application key, make sure its team can use the new public model name. A key
with limited access will not show the model in `/v1/models`. If needed, update the key by following
[Create a key for your application](../getting-started/first-api-key.md).

## Check the connection

Set the address and application key for your environment:

```bash
export BASE_URL="http://localhost:4002"
export API_KEY="YOUR_APPLICATION_KEY"
```

Use `http://localhost:8000` for the manual development setup.

Confirm that the public name appears:

```bash
curl "$BASE_URL/v1/models" \
  -H "Authorization: Bearer $API_KEY"
```

Send a request using the public name, not the provider model name:

```bash
curl "$BASE_URL/v1/chat/completions" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "support-chat",
    "messages": [{"role": "user", "content": "Reply with: model connected"}]
  }'
```

## Add reliability later

- To spread traffic or add a standby, follow [Route traffic and handle failures](routing-and-failover.md).
- To reuse credentials, see [Provider credentials](../admin-ui/named-credentials.md).
- For every model field, see the [model deployment reference](../configuration/models.md).
- For supported provider features, see [Models and supported features](../providers/capabilities.md).
