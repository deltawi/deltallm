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
  If all tools of a search entry require manual approval, DeltaLLM rejects the request with HTTP 400.
- Searches can add a maximum of 32 tools to one request. When this limit is reached, a search lists
  only the tools that are already in the request.
- With `"server": "*"`, `allowed_tools` can also contain namespaced names, for example
  `"github.create_issue"`, to select the tool of one server.
- If the model calls `mcp_tool_search` together with one of your own function tools in one turn,
  DeltaLLM returns all the tool calls to the client. Do not use your own function tools with tool
  search if your client cannot ignore an `mcp_tool_search` call.
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

When accounting is on, DeltaLLM makes one spend reservation for the full request before the first
model call. The reservation has space for each model call that the tool loop can make. The number of
calls is `accounting_max_provider_attempts` (default `3`) plus the MCP hop limit (default `4`), with a
maximum of `128`. Thus a request with MCP tools holds more budget than a request without tools while
it runs. When the request ends, DeltaLLM charges the sum of the model calls and releases the
remainder of the reservation. Only a failed or retried model call keeps its part of the reservation
as unresolved spend.

### Important Limits

- chat and responses requests do not support manual approval. If a tool that the request can use requires manual approval, DeltaLLM rejects the full request with HTTP `400`. Use `allowed_tools` to remove that tool from the request, or call the tool through `/mcp`
- the upstream model/provider must support tool calling well enough for the bridge to work reliably

## Per-user OAuth servers

A server with `auth_mode: "oauth2"` (OAuth 2.0, per user) has no shared credential. Each user
connects an own account on the **Connected accounts** page. A tool call then uses the token of
the user who owns the API key.

Rules for a tool call:

- The tools of the server stay in `tools/list`, also for users without a connection.
- An API key that a user owns uses the connection of that user.
- An API key that no user owns (for example, a service account key) cannot use the server.
- When the access token expires, the gateway refreshes it before the call. Only one refresh
  runs at a time for each connection, also across replicas.
- When the MCP server rejects the token with HTTP `401` or `403`, the gateway refreshes the
  token and tries the call one more time.
- When the authorization server rejects the refresh token (`invalid_grant`), the connection
  changes to **Connect again**.

### Connection required

Without a usable connection, `tools/call` returns a tool result with `isError: true`. It is not
a JSON-RPC error, so the client and the model can show the message to the user:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "Connect your GitHub account to use this tool.\nOpen https://gateway.example.com/ui/connect/github"
      }
    ],
    "structuredContent": {
      "error": {
        "type": "mcp_connection_required",
        "server": "github",
        "message": "Connect your GitHub account to use this tool.",
        "connect_url": "https://gateway.example.com/ui/connect/github"
      }
    },
    "isError": true
  }
}
```

| `error.type` | When | Message |
| --- | --- | --- |
| `mcp_connection_required` | The user has no connection | `Connect your <Name> account to use this tool.` |
| `mcp_connection_required` with `reason: "needs_reconnect"` | The server revoked the token | `Connect your <Name> account again to use this tool.` |
| `mcp_user_key_required` | No user owns the API key | `This key is not owned by a user. Per-user OAuth tools need a user key.` |

`connect_url` is `<email_base_url>/ui/connect/<server_key>`. If `general_settings.email_base_url`
is not set, the error has no `connect_url` and the text has no link.

In chat and responses requests, the tool message to the model contains the same `error` object.
The model can tell the user to open the link.

### Connect flow

1. The user selects **Connect**. The gateway makes a PKCE S256 verifier and a random state. The
   state is bound to the signed-in account, works one time, and expires after 10 minutes.
2. The browser opens the authorization endpoint of the server.
3. The authorization server sends the browser to `GET /mcp/oauth/callback`. The gateway checks
   the state and the signed-in account, exchanges the code, and stores the tokens encrypted for
   the pair of server and account.
4. The gateway sends the browser to `/ui/connect/<server_key>?result=connected` (or
   `result=error&reason=<code>`).

**Disconnect** revokes the refresh token (RFC 7009) when the server has a revocation endpoint,
then deletes the tokens. When a user account or a server is deleted, its connections are also
deleted. When an administrator changes the base URL of a server or changes its auth mode, the
gateway deletes all connections of that server, because a token is valid for one resource only.

### Discovery

When an administrator saves an `oauth2` server, the gateway finds the authorization server:

1. It sends `initialize` without credentials and reads `resource_metadata` from the
   `WWW-Authenticate` header (RFC 9728).
2. It reads `/.well-known/oauth-protected-resource` (path-aware, then root).
3. It reads the authorization server metadata (RFC 8414), then OpenID configuration.
4. When no resource metadata exists, it uses the origin of the server as the authorization server.

The server must declare PKCE with `S256`. When the administrator gives no client ID, the gateway
registers a public client (RFC 7591). Discovery and registration errors do not block the save;
the server shows the error, and **Run again** repeats the steps.

Capability refresh and health check on an `oauth2` server use the connection of the
administrator who runs them. Without a connection, they return `409` with the steps to connect.
The master key has no account, so it cannot run them.

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

### OAuth 2.0 (per user)

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/ui/api/mcp-oauth/settings` | Read the redirect URL and whether the key and the public base URL are set |
| `POST` | `/ui/api/mcp-servers/oauth/discover` | Run discovery for a `base_url` before the server exists. Stores nothing |
| `POST` | `/ui/api/mcp-servers/{server_id}/oauth/discovery` | Run discovery again, register a client when none is set, and store the result |
| `GET` | `/ui/api/mcp-connections` | List the `oauth2` servers that the signed-in user can use, with the connection status |
| `POST` | `/ui/api/mcp-connections/{server_key}/connect` | Start the connect flow. Returns `authorization_url` |
| `DELETE` | `/ui/api/mcp-connections/{server_key}` | Revoke and delete the connection of the signed-in user |
| `GET` | `/mcp/oauth/callback` | Redirect target of the authorization server. Not for API clients |

