# MCP Servers

The MCP Servers page is where operators register upstream MCP servers, decide who can use them, and govern how each tool executes.

## What This Page Is For

Use this area when you need to:

- register a new MCP server
- refresh the tool catalog after the upstream server changes
- run a health check before exposing the server to applications
- bind a server to an organization, team, or API key
- bind a server to an organization, team, API key, or user
- set policy for a specific tool
- review and decide manual approval requests

## Quick Success Workflow

1. Open **AI Gateway -> MCP Servers**
2. Create one server with a real `base_url`
3. Refresh capabilities and confirm the expected tool list appears
4. Run a health check and confirm the server is healthy
5. Add a binding for the scope that should see the server
6. Add a tool policy for the first tool you want to allow
7. Verify the tool through `/mcp` or a chat request

## Server Fields

When you create or edit a server, the key fields are:

- **Server Key**: stable identifier used in tool names such as `docs.search`
- **Name**: human-readable label in the UI
- **Description**: optional operator context
- **Transport**: currently `streamable_http`
- **Base URL**: upstream MCP endpoint
- **Enabled**: hides or exposes the server at runtime
- **Auth Mode**: `none`, `bearer`, `basic`, or `header_map`
- **Forwarded Headers Allowlist**: which caller-supplied headers DeltaLLM may forward
- **Request Timeout**: per-request timeout to the MCP server

Platform admins can create:

- global servers
- organization-owned servers

Scoped admins can create only organization-owned servers for organizations they manage.

## Refresh and Health Check

Two actions should happen before you bind a server to real traffic:

### Refresh Capabilities

This fetches the upstream `tools/list` response and stores the current tool catalog in DeltaLLM.

Use it when:

- you create the server for the first time
- the upstream MCP server adds, removes, or renames tools
- you need to confirm the current input schemas seen by DeltaLLM

### Health Check

This runs an `initialize` plus `tools/list` sequence against the upstream server and stores the current status, latency, and error.

Use it when:

- a new server was just configured
- an operator changed auth or networking
- you want a fast answer before debugging app-side behavior

## Bindings

Bindings control who can see a server at all.

Supported binding scopes:

- `organization`
- `team`
- `api_key`
- `user`

If you leave the binding allowlist empty, every tool from that server is eligible to be visible at that scope. If you set a `tool_allowlist`, only those tools are eligible.

### Resolution Model

MCP now follows the same scoped-governance model used by callable targets:

- organization bindings are the ceiling
- team, API key, and user policies can narrow access with `restrict`
- tool allowlists intersect as scope becomes more specific

If an organization has not been migrated yet, DeltaLLM still supports a compatibility fallback. Use the MCP migration report and backfill endpoints to move orgs to the explicit org-ceiling model.

## Tool Policies

Policies apply after the binding step and govern each tool individually.

Available controls:

- **Enabled**: hard on/off switch
- **Approval**: `never` or `manual`
- **Max RPM**: per-tool rate limit
- **Max Concurrency**: max simultaneous executions
- **Result Cache TTL**: cache identical tool results
- **Max Total Execution Time**: fail long-running tool calls

Use policies to keep the server broadly visible but still control the riskiest tools narrowly.

## Approval Requests

If a policy uses manual approval, DeltaLLM creates approval requests for `/mcp tools/call` attempts.

From the UI, operators can:

- inspect the requested tool and arguments
- approve or reject the request
- add a decision comment

Important limitation:

- manual approval is currently part of the direct `/mcp` flow
- chat and responses auto-execution do not support manual-approval tools yet

## Operations View

Each server also exposes an operations summary that helps answer:

- how often the server is being called
- which tools are used most
- how many recent failures happened
- how many approval requests were created, approved, or rejected

This is the fastest place to look before opening raw audit logs.

## Header Forwarding

If the upstream server needs caller-specific headers, add the header names to the allowlist on the server.

Clients then send them with this prefix:

```text
x-deltallm-mcp-<server_key>-<header-name>
```

Example:

```text
x-deltallm-mcp-github-authorization: Bearer ...
```

DeltaLLM strips the prefix and forwards only the allowlisted header names.

## Common Operator Pitfalls

| Problem | What usually fixes it |
| --- | --- |
| Refresh works but the app sees no tools | Add a binding for the caller scope and confirm the tool is not filtered out |
| Health check fails in Docker | Use `host.docker.internal` instead of `localhost` for a host-run MCP server |
| A tool is visible to the master key but not to app keys | Missing binding or overly narrow allowlist/policy |
| Chat requests fail but `/mcp tools/call` works | The chosen model is not handling tool calling reliably enough |

## Related Pages

- [MCP Quick Start](../getting-started/mcp-quickstart.md)
- [MCP Gateway & Tools](../features/mcp.md)
- [API Reference: MCP Gateway & Tooling](../api/mcp.md)
- [Governance Rollout](../deployment/governance.md)
