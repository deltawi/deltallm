import { useNavigate } from 'react-router-dom';
import { ChevronLeft, ChevronRight, Globe, HeartPulse, RefreshCw, Trash2 } from 'lucide-react';
import type { MCPServer, Pagination } from '../../lib/api';
import { fmtDuration } from '../../lib/format';

function HealthBadge({ server }: { server: MCPServer }) {
  const status = server.last_health_status;
  const latencyMs = server.last_health_latency_ms;
  const latencyLabel = latencyMs != null ? fmtDuration(latencyMs) : null;

  if (status === 'healthy') {
    return (
      <div className="flex items-center gap-1.5">
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
        </span>
        <span className="text-sm font-medium text-emerald-700">
          Healthy{latencyLabel ? ` · ${latencyLabel}` : ''}
        </span>
      </div>
    );
  }
  if (status === 'unhealthy') {
    return (
      <div className="flex items-center gap-1.5">
        <span className="relative inline-flex h-2 w-2 rounded-full bg-red-500" />
        <span className="text-sm font-medium text-red-600">Unhealthy</span>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-1.5">
      <span className="relative inline-flex h-2 w-2 rounded-full bg-gray-300" />
      <span className="text-sm font-medium text-gray-400">Unchecked</span>
    </div>
  );
}

function ownershipLabel(server: MCPServer) {
  if (server.access) {
    const role = server.access.effective_role || 'shared';
    return `${role.charAt(0).toUpperCase()}${role.slice(1)} · ${server.access.visibility}`;
  }
  return server.owner_scope_type === 'organization'
    ? `Organization · ${server.owner_scope_id || 'Unknown'}`
    : 'Global';
}

function accentColor(server: MCPServer) {
  if (server.last_health_status === 'healthy') return 'bg-emerald-500';
  if (server.last_health_status === 'unhealthy') return 'bg-red-500';
  return 'bg-gray-300';
}

type MCPServerTableProps = {
  servers: MCPServer[];
  loading: boolean;
  /** A search or status filter is active. Changes the empty-state hint. */
  filtered: boolean;
  pagination?: Pagination;
  offset: number;
  pageSize: number;
  onPageChange: (offset: number) => void;
  refreshingId: string | null;
  healthCheckId: string | null;
  onRefreshTools: (server: MCPServer) => void;
  onHealthCheck: (server: MCPServer) => void;
  onDelete: (server: MCPServer) => void;
};

export default function MCPServerTable({
  servers,
  loading,
  filtered,
  pagination,
  offset,
  pageSize,
  onPageChange,
  refreshingId,
  healthCheckId,
  onRefreshTools,
  onHealthCheck,
  onDelete,
}: MCPServerTableProps) {
  const navigate = useNavigate();
  const totalPages = pagination ? Math.max(1, Math.ceil(pagination.total / pageSize)) : 1;
  const currentPage = pagination ? Math.floor(pagination.offset / pageSize) + 1 : 1;

  return (
    <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
      <div className="border-b border-gray-100 px-4 py-3 text-sm text-gray-500">
        Review health, tools, ownership, and scope before drilling into per-server bindings and policies.
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 bg-gray-50">
              <th className="w-64 px-6 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">Server</th>
              <th className="px-6 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">Health</th>
              <th className="px-6 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">Transport</th>
              <th className="w-24 px-6 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">Tools</th>
              <th className="w-24 px-6 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">Status</th>
              <th className="w-36 px-6 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">Last checked</th>
              <th className="w-28 px-6 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading && servers.length === 0 ? (
              <tr>
                <td colSpan={7} className="py-16 text-center text-gray-400">
                  <RefreshCw className="mx-auto mb-2 h-6 w-6 animate-spin text-gray-300" />
                  <p className="text-sm">Loading servers…</p>
                </td>
              </tr>
            ) : null}
            {!loading && servers.length === 0 ? (
              <tr>
                <td colSpan={7} className="py-16 text-center text-gray-400">
                  <Globe className="mx-auto mb-3 h-10 w-10 text-gray-200" />
                  <p className="font-medium text-gray-500">No servers found</p>
                  <p className="mt-1 text-sm">
                    {filtered ? 'Try adjusting your filters.' : 'Register your first MCP server to get started.'}
                  </p>
                </td>
              </tr>
            ) : null}
            {servers.map((server) => {
              const canOperate = Boolean(server.capabilities?.can_operate);
              const canDelete = server.access?.capabilities.delete
                ?? Boolean(server.capabilities?.can_mutate);
              return (
                <tr
                  key={server.mcp_server_id}
                  className="group relative cursor-pointer transition-colors hover:bg-blue-50/30"
                  onClick={() => navigate(`/mcp-servers/${server.mcp_server_id}`)}
                >
                  <td className="relative px-6 py-4">
                    <div className={`absolute bottom-0 left-0 top-0 w-[3px] ${accentColor(server)}`} />
                    <div className="font-medium text-gray-900">{server.name}</div>
                    <div className="mt-0.5 font-mono text-xs text-gray-400">{server.server_key}</div>
                    <div className="mt-1 inline-flex items-center rounded-md border border-gray-200 bg-gray-50 px-2 py-0.5 text-[11px] font-medium text-gray-500">
                      {ownershipLabel(server)}
                    </div>
                  </td>
                  <td className="px-6 py-4">
                    <HealthBadge server={server} />
                  </td>
                  <td className="px-6 py-4">
                    <span className="inline-flex items-center gap-1.5 rounded-md border border-gray-200 bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-600">
                      <Globe className="h-3 w-3" />
                      Streamable HTTP
                    </span>
                  </td>
                  <td className="px-6 py-4">
                    <span className="inline-flex items-center rounded-md border border-gray-200 bg-gray-100 px-2.5 py-1 text-xs font-semibold text-gray-700">
                      {server.tool_count}
                    </span>
                  </td>
                  <td className="px-6 py-4">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                        server.enabled
                          ? 'border border-blue-100 bg-blue-50 text-blue-700'
                          : 'border border-gray-200 bg-gray-100 text-gray-500'
                      }`}
                    >
                      {server.enabled ? 'Enabled' : 'Disabled'}
                    </span>
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-400">
                    {server.last_health_at
                      ? new Date(server.last_health_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                      : 'Never'}
                  </td>
                  <td className="px-6 py-4 text-right">
                    <div className="flex items-center justify-end gap-1 transition-opacity group-hover:opacity-100 sm:opacity-0">
                      {canOperate ? (
                        <>
                          <button
                            type="button"
                            title="Refresh tools"
                            disabled={refreshingId === server.mcp_server_id}
                            onClick={(event) => {
                              event.stopPropagation();
                              onRefreshTools(server);
                            }}
                            className="rounded-md p-1.5 text-gray-400 transition-colors hover:bg-blue-50 hover:text-brand-primary-ink disabled:opacity-50"
                          >
                            <RefreshCw className={`h-3.5 w-3.5 ${refreshingId === server.mcp_server_id ? 'animate-spin' : ''}`} />
                          </button>
                          <button
                            type="button"
                            title="Check health"
                            disabled={healthCheckId === server.mcp_server_id}
                            onClick={(event) => {
                              event.stopPropagation();
                              onHealthCheck(server);
                            }}
                            className="rounded-md p-1.5 text-gray-400 transition-colors hover:bg-emerald-50 hover:text-emerald-600 disabled:opacity-50"
                          >
                            <HeartPulse className={`h-3.5 w-3.5 ${healthCheckId === server.mcp_server_id ? 'animate-pulse' : ''}`} />
                          </button>
                        </>
                      ) : null}
                      {canDelete ? (
                        <button
                          type="button"
                          title="Delete server"
                          onClick={(event) => {
                            event.stopPropagation();
                            onDelete(server);
                          }}
                          className="rounded-md p-1.5 text-gray-400 transition-colors hover:bg-red-50 hover:text-red-600"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      ) : null}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between border-t border-gray-100 bg-gray-50/50 px-6 py-4">
        <span className="text-sm text-gray-500">
          {pagination ? (
            <>
              Showing <span className="font-medium text-gray-900">{pagination.offset + 1}</span> –{' '}
              <span className="font-medium text-gray-900">{Math.min(pagination.offset + pageSize, pagination.total)}</span> of{' '}
              <span className="font-medium text-gray-900">{pagination.total}</span> servers
            </>
          ) : (
            <span className="font-medium text-gray-900">{servers.length}</span>
          )}
        </span>
        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={offset === 0}
            aria-label="Previous page"
            onClick={() => onPageChange(Math.max(0, offset - pageSize))}
            className="rounded-md border border-gray-200 bg-white p-1.5 text-gray-600 transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span className="tabular-nums text-xs text-gray-500">
            {currentPage} / {totalPages}
          </span>
          <button
            type="button"
            disabled={!pagination?.has_more}
            aria-label="Next page"
            onClick={() => onPageChange(offset + pageSize)}
            className="rounded-md border border-gray-200 bg-white p-1.5 text-gray-600 transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