The connection endpoints act for the signed-in account only. The master key has no account:
`connect` returns `403` and the list is empty.

For an `oauth2` server, the server payload contains `oauth`:

```json
{
  "client_id": "dyn-7f3a",
  "client_id_source": "dynamic",
  "client_secret_set": false,
  "scopes": ["repo", "read:user"],
  "discovery": {"authorization_server": "https://auth.example.com", "found_from": "WWW-Authenticate header", "...": "..."},
  "discovery_error": null
}
```

The client secret is write-only. The API returns only `client_secret_set`.

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

Create a server with OAuth 2.0 (per user):

```json
{
  "server_key": "github",
  "name": "GitHub",
  "base_url": "https://api.githubcopilot.com/mcp",
  "auth_mode": "oauth2",
  "auth_config": {
    "client_id": "",
    "client_secret": "optional, write-only",
    "scopes": ["repo", "read:user"]
  }
}
```

An empty `client_id` uses dynamic client registration. On update, a missing field keeps the
stored value. Send `"clear_client_secret": true` to remove the stored secret. The server needs
`general_settings.mcp_oauth_encryption_key` and `general_settings.email_base_url`; without them
the API returns `400`.

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

## MCP Bundles

A bundle shows the tools of several MCP servers at one endpoint. A bundle does not give access. Bindings, tool policies and approvals stay the only access rules. A caller sees only the bundle tools that their key can already use through `POST /mcp`.

### Bundle Visibility

Each bundle has an owner scope:

| Owner scope | Who can see the bundle |
| --- | --- |
| `global` | All callers |
| `organization` | Keys of that organization |
| `team` | Keys of that team |

A disabled bundle, or a bundle with an owner scope that does not include the key, gives the same result as an unknown name: HTTP `404` on `/mcp/{bundle_key}`, and HTTP `400` on chat and responses.

### Tool Names

A bundle member is one server, a list of its tools (or all tools), and an optional name prefix.

- With a prefix, the bundle shows the tool as `<prefix>_<tool>`. For example, prefix `gh` and tool `create_issue` give `gh_create_issue`.
- Without a prefix, the bundle shows the tool name of the server.
- Two tools in one bundle cannot have the same name. The API rejects the save with HTTP `400`, and the message names the two tools.
- The name `mcp_tool_search` is reserved.

DeltaLLM sends each call to the real server and tool. Audit events and usage records use the namespaced name (`github.create_issue`) and add `bundle_key` to the audit metadata.

When a server adds a tool later, and that tool name is already in the bundle, the bundle keeps the first tool. Use a name prefix to prevent this.

### Bundle Endpoint

