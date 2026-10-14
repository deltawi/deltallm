# MCP

Use the MCP page to register upstream MCP servers, control who can use them, set how each tool
runs, and decide tool calls that need approval.

To open the page, select **Gateway**, then **MCP**. The page has two tabs:

- **Servers**: register, check, and manage MCP servers.
- **Approvals**: approve or reject tool calls that need manual approval. This tab shows only when
  your account can decide approvals.

The tab is part of the page URL (for example, `/mcp-servers?tab=approvals`). You can bookmark or
share a link to a tab. The old `/mcp-approvals` link opens the **Approvals** tab.

**Access:** `key.read` can expose read views; `org.update` is required for server, binding, and tool-
policy mutations; `key.update` is required for approval decisions. Resource scope is still enforced
by the API. See [Access requirements](access-requirements.md) and the [MCP API](../api/mcp.md).

## What This Page Is For

Use this area when you need to:

- register a new MCP server
- refresh the tool catalog after the upstream server changes
- run a health check before exposing the server to applications
- bind a server to an organization, team, API key, or user
- set policy for a specific tool
- review and decide manual approval requests

## Quick Success Workflow

1. Open **Gateway > MCP**, then the **Servers** tab
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

## Approvals tab

Some MCP tools can require a person to approve each call before DeltaLLM sends it to the external
server. When a tool policy uses manual approval, DeltaLLM creates an approval request for each
direct `/mcp` `tools/call` attempt.

**Access:** platform administrator or a role with `key.update`. Results show only the
organizations and teams that your account can manage.

### How an approval works

- One approval allows one call. After DeltaLLM runs the approved call, the request shows
  **Approved · used**. A new call needs a new approval.
- The caller must send the identical call again after the approval. A call with a different tool
  or different arguments needs a different approval.
- The caller must send the call again within 15 minutes after the approval. If not, the request
  shows **Expired**.
- A pending request expires after 24 hours.
- The person who made the call cannot approve it. That person can reject it. The row shows
  **Your request**, and another operator must approve it.

### Review a request

1. Open **Gateway > MCP**, then the **Approvals** tab. The tab shows the number of pending
   requests.
2. The **Pending** filter shows by default. Use **Approved**, **Rejected**, **Expired**, or **All**
   to see other requests.
3. Select the arrow button at the end of a row to show the details. Examine the MCP server, tool
   name, arguments, caller, and creation time.
4. Approve the request only when you expect the action and the input. If not, reject it.
5. To reject a request, show its details, then select **Reject**. You can add a short comment for
   a later review.

To show the requests of one server only, open the server, select the **Activity** tab, then select
**Open in Approvals**. The **Approvals** tab then shows a server filter. Select the **x** on the
filter to remove it. With a server filter, the line above the table shows the number of pending
requests for that server only. The tab number always shows the pending requests for all servers.

### Export requests

Select **Export** to download the requests of the current filter as a CSV file. The export
contains all pages of the filter, up to 500 requests. If the filter has more than 500 requests, a
message tells you that the file is not complete. Use a narrower filter to export the remaining
requests.

### Server detail Activity tab

The **Activity** tab of a server shows the approval requests of that server. It has the same
status filters, and **Pending** shows by default.

### Important limitation

Manual approval applies to direct `/mcp` tool calls only. A model cannot stop a chat or Responses
API request and continue it after approval. Thus, those requests reject tools that require manual
approval.

### If no request appears

Make sure that:

- the MCP tool policy requires manual approval
- the request used the direct `/mcp` tool-call flow
- your account has `key.update` for the scope of the request
- the request did not expire
- the status filter and the server filter are correct

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

- [Connect your first MCP server](../getting-started/mcp-quickstart.md)
- [MCP Gateway & Tools](../features/mcp.md)
- [API Reference: MCP Gateway & Tooling](../api/mcp.md)
- [Governance Rollout](../deployment/governance.md)
