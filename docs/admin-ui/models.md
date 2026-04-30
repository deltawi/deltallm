# Models

The Models page is where operators create and manage concrete provider-backed deployments.

Each deployment defines:

- the public model name clients will call
- the upstream provider model ID
- credentials and connection details, either inline or through a shared named credential
- the workload type, such as chat or embeddings
- optional pricing and default request parameters

![Models](images/models-list.png)

## Quick Success Workflow

1. Open **AI Gateway > Models**
2. Add one deployment for a model you already have provider access to
3. Confirm the deployment becomes healthy
4. Verify the model appears in `GET /v1/models`
5. Send a test proxy request

## What You Manage Here

- `model_name`: the public name clients use
- `deployment_id`: the stable internal identifier for this deployment
- provider settings in `deltallm_params`
- workload mode in `model_info.mode`
- access groups in `model_info.access_groups`
- pricing metadata for spend tracking
- default request parameters

## Recommended First Deployment

For a simple first deployment:

1. Set `model_name` to the public name you want clients to use
2. Set `deltallm_params.model` to the provider-prefixed upstream model ID
3. Add the provider API key inline, or select a shared named credential
4. Keep the default mode as `chat` unless this is an embeddings, image, audio, or rerank model

If you do not set a `deployment_id`, DeltaLLM creates one automatically.

## Access Groups

The model form includes an **Access Groups** field for authorization grouping. Enter group keys such as `beta` or `support` when scopes should be able to grant access to a set of callable targets instead of selecting each model separately.

Access groups are attached to the public model name, not a single provider deployment. If several deployments share the same `model_name`, keep their access group lists identical so group expansion remains deterministic.

Do not use access groups for routing. Deployment tags remain routing metadata and can be matched by request `metadata.tags`; tags do not make a model visible to an organization, team, key, or user.

## What the Table Tells You

- **Model Name**: the public runtime model name
- **Provider**: resolved provider type such as OpenAI or Groq
- **Type**: runtime mode such as `chat` or `embedding`
- **Deployment ID**: the internal ID used by route groups and policies
- **Health**: whether the runtime currently sees the deployment as healthy

## When You Need Route Groups

You do not need a route group for a single deployment.

Create a route group when:

- you want multiple deployments behind one logical target
- you want explicit routing policy
- you want controlled failover behavior
- you want to bind prompt behavior at the route-group level

## Custom Upstream Auth Headers

For these OpenAI-compatible providers, the model form supports inline upstream auth-header overrides:

- `openai`
- `openrouter`
- `groq`
- `together`
- `fireworks`
- `deepinfra`
- `perplexity`
- `vllm`
- `lmstudio`
- `ollama`

In the model form:

1. Choose **Inline credentials**
2. Enter `api_key` and any `api_base`
3. Expand the provider connection fields
4. Fill `Auth Header Name` and `Auth Header Format` if the upstream does not accept `Authorization: Bearer ...`

Example inline deployment payload:

```json
{
  "model_name": "support-vllm",
  "deltallm_params": {
    "provider": "vllm",
    "model": "vllm/meta-llama/Llama-3.1-8B-Instruct",
    "api_key": "gateway-key",
    "api_base": "https://vllm.example/v1",
    "auth_header_name": "X-API-Key",
    "auth_header_format": "{api_key}"
  },
  "model_info": {
    "mode": "chat"
  }
}
```

For shared gateway credentials, [Named Credentials](named-credentials.md) remain the recommended workflow. If a deployment references a named credential and also carries overlapping connection fields locally, the named credential values win.

## Operational Notes

- DeltaLLM validates provider and mode compatibility when you create or update a deployment
- Shared provider credentials are best managed through [Named Credentials](named-credentials.md)
- Creating, updating, and deleting deployments requires admin access
- Readable deployment IDs make later route-group work easier
- Visibility to organizations, teams, keys, and users is governed through callable-target bindings, access-group bindings, and scope policies

## Related Pages

- [Named Credentials](named-credentials.md)
- [Route Groups](route-groups.md)
- [Model Deployments config reference](../configuration/models.md)
- [Quick Start](../getting-started/quickstart.md)
