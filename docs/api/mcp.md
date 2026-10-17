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

For a guided setup, see [Connect your first MCP server](../getting-started/mcp-quickstart.md).

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

### Manual approval

When a tool policy has `require_approval: manual`, a `tools/call` does not run the tool immediately.

1. DeltaLLM creates an approval request and returns error `-32008`. The error `data.approval_request_id` field contains the request ID.
2. An operator approves or rejects the request in the Admin UI or with the admin API.
3. The caller sends the identical call again: the same API key, tool, arguments, and forwarded headers.
4. If the request is approved, DeltaLLM runs the tool one time. If the request is rejected, DeltaLLM returns error `-32009` with the same `approval_request_id`.

Rules:

- One approval allows one execution. A second identical call creates a new approval request.
- An approval stays usable for 15 minutes after the decision. After that time, the next identical call creates a new request.
- A rejection applies to identical calls for 15 minutes.
- A pending request expires after 24 hours if nobody decides it. An expired request cannot be approved.
- DeltaLLM uses the approval before it calls the upstream server. If the upstream call then fails, the caller must get a new approval.

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
| `tool_search` | Optional, default `false`. When `true`, the model gets a search tool instead of the tools of this entry. See [Tool search](#tool-search) |

`server` can be `"*"` (all servers that the caller can see) only together with `"tool_search": true`.

### Tool search

A caller can see many MCP tools. If all tool definitions go into the prompt, the prompt is long
and the model can choose the wrong tool. With `"tool_search": true`, DeltaLLM gives the model one
tool, `mcp_tool_search`, instead of the tools of that entry.

1. The model calls `mcp_tool_search` with a `query`, for example `"create github issue"`, and an
   optional `limit` (1 to 10, default 5).
2. DeltaLLM searches the names, descriptions, and parameter names of the tools of that entry. The
   tool result lists the tools that match.
3. DeltaLLM adds the matching tools to the next model call. The model then calls them as usual.

```json
{
  "model": "gpt-4o-mini",
  "messages": [{"role": "user", "content": "File a bug for the login error."}],
  "tools": [{"type": "mcp", "server": "*", "tool_search": true}]
}
```

Rules:

- The search uses only tools that the caller can already see. A search does not give access to a tool.
- Tools that require manual approval are not in the search results. Call them through `/mcp`.
- Searches can add a maximum of 32 tools to one request.
- Each search is one model call, so it counts against the tool hop limit.
- The search is a word match (BM25). It runs in the gateway process and adds no network calls.
- You can use a normal entry and a search entry together, for example
  `[{"type": "mcp", "server": "docs"}, {"type": "mcp", "server": "*", "tool_search": true}]`.
  The `docs` tools are always present, and the model searches for the other tools.
- If your own function tool has the name `mcp_tool_search`, DeltaLLM rejects the request.
- The metric `deltallm_mcp_tool_search_total{result="hit|empty"}` counts searches.

### Streaming

Set `"stream": true` to stream a request that uses MCP tools. This works on
`/v1/chat/completions` and `/v1/responses`.

- DeltaLLM sends the model text to the client as it arrives.
- DeltaLLM does not send the tool call chunks of an MCP tool to the client. It runs the tool and then
  streams the next model answer in the same response.
- Before a tool runs, the stream sends an SSE comment line, for example `: mcp_tool_call docs.search`.
  While the tools run, the stream sends `: mcp_tool_running` every 15 seconds.
  SSE clients ignore comment lines. The lines prevent idle proxies from closing the connection.
- If the model calls one of your own `function` tools, the client gets the tool call chunks and
  `finish_reason: "tool_calls"`, the same as in a stream without MCP tools.
- All chunks have the `id` of the first model answer.
- With `"stream_options": {"include_usage": true}`, the last chunk before `[DONE]` has the usage of
  all model calls in the request together.
- If a tool or a later model call fails after the stream started, the last frame is
  `data: {"error": {...}}` and DeltaLLM does not send `[DONE]`.
- A streaming request with MCP tools must use `n: 1`.

Example frames:

```text
data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{"role":"assistant","content":"Let me search. "}}]}

: mcp_tool_call docs.search

data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{"content":"DeltaLLM is a gateway."}}]}

data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

### Usage and billing

One request with MCP tools can call the model more than one time: one call that asks for a tool,
and one more call after each tool result. The `usage` in the response, and the spend that DeltaLLM
records, is the sum of all these model calls. This is true for streaming and non-streaming requests.

### Important Limits

- chat and responses requests do not support manual approval. If a tool that the request can use requires manual approval, DeltaLLM rejects the full request with HTTP `400`. Use `allowed_tools` to remove that tool from the request, or call the tool through `/mcp`
- the upstream model/provider must support tool calling well enough for the bridge to work reliably

## Admin MCP Endpoints

These endpoints back the MCP pages in the Admin UI.

### Server Registry

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/ui/api/mcp-servers` | List servers visible to the admin scope |
| `POST` | `/ui/api/mcp-servers` | Create a server |
| `GET` | `/ui/api/mcp-servers/{server_id}` | Get one server with visible tools, bindings, and policies |
| `GET` | `/ui/api/mcp-servers/{server_id}/operations` | Read recent usage, failures, and approval counts |
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

`GET /ui/api/mcp-approval-requests` accepts these query parameters:

| Parameter | Description |
| --- | --- |
| `server_id` | Show only requests for this MCP server |
| `status` | `pending`, `approved`, `rejected`, or `expired`. A pending request that is past its expiry time shows as `expired` |
| `limit` | Page size, from 1 to 500. The default is 100 |
| `offset` | Number of rows to skip |

Each request contains `consumed_at`. It is set when an approved request was used for its one execution. An approval that nobody used before its window ended shows as `expired`.

Each request also contains `capabilities`:

- `can_decide`: the request is pending and you can decide it.
- `can_approve`: you can approve it. This is `false` when you own the API key that made the call.

The decision body is `{"status": "approved" | "rejected", "decision_comment": "optional text"}`. The decision endpoint returns:

- `403` if the account that decides the request is the owner of the API key that made the call. Another operator must approve it. The requester can reject their own request.
- `404` if the request is not pending, for example because somebody decided it already.
- `409` if the pending request expired.

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

`transport: "http"` is accepted as a compatibility alias and normalized to `streamable_http`.
Streamable HTTP servers may return either JSON or `text/event-stream` JSON-RPC responses; DeltaLLM also reuses upstream `MCP-Session-Id` values when a server establishes a stateful session.

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

- [Connect your first MCP server](../getting-started/mcp-quickstart.md)
- [MCP Gateway & Tools](../features/mcp.md)
- [Admin UI: MCP](../admin-ui/mcp.md)
