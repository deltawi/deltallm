import { apiFetch } from './transport';
import { backgroundRequestHeaders } from '../sessionActivity';
import type { MCPConnectionList } from './mcpOAuth';

// The user menu badge loads this read with the app shell, so it lives apart
// from the other MCP OAuth endpoints, which load with their pages.
export function listMCPConnections(signal?: AbortSignal, options?: { background?: boolean }) {
  return apiFetch<MCPConnectionList>('/ui/api/mcp-connections', {
    signal,
    // The badge reads this in the background; it must not extend the session.
    headers: options?.background ? backgroundRequestHeaders() : undefined,
  });
}
