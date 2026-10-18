import type { MCPConnectionRow } from './api/mcpOAuth';

// Small helpers that the app shell (user menu badge) needs. They stay apart
// from mcpConnections.ts so that the initial bundle does not grow.

export const CONNECTED_ACCOUNTS_PATH = '/connected-accounts';

// The user menu badge counts connections that stopped working. A server the
// user never connected is not an error, so it does not count.
export function connectionAttentionCount(rows: readonly Pick<MCPConnectionRow, 'status'>[]): number {
  return rows.filter((row) => row.status === 'needs_reconnect').length;
}

// The Connected accounts page fires this event after a change, so the user
// menu badge reads the list again.
export const MCP_CONNECTIONS_CHANGED_EVENT = 'deltallm:mcp-connections-changed';

export function notifyMCPConnectionsChanged(): void {
  if (typeof window !== 'undefined') window.dispatchEvent(new Event(MCP_CONNECTIONS_CHANGED_EVENT));
}
