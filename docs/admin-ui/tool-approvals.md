# Tool approvals

Some MCP tools can require a person to approve each call before DeltaLLM sends it to the external
server. Use Tool Approvals to review those requests.

**Access:** platform administrator or a role with `key.update`. Results remain limited to the
organizations and teams the account is allowed to manage.

## Review a request

1. Open **AI Gateway**, then **Tool Approvals**.
2. Use **Pending** to see requests waiting for a decision.
3. Open a request and check the MCP server, tool name, arguments, caller, and creation time.
4. Approve it only when the action and input are expected. Otherwise, reject it.
5. When rejecting a request, add a short comment when it will help a later review.

The page also lets you filter approved, rejected, and expired requests. Use **Export** when you need
the currently displayed records outside DeltaLLM.

## Important limitation

Manual approval currently applies to direct `/mcp` tool calls. A model cannot pause a chat or
Responses API request and resume it after approval, so those requests reject tools that require
manual approval.

## If no request appears

Check that:

- the MCP tool policy requires manual approval
- the request used the direct `/mcp` tool-call flow
- your account has `key.update` for the request's scope
- the request has not already expired

## Related pages

- [MCP servers](mcp.md)
- [Connect your first MCP server](../getting-started/mcp-quickstart.md)
- [MCP tools reference](../features/mcp.md)
