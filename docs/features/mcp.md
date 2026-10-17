# MCP tools: details and limits

MCP lets a model use tools provided by another service. DeltaLLM controls which MCP servers and
tools an application can see and use.

That gives you two ways to use the same approved tool catalog:

- direct MCP access through `POST /mcp`
- OpenAI-compatible chat or responses requests that include `tools: [{ "type": "mcp", ... }]`

## Recommended first setup

For the first successful rollout:

1. register one MCP server
2. refresh capabilities and run a health check
3. add one binding for the organization, team, or API key that should see it
4. add one enabled tool policy with `require_approval: never`
5. verify the tool through `/mcp` before letting a model auto-call it

For a guided setup, start with [Connect your first MCP server](../getting-started/mcp-quickstart.md).

## What DeltaLLM controls

An MCP server exposes tools. DeltaLLM adds the access and safety rules around them:

- server registry with health and capability refresh
- scoped visibility through bindings
- per-tool policy controls
- optional manual approvals
- per-tool rate limiting, concurrency limits, and result caching
- audit logging and metrics
- namespaced tool exposure into chat and responses APIs

## How it works

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

## Choose who can see tools

Normal API keys do not automatically see all MCP tools. DeltaLLM resolves visibility from bindings.

Supported binding scopes:

- `organization`
- `team`
- `api_key`

When more than one rule applies, DeltaLLM checks them in this order:

```text
api_key -> team -> organization
```

The first matching rule wins. If it has a `tool_allowlist`, the caller can see only those tools.

### Important behavior

- a request-level `allowed_tools` list only narrows already visible tools
- disabled tool policies hide the tool from normal callers
- the master key bypasses normal binding and policy visibility, which is useful for testing

## Tool policies

Tool policies use the same order:

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

With manual approval, one approval allows one tool call. The caller sends the identical call again after an operator approves it. For the full flow and the time limits, see [Manual approval](../api/mcp.md#manual-approval).

## Call a tool directly

Use `POST /mcp` when your application should choose the tool instead of asking a model to choose it.

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

## Let a model call a tool

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

Streaming requests (`"stream": true`) also work. DeltaLLM streams the model text as it arrives, runs
each MCP tool call between model answers, and continues in the same response. The client does not
see the MCP tool call chunks. For the stream format, see [Streaming](../api/mcp.md#streaming).

If a caller can see many tools, set `"tool_search": true` on the MCP entry. The model then gets
one search tool and DeltaLLM adds only the tools that match its search. Use `"server": "*"` to
search all tools that the caller can see. See [Tool search](../api/mcp.md#tool-search).

The `usage` of a request is the sum of all model calls that the request made, so the spend includes
the model calls that asked for tools.

### Current limits

- a streaming request with MCP tools must use `n: 1`
- chat and responses requests do not support manual approval. DeltaLLM rejects the request if a tool that it can use requires manual approval. Use `allowed_tools` to remove that tool, or call it through `/mcp`
- the upstream model still needs reliable tool-calling support

For early production rollouts, verify the provider and model combination with a real tool call path before enabling it broadly.

## MCP server requirements

DeltaLLM currently supports:

- transport: `streamable_http`
- request format: JSON-RPC `2.0`
- response content type: JSON or `text/event-stream` for Streamable HTTP POST responses
- stateful HTTP sessions through upstream `MCP-Session-Id` headers

For compatibility with common client config names, the admin API also accepts `transport: "http"` and stores it as `streamable_http`.

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

## Monitor MCP tools

DeltaLLM records MCP activity in both audits and metrics.

Operator workflows include:

- refreshing tool capabilities after an upstream server changes
- running on-demand health checks
- inspecting recent call counts, failures, and approval volume per server
- reviewing and deciding approval requests

For the day-to-day UI workflow, see [Admin UI: MCP](../admin-ui/mcp.md).

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `No visible MCP tools are available for server 'docs'` | The caller has no matching binding, or the binding and policy combination hides every tool |
| `Unknown MCP tool 'docs.search'` | The tool is not visible to that caller, or the upstream capabilities changed and were not refreshed |
| Health check reports `unhealthy` | Base URL, upstream auth, or network reachability is wrong |
| `/mcp` works but chat fails | The provider/model may not support tool calling well enough |
| A stream ends with `data: {"error": ...}` and no `[DONE]` | A tool or a later model call failed after the stream started. The error message tells which |
| Chat returns a manual approval error | Remove the tool with `allowed_tools`, or use `/mcp tools/call`, approve the request, and send the identical call again |

## Related pages

- [Connect your first MCP server](../getting-started/mcp-quickstart.md)
- [Admin UI: MCP](../admin-ui/mcp.md)
- [API Reference: MCP Gateway & Tooling](../api/mcp.md)
