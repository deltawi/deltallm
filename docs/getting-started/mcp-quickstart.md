# MCP Quick Start

Use this guide when DeltaLLM is already running and you want your first successful MCP-backed tool call as quickly as possible.

This path assumes:

- DeltaLLM is reachable on `http://localhost:4000` or `http://localhost:8000`
- you already have a compatible MCP server that speaks JSON-RPC over `streamable_http`
- you have either the DeltaLLM master key or admin UI access

## What You Will Set Up

In about five minutes you will:

1. register an MCP server in DeltaLLM
2. fetch its tool list
3. expose it to an organization, team, or API key
4. run a direct `tools/call`
5. let a chat model use the same tool through the OpenAI-compatible chat API

## 1. Pick the Right Base URL

Use the DeltaLLM URL that matches your setup:

- Docker quick start: `http://localhost:4000`
- local backend: `http://localhost:8000`

If your MCP server runs on your host machine:

- use `http://localhost:PORT/...` when DeltaLLM also runs locally
- use `http://host.docker.internal:PORT/...` when DeltaLLM runs in Docker

Export the values once:

```bash
export BASE="http://localhost:4000"
export MASTER_KEY="YOUR_MASTER_KEY"
```

## 2. Register an MCP Server

Create one server record in DeltaLLM. Replace `base_url` with your real MCP endpoint.

```bash
curl -X POST "$BASE/ui/api/mcp-servers" \
  -H "Authorization: Bearer $MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "server_key": "docs",
    "name": "Docs MCP",
    "transport": "streamable_http",
    "base_url": "http://host.docker.internal:8899/mcp",
    "auth_mode": "none",
    "enabled": true
  }'
```

Important fields:

- `server_key`: stable identifier that clients will later reference in chat requests
- `transport`: currently must be `streamable_http`
- `auth_mode`: one of `none`, `bearer`, `basic`, or `header_map`

Save the returned `mcp_server_id`.

## 3. Refresh Capabilities and Run a Health Check

```bash
export SERVER_ID="mcp-server-id-from-create"

curl -X POST "$BASE/ui/api/mcp-servers/$SERVER_ID/refresh-capabilities" \
  -H "Authorization: Bearer $MASTER_KEY"

curl -X POST "$BASE/ui/api/mcp-servers/$SERVER_ID/health-check" \
  -H "Authorization: Bearer $MASTER_KEY"
```

What success looks like:

- refresh returns a `tools` array
- health check returns `health.status: "healthy"`

## 4. Make the Server Visible to a Caller

MCP tools are not globally visible to normal API keys. You must bind the server to a scope.

Bind it to an organization:

```bash
curl -X POST "$BASE/ui/api/mcp-bindings" \
  -H "Authorization: Bearer $MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "server_id": "'"$SERVER_ID"'",
    "scope_type": "organization",
    "scope_id": "org_123",
    "enabled": true
  }'
```

Or bind it more narrowly to:

- a `team`
- a single `api_key`

If you want to expose only some tools, set `tool_allowlist`.

Use a scoped API key that belongs to the bound organization, team, or API key scope for the runtime verification steps below.

The master key is still useful for operator setup, but it bypasses normal MCP visibility rules and is not the right key for validating end-user access.

If you do not already have a scoped key in that scope, create one first from the [Admin UI: API Keys](../admin-ui/api-keys.md) page or the `/ui/api/keys` admin API.

## 5. Add a Tool Policy

For the first test, keep the policy simple:

```bash
curl -X POST "$BASE/ui/api/mcp-tool-policies" \
  -H "Authorization: Bearer $MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "server_id": "'"$SERVER_ID"'",
    "tool_name": "search",
    "scope_type": "organization",
    "scope_id": "org_123",
    "enabled": true,
    "require_approval": "never"
  }'
```

Later you can add:

- `max_rpm`
- `max_concurrency`
- `result_cache_ttl_seconds`
- `max_total_execution_time_ms`

## 6. Verify the MCP Gateway Directly

List visible tools:

```bash
curl "$BASE/mcp" \
  -H "Authorization: Bearer YOUR_SCOPED_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

Call one tool:

```bash
curl "$BASE/mcp" \
  -H "Authorization: Bearer YOUR_SCOPED_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc":"2.0",
    "id":2,
    "method":"tools/call",
    "params":{
      "name":"docs.search",
      "arguments":{"query":"DeltaLLM"}
    }
  }'
```

DeltaLLM namespaces tools as `server_key.tool_name`, so a tool named `search` on server `docs` becomes `docs.search`.

## 7. Use the Same Tool from Chat

Once direct MCP works, let the model call the tool through the chat API.

```bash
curl "$BASE/v1/chat/completions" \
  -H "Authorization: Bearer YOUR_SCOPED_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "Use the docs MCP search tool to look up DeltaLLM."
      }
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
  }'
```

Notes:

- `allowed_tools` narrows the visible tools for this request. It does not grant permission by itself.
- choose a model/provider combination that supports OpenAI-style tool calling reliably
- MCP tools are currently supported only on non-streaming chat and responses requests

## Fast Troubleshooting

| Symptom | What to check first |
| --- | --- |
| `No visible MCP tools are available` | Missing binding, wrong scope, or binding allowlist excludes the tool |
| `tools/list` returns an empty array | Capabilities were not refreshed or the bound scope has no visible tools |
| Health check is `unhealthy` | Wrong `base_url`, wrong auth settings, or DeltaLLM cannot reach the upstream MCP server |
| Chat request returns `manual approval` error | Manual approval is supported through `/mcp`, not through chat/responses auto-execution |
| Chat request fails upstream during tool calling | Use a model that supports tool calling more reliably, and keep the prompt explicit |

## Where to Go Next

- [MCP Gateway & Tools](../features/mcp.md)
- [Admin UI: MCP Servers](../admin-ui/mcp.md)
- [API Reference: MCP Gateway & Tooling](../api/mcp.md)