```text
POST /mcp/{bundle_key}
```

The endpoint supports `initialize`, `ping`, `tools/list` and `tools/call`. `tools/list` returns the bundle names. `tools/call` accepts the bundle names only.

```bash
curl http://localhost:8000/mcp/eng-assist \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"gh_create_issue","arguments":{"title":"Bug"}}}'
```

`POST /mcp` does not change. It does not show bundle tools.

### Bundles in Chat and Responses

Put the bundle key in the `server` field:

```json
{
  "model": "gpt-4o-mini",
  "messages": [{"role": "user", "content": "List the open pull requests"}],
  "tools": [
    {"type": "mcp", "server": "eng-assist", "allowed_tools": ["gh_list_pull_requests"]}
  ]
}
```

- `allowed_tools` and `tool_search` use the bundle names.
- Streaming and non-streaming requests are supported.
- When a server and a bundle have the same key, the server is used. The API does not let you create a bundle with the key of a server, or a server with the key of a bundle.
- If two MCP entries in one request give the same tool name, or a tool name is also the name of a `function` tool, DeltaLLM rejects the request with HTTP `400`.

### Admin Bundle Endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/ui/api/mcp-bundles` | List the bundles that the admin scope can read. Accepts `search`, `limit` and `offset` |
| `POST` | `/ui/api/mcp-bundles` | Create a bundle |
| `GET` | `/ui/api/mcp-bundles/{bundle_id}` | Get one bundle |
| `PATCH` | `/ui/api/mcp-bundles/{bundle_id}` | Update the name, description, `enabled` flag or members |
| `DELETE` | `/ui/api/mcp-bundles/{bundle_id}` | Delete a bundle |
| `POST` | `/ui/api/mcp-bundles/preview` | Show the tools of a saved or draft bundle, and what one API key can use |

Permissions:

- A platform admin can manage all bundles. Only a platform admin can create a `global` bundle.
- An organization admin can manage the bundles of their organization and of its teams.
- A team admin can read the bundles of their team. They cannot change them.
- A member server must be visible to the admin. A server that an organization owns can be in a bundle of that organization or of its teams only.
- You cannot change `bundle_key` or the owner scope after you create the bundle.

DeltaLLM writes the audit actions `ADMIN_MCP_BUNDLE_CREATE`, `ADMIN_MCP_BUNDLE_UPDATE` and `ADMIN_MCP_BUNDLE_DELETE`.

Create a bundle:

```json
{
  "bundle_key": "eng-assist",
  "name": "Engineering assistant",
  "description": "Code review and issue triage",
  "owner_scope_type": "organization",
  "owner_scope_id": "org-acme",
  "members": [
    {"mcp_server_id": "mcp-github", "all_tools": true, "name_prefix": "gh"},
    {"mcp_server_id": "mcp-jira", "all_tools": false, "tool_names": ["search"], "name_prefix": "jira"}
  ]
}
```

Rules:

- `bundle_key`: 1 to 63 characters, `a-z`, `0-9`, `-` and `_`. It must start with a letter or a digit.
- `name_prefix`: 1 to 32 characters, letters, digits, `-` and `_`.
- A bundle can have at most 50 servers. One member can list at most 500 tools.
- Each tool in `tool_names` must be a tool of the server.

Preview a bundle. Send `mcp_bundle_id` for a saved bundle, or the owner scope and the members for a draft. `api_key` is optional. It is the key hash (`token`) from `GET /ui/api/keys`.

```json
{
  "mcp_bundle_id": "bundle-1",
  "api_key": "KEY_TOKEN_HASH"
}
```

The response contains:

- `servers`: each member server with its tools. Each tool has `needs_approval` and `disabled_by_policy` for the owner scope.
- `tools`: the bundle names and the real server and tool of each name.
- `errors`: the problems that stop a save, for example a name conflict.
- `key`: when you send `api_key`, the result for that key. `sees_bundle` is `false` when the owner scope does not include the key. Each tool has an `access` value: `available`, `needs_approval`, `disabled_by_policy` or `no_access`.

## Related Pages

- [Connect your first MCP server](../getting-started/mcp-quickstart.md)
- [MCP Gateway & Tools](../features/mcp.md)
- [Admin UI: MCP](../admin-ui/mcp.md)
- [Admin UI: Connected accounts](../admin-ui/connected-accounts.md)
