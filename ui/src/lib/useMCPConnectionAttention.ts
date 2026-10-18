import { useEffect } from 'react';
import { listMCPConnections } from './api/mcpConnectionList';
import { useApi } from './hooks';
import { connectionAttentionCount, MCP_CONNECTIONS_CHANGED_EVENT } from './mcpConnectionAttention';

// Number of connections that the user must connect again, for the user menu
// badge. null while unknown or when the read fails: a failed read is not zero.
export function useMCPConnectionAttention(enabled: boolean): number | null {
  const { data, error, refetch } = useApi(
    (signal) => (enabled ? listMCPConnections(signal, { background: true }) : Promise.resolve(null)),
    [enabled],
  );
  useEffect(() => {
    if (!enabled) return undefined;
    window.addEventListener(MCP_CONNECTIONS_CHANGED_EVENT, refetch);
    return () => window.removeEventListener(MCP_CONNECTIONS_CHANGED_EVENT, refetch);
  }, [enabled, refetch]);
  if (!enabled || error || !data) return null;
  return connectionAttentionCount(data.data);
}
