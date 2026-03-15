# MCP Gateway & Tooling

DeltaLLM exposes MCP in two separate API shapes:

- `POST /mcp` for direct JSON-RPC tool access
- OpenAI-compatible chat and responses requests with `tools: [{ "type": "mcp", ... }]`

Use `/mcp` when you want deterministic tool execution. Use chat or responses when you want the model to decide when to call a visible tool.

## Quick Success Path

1. register the server and refresh capabilities
2. add a binding and tool policy for the caller scope
3. call `tools/list` through `/mcp`
4. call `tools/call` through `/mcp`
5. only then move to chat or responses auto-execution

For the shortest setup path, see [MCP Quick Start](../getting-started/mcp-quickstart.md).

## Direct MCP Gateway

### Endpoint

```text
POST /mcp
```

Auth uses the same bearer token pattern as the rest of the DeltaLLM proxy API:

```text
Authorization: Bearer YOUR_API_KEY
```

### Supported Methods

| Method | Purpose |
| --- | --- |
| `initialize` | Returns DeltaLLM's MCP gateway capabilities |
| `ping` | Lightweight liveness check |
| `tools/list` | Lists the tools visible to the authenticated caller |
| `tools/call` | Executes a visible namespaced tool |

### Initialize

```bash
curl http://localhost:8000/mcp \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
```

### List Tools

```bash
curl http://localhost:8000/mcp \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
```

Example success payload:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "tools": [
      {
        "name": "docs.search",
        "description": "Search docs",
        "inputSchema": {
          "type": "object",
          "properties": {
            "query": {"type": "string"}
          },
          "required": ["query"]
        }
      }
    ]
  }
}
```

### Call a Tool

```bash
curl http://localhost:8000/mcp \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc":"2.0",
    "id":3,
    "method":"tools/call",
    "params":{
      "name":"docs.search",
      "arguments":{"query":"DeltaLLM"}
    }
  }'
```

Tool names must be namespaced as `server_key.tool_name`.

### JSON-RPC Error Behavior

DeltaLLM returns JSON-RPC errors as HTTP `200` responses with an `error` object.

Common codes:

| Code | Meaning |
| --- | --- |
| `-32600` | Invalid request |
| `-32601` | Unsupported method |
| `-32602` | Invalid params |
| `-32700` | Parse error |
| `-32001` | Upstream auth failure |
| `-32003` | Access denied or policy denied |
| `-32004` | Tool not found or not visible |
| `-32005` | Upstream transport or response failure |
| `-32008` | Approval required |
| `-32009` | Approval denied |
| `-32029` | Tool rate limited |
| `-32030` | Tool execution timed out |

## Chat and Responses Tool Bridge

DeltaLLM accepts MCP tool definitions inside OpenAI-compatible requests.

### Chat Example

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_KEY" \
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

### Responses Example

```bash
curl http://localhost:8000/v1/responses \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "input": "Search the docs for DeltaLLM.",
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

### Request Fields

| Field | Meaning |
| --- | --- |
| `type` | Must be `mcp` |
| `server` | The DeltaLLM `server_key`, such as `docs` |
| `allowed_tools` | Optional subset of visible tool names from that server |
| `require_approval` | Currently `never` in the request contract |

### Important Limits

- streaming chat requests do not support MCP tools yet
- streaming responses requests do not support MCP tools yet
- manual-approval tools are not auto-executed in chat or responses flows
- the upstream model/provider must support tool calling well enough for the bridge to work reliably

## Admin MCP Endpoints

These endpoints back the MCP pages in the Admin UI.

### Server Registry

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/ui/api/mcp-servers` | List servers visible to the admin scope |
| `POST` | `/ui/api/mcp-servers` | Create a server |
| `GET` | `/ui/api/mcp-servers/{server_id}` | Get one server with visible tools, bindings, and policies |
| `GET` | `/ui/api/mcp-servers/{server_id}/operations` | Read recent usage, failures, and approvals |
| `PATCH` | `/ui/api/mcp-servers/{server_id}` | Update a server |
| `DELETE` | `/ui/api/mcp-servers/{server_id}` | Delete a server |
| `POST` | `/ui/api/mcp-servers/{server_id}/refresh-capabilities` | Refresh upstream tools |
| `POST` | `/ui/api/mcp-servers/{server_id}/health-check` | Run `initialize` and `tools/list` against upstream |

### Bindings

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/ui/api/mcp-bindings` | List bindings |
| `POST` | `/ui/api/mcp-bindings` | Create or update a binding |
| `DELETE` | `/ui/api/mcp-bindings/{binding_id}` | Delete a binding |

Binding scopes are `organization`, `team`, and `api_key`.

### Tool Policies

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/ui/api/mcp-tool-policies` | List policies |
| `POST` | `/ui/api/mcp-tool-policies` | Create or update a policy |
| `DELETE` | `/ui/api/mcp-tool-policies/{policy_id}` | Delete a policy |

Policy fields include:

- `enabled`
- `require_approval`
- `max_rpm`
- `max_concurrency`
- `result_cache_ttl_seconds`
- `max_total_execution_time_ms`

### Approval Requests

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/ui/api/mcp-approval-requests` | List approval requests |
| `POST` | `/ui/api/mcp-approval-requests/{approval_request_id}/decision` | Approve or reject a pending request |

## Example Admin Payloads

### Create a Server

```json
{
  "server_key": "docs",
  "name": "Docs MCP",
  "transport": "streamable_http",
  "base_url": "https://mcp.example.com/mcp",
  "enabled": true,
  "auth_mode": "bearer",
  "auth_config": {
    "token": "secret"
  },
  "forwarded_headers_allowlist": ["authorization"],
  "request_timeout_ms": 30000
}
```

### Create a Binding

```json
{
  "server_id": "mcp-1",
  "scope_type": "team",
  "scope_id": "team-ops",
  "enabled": true,
  "tool_allowlist": ["search"]
}
```

### Create a Tool Policy

```json
{
  "server_id": "mcp-1",
  "tool_name": "search",
  "scope_type": "team",
  "scope_id": "team-ops",
  "enabled": true,
  "require_approval": "never",
  "max_rpm": 60,
  "max_concurrency": 4,
  "result_cache_ttl_seconds": 300,
  "max_total_execution_time_ms": 5000
}
```

## Related Pages

- [MCP Quick Start](../getting-started/mcp-quickstart.md)
- [MCP Gateway & Tools](../features/mcp.md)
- [Admin UI: MCP Servers](../admin-ui/mcp.md)
