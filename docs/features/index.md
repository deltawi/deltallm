# Features

Use this section when DeltaLLM is already running and you want to turn on a capability, understand how it behaves, or decide which feature to use.

## Start With the Outcome You Want

| Goal | Read this |
|------|-----------|
| Protect access to the gateway | [Authentication & SSO](authentication.md) |
| Connect external MCP tools and expose them safely | [MCP Gateway & Tools](mcp.md) |
| Spread traffic across multiple deployments | [Routing & Failover](routing.md) |
| Lower latency and cost for repeated requests | [Caching](caching.md) |
| Block or sanitize unsafe content | [Guardrails](guardrails.md) |
| Control request volume at each scope | [Rate Limiting](rate-limiting.md) |
| Track or cap spend | [Budgets & Spend](budgets.md) |
| Export evidence for compliance or investigations | [Audit Log](audit-log.md) |
| Monitor health, latency, and request volume | [Observability](observability.md) |
| Use gRPC for vLLM or Triton inference servers | [gRPC Transport](grpc-transport.md) |

## Quick Success Pattern

Most feature pages in this section follow the same order:

1. Turn the feature on with the smallest working configuration
2. Verify it with one request, API call, or UI action
3. Read the advanced options only if you need them

If you are still trying to get DeltaLLM running for the first time, go back to [Getting Started](../getting-started/index.md) first.

## Where Other Capabilities Live

Some DeltaLLM capabilities are documented outside the Features section because they are primarily control-plane workflows:

- [Model Deployments](../configuration/models.md) explains how runtime models are defined
- [MCP Servers](../admin-ui/mcp.md) covers the operator workflow for server registration, bindings, policies, and approvals
- [Admin UI](../admin-ui/index.md) covers operator workflows such as Models, Route Groups, Prompt Registry, Batch Jobs, and Settings
- [API Reference](../api/index.md) documents the public proxy API and admin API endpoints
