# MCP Gateway & Tools

DeltaLLM can act as an MCP gateway and as an MCP-to-chat bridge.

That gives you two ways to use the same approved tool catalog:

- direct MCP access through `POST /mcp`
- OpenAI-compatible chat or responses requests that include `tools: [{ "type": "mcp", ... }]`

## Quick Path

For the first successful rollout:

1. register one MCP server
2. refresh capabilities and run a health check
3. add one binding for the organization, team, or API key that should see it
4. add one enabled tool policy with `require_approval: never`
5. verify the tool through `/mcp` before letting a model auto-call it

If you want the shortest path, start with [MCP Quick Start](../getting-started/mcp-quickstart.md).

## What DeltaLLM Adds on Top of MCP

Raw MCP servers expose tools. DeltaLLM adds the control plane around them:

- server registry with health and capability refresh
- scoped visibility through bindings
- per-tool policy controls
- optional manual approvals
- per-tool rate limiting, concurrency limits, and result caching
- audit logging and metrics
- namespaced tool exposure into chat and responses APIs

## How It Works

```text
App or model
  -> DeltaLLM
    -> resolve visible MCP servers for the caller
    -> filter tools by binding allowlist
    -> apply the effective per-tool policy
    -> call upstream MCP server over streamable HTTP
    -> return tool result through /mcp or back into chat execution
```

DeltaLLM namespaces each tool as:

```text
server_key.tool_name
```

So a `search` tool on a server with key `docs` is exposed as `docs.search`.

## Visibility and Scope Resolution

Normal API keys do not automatically see all MCP tools. DeltaLLM resolves visibility from bindings.

Supported binding scopes:

- `organization`
- `team`
- `api_key`

Resolution precedence is:

```text
api_key -> team -> organization
```

The most specific binding wins. If that winning binding has a `tool_allowlist`, only those tools are visible.

### Important Behaviors

- a request-level `allowed_tools` list only narrows already visible tools
- disabled tool policies hide the tool from normal callers
- the master key bypasses normal binding and policy visibility, which is useful for testing

## Tool Policies

Policies are scoped the same way as bindings and use the same precedence:

```text
api_key -> team -> organization
```

Each policy can control:

- whether the tool is enabled
- whether it requires approval
- requests per minute
- max concurrency
- result cache TTL
- max total execution time

Use manual approval when a tool can take irreversible or sensitive actions. Use `never` when you want low-friction retrieval and read-only integrations.

## Direct MCP Gateway

Use `POST /mcp` when you want deterministic tool access without relying on model tool selection.

Supported JSON-RPC methods:

- `initialize`
- `ping`
- `tools/list`
- `tools/call`

This is the best first verification path because it isolates:

- DeltaLLM-to-MCP connectivity
- auth and header forwarding
- binding visibility
- policy enforcement

See [API Reference: MCP Gateway & Tooling](../api/mcp.md) for examples.

## Chat and Responses Bridge

DeltaLLM also lets OpenAI-compatible chat and responses requests reference MCP servers directly.

Example request shape:

```json
{
  "model": "gpt-4o-mini",
  "messages": [
    {"role": "user", "content": "Search the docs for DeltaLLM."}
  ],
  "tools": [
    {
      "type": "mcp",
      "server": "docs",
      "allowed_tools": ["search"],
      "require_approval": "never"
    }
  ],
  "tool_choice": "required"
}
```

DeltaLLM translates the visible MCP tools into OpenAI-style function tools, executes any resulting tool calls, and feeds the tool result back into the model.

### Current Limits

- MCP tools are not supported on streaming chat requests yet
- MCP tools are not supported on streaming responses requests yet
- tools that require manual approval are not auto-executed in chat or responses flows
- the upstream model still needs reliable tool-calling support

For early production rollouts, verify the provider and model combination with a real tool call path before enabling it broadly.

## Upstream MCP Server Requirements

DeltaLLM currently supports:

- transport: `streamable_http`
- request format: JSON-RPC `2.0`
- response content type: JSON, not SSE event streams

Supported upstream auth modes:

- `none`
- `bearer`
- `basic`
- `header_map`

If you need to pass selected end-user headers through DeltaLLM, use `forwarded_headers_allowlist`. Callers then send headers in this form:

```text
x-deltallm-mcp-<server_key>-<header-name>: <value>
```

Example:

```text
x-deltallm-mcp-github-authorization: Bearer ...
```

## Operations and Observability

DeltaLLM records MCP activity in both audits and metrics.

Operator workflows include:

- refreshing tool capabilities after an upstream server changes
- running on-demand health checks
- inspecting recent call counts, failures, and approval volume per server
- reviewing and deciding approval requests

For the day-to-day UI workflow, see [Admin UI: MCP Servers](../admin-ui/mcp.md).

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `No visible MCP tools are available for server 'docs'` | The caller has no matching binding, or the binding and policy combination hides every tool |
| `Unknown MCP tool 'docs.search'` | The tool is not visible to that caller, or the upstream capabilities changed and were not refreshed |
| Health check reports `unhealthy` | Base URL, upstream auth, or network reachability is wrong |
| `/mcp` works but chat fails | The provider/model may not support tool calling well enough, or the request is streaming |
| Chat returns a manual approval error | Use `/mcp tools/call`, approve the request, and retry there |

## Related Pages

- [MCP Quick Start](../getting-started/mcp-quickstart.md)
- [Admin UI: MCP Servers](../admin-ui/mcp.md)
- [API Reference: MCP Gateway & Tooling](../api/mcp.md)
