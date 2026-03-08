# Models

The Models page is where operators create and manage concrete provider-backed deployments.

Each deployment defines:

- the public model name clients will call
- the upstream provider model ID
- credentials and connection details
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
- pricing metadata for spend tracking
- default request parameters

## Recommended First Deployment

For a simple first deployment:

1. Set `model_name` to the public name you want clients to use
2. Set `deltallm_params.model` to the provider-prefixed upstream model ID
3. Add the provider API key
4. Keep the default mode as `chat` unless this is an embeddings, image, audio, or rerank model

If you do not set a `deployment_id`, DeltaLLM creates one automatically.

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

## Operational Notes

- DeltaLLM validates provider and mode compatibility when you create or update a deployment
- Creating, updating, and deleting deployments requires admin access
- Readable deployment IDs make later route-group work easier

## Related Pages

- [Route Groups](route-groups.md)
- [Model Deployments config reference](../configuration/models.md)
- [Quick Start](../getting-started/quickstart.md)
