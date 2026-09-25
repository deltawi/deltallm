# Build with DeltaLLM

Use these guides after DeltaLLM is running. They explain how to connect models and tools, route
traffic, and add safety and reliability features.

## Common tasks

| I want to… | Read this |
| --- | --- |
| Connect a model or provider | [Choose and connect a model](../guides/models-and-providers.md) |
| Send traffic to several deployments | [Route traffic and handle failures](../guides/routing-and-failover.md) |
| Reuse repeated responses | [Caching](caching.md) |
| Process large jobs in the background | [Process work in batches](../guides/batching.md) |
| Let models use external tools | [Connect your first MCP server](../getting-started/mcp-quickstart.md) |
| Check or clean prompts and responses | [Add your first guardrail](../guides/guardrails.md) |

## A simple way to build

1. Start with one model deployment and one test request.
2. Create a separate application key instead of using the master key.
3. Add routing, caching, limits, or guardrails only when the basic request works.
4. Test failure cases before moving the application to production.

For tasks that cross the Admin UI and API, use the [complete build workflows](../guides/build-workflows.md).

Use [Reference](../reference/index.md) when you need every field or endpoint rather than a guided
task.
