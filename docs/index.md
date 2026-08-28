# DeltaLLM

DeltaLLM is a self-hosted LLM gateway and control plane. Applications send
OpenAI-compatible requests to one endpoint while operators manage provider credentials,
model routing, scoped access, budgets, guardrails, batches, MCP tools, and usage from one
place.

[Get started with Docker](getting-started/docker.md){ .md-button .md-button--primary }
[Understand the architecture](concepts/architecture.md){ .md-button }

## Send one request

After completing the [Docker setup](getting-started/docker.md), point an OpenAI-compatible
client at DeltaLLM:

=== "curl"

    ```bash
    curl http://localhost:4002/v1/chat/completions \
      -H "Authorization: Bearer $DELTALLM_MASTER_KEY" \
      -H "Content-Type: application/json" \
      -d '{
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Hello from DeltaLLM"}]
      }'
    ```

=== "Python"

    ```python
    from openai import OpenAI

    client = OpenAI(
        base_url="http://localhost:4002/v1",
        api_key="YOUR_DELTALLM_KEY",
    )
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Hello from DeltaLLM"}],
    )
    print(response.choices[0].message.content)
    ```

For JavaScript, streaming, embeddings, images, audio, files, and batches, continue to the
[gateway quick start](getting-started/quickstart.md).

## What you can do

| Outcome | Start here |
| --- | --- |
| Connect models and provider credentials | [Model deployments](configuration/models.md) |
| Route requests across deployments and fail over safely | [Routing and failover](features/routing.md) |
| Give applications scoped keys and limits | [Authentication and SSO](features/authentication.md) |
| Enforce budgets and hierarchical rate limits | [Budgets](features/budgets.md) and [rate limiting](features/rate-limiting.md) |
| Apply PII and prompt-injection controls | [Guardrails](features/guardrails.md) |
| Run asynchronous embeddings and chat workloads | [Batch API](features/batching.md) |
| Connect governed external tools | [MCP gateway](features/mcp.md) |
| Operate the gateway through a browser | [Admin UI](admin-ui/index.md) |
| Integrate directly with the HTTP surfaces | [API reference](api/index.md) |

## How DeltaLLM fits together

```text
Applications                  DeltaLLM                         External systems
┌──────────────┐       ┌──────────────────────────┐       ┌──────────────────┐
│ OpenAI SDKs  │──────▶│ Data plane               │──────▶│ LLM providers    │
│ HTTP clients │◀──────│ auth · policy · routing  │◀──────│ MCP servers      │
└──────────────┘       │ cache · usage · audit    │       │ webhooks         │
                       ├──────────────────────────┤       └──────────────────┘
┌──────────────┐       │ Control plane            │
│ Operators    │──────▶│ Admin API and Admin UI   │
└──────────────┘       └────────────┬─────────────┘
                                   │
                              PostgreSQL
                         Redis coordination/cache
```

Read [Architecture](concepts/architecture.md) for component ownership and deployment
boundaries, or [Life of a request](concepts/request-lifecycle.md) for the policy and routing
sequence.

## Before production

Docker Compose is the local evaluation path, not a production high-availability design.
Production deployments need externally managed durable storage, restricted network access,
coordinated database migrations before application rollout, multiple replicas, and monitored
failure behavior. Begin with the [deployment overview](deployment/index.md) and
[Kubernetes guide](deployment/kubernetes.md).

## Get help and contribute

- Report defects in [GitHub Issues](https://github.com/deltawi/deltallm/issues).
- Discuss features in [GitHub Discussions](https://github.com/deltawi/deltallm/discussions).
- Follow the [documentation contribution guide](https://github.com/deltawi/deltallm/blob/main/CONTRIBUTING_DOCS.md) when updating this site.
