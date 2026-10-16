import { useEffect, useState } from 'react';
import { Building2, Filter, HeartPulse, Plus, RefreshCw, Search, Server, Wrench } from 'lucide-react';
import ConfirmDialog from '../ConfirmDialog';
import { useToast } from '../ToastProvider';
import MCPServerCreateModal from './MCPServerCreateModal';
import MCPServerTable from './MCPServerTable';
import { mcpServers, type MCPServer } from '../../lib/api';
import { useApi } from '../../lib/hooks';
import { errorMessage } from '../../lib/errorMessage';
import { fmtDuration } from '../../lib/format';

type MCPServersTabProps = {
  canRegister: boolean;
};

export default function MCPServersTab({ canRegister }: MCPServersTabProps) {
  const { pushToast } = useToast();

  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [enabledFilter, setEnabledFilter] = useState<'all' | 'enabled' | 'disabled'>('all');
  const [pageOffset, setPageOffset] = useState(0);
  const pageSize = 20;

  const [createOpen, setCreateOpen] = useState(false);

  const [deleteTarget, setDeleteTarget] = useState<MCPServer | null>(null);
  const [deleting, setDeleting] = useState(false);

  const [refreshingId, setRefreshingId] = useState<string | null>(null);
  const [healthCheckId, setHealthCheckId] = useState<string | null>(null);

  const { data: result, loading, refetch } = useApi(
    () =>
      mcpServers.list({
        search,
        enabled: enabledFilter === 'all' ? undefined : enabledFilter === 'enabled',
        limit: pageSize,
        offset: pageOffset,
      }),
    [search, enabledFilter, pageOffset]
  );

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearch(searchInput.trim());
      setPageOffset(0);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  const servers = result?.data || [];
  const pagination = result?.pagination;

  const healthyCount = servers.filter((server) => server.last_health_status === 'healthy').length;
  const totalTools = servers.reduce((acc, server) => acc + (server.tool_count || 0), 0);
  const sharedCount = servers.filter(
    (server) => server.access && server.access.visibility !== 'private'
  ).length;

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await mcpServers.delete(deleteTarget.mcp_server_id);
      pushToast({ tone: 'success', title: 'Server deleted', message: `"${deleteTarget.server_key}" was removed.` });
      setDeleteTarget(null);
      refetch();
    } catch (error) {
      pushToast({ tone: 'error', title: 'Delete failed', message: errorMessage(error, 'Failed to delete server.') });
    } finally {
      setDeleting(false);
    }
  };

  const handleRefreshTools = async (server: MCPServer) => {
    if (refreshingId || !server.capabilities?.can_operate) return;
    setRefreshingId(server.mcp_server_id);
    try {
      await mcpServers.refreshCapabilities(server.mcp_server_id);
      pushToast({ tone: 'success', title: 'Tools refreshed', message: `"${server.name}" tool list updated.` });
      refetch();
    } catch (error) {
      pushToast({ tone: 'error', title: 'Refresh failed', message: errorMessage(error, 'Could not refresh tools.') });
    } finally {
      setRefreshingId(null);
    }
  };

  const handleHealthCheck = async (server: MCPServer) => {
    if (healthCheckId || !server.capabilities?.can_operate) return;
    setHealthCheckId(server.mcp_server_id);
    try {
      const response = await mcpServers.healthCheck(server.mcp_server_id);
      const { status, latency_ms } = response.health;
      pushToast({
        tone: status === 'healthy' ? 'success' : 'error',
        title: status === 'healthy' ? 'Server is healthy' : 'Server unhealthy',
        message: status === 'healthy' ? `Responded in ${fmtDuration(latency_ms)}.` : response.health.error || 'Health check failed.',
      });
      refetch();
    } catch (error) {
      pushToast({ tone: 'error', title: 'Health check failed', message: errorMessage(error, 'Could not reach server.') });
    } finally {
      setHealthCheckId(null);
    }
  };

  // The API counts only the total. The other figures come from the rows of
  // the current page, so label them when the list has more than one page.
  const pageNote = pagination && pagination.total > servers.length ? 'On this page' : undefined;
  const stats = [
    { label: 'Total Servers', value: pagination ? pagination.total : '—', note: undefined, icon: Server, color: 'text-brand-primary-ink', bg: 'bg-blue-100' },
    { label: 'Healthy', value: result ? healthyCount : '—', note: pageNote, icon: HeartPulse, color: 'text-emerald-600', bg: 'bg-emerald-100' },
    { label: 'Tools Exposed', value: result ? totalTools : '—', note: pageNote, icon: Wrench, color: 'text-brand-secondary-ink', bg: 'bg-violet-100' },
    { label: 'Shared', value: result ? sharedCount : '—', note: pageNote, icon: Building2, color: 'text-amber-600', bg: 'bg-amber-100' },
  ];

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {stats.map((stat) => (
          <div key={stat.label} className="flex items-center gap-4 rounded-xl border border-gray-200/60 bg-white p-5 shadow-sm">
            <div className={`rounded-lg p-3 ${stat.bg} ${stat.color}`}>
              <stat.icon className="h-5 w-5" aria-hidden="true" />
            </div>
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-gray-500">{stat.label}</p>
              <p className="mt-0.5 text-2xl font-semibold">{stat.value}</p>
              {stat.note ? <p className="text-[11px] text-gray-400">{stat.note}</p> : null}
            </div>
          </div>
        ))}
      </div>

      <div className="flex flex-col items-center justify-between gap-3 sm:flex-row">
        <div className="relative w-full sm:w-80">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" aria-hidden="true" />
          <input
            type="text"
            aria-label="Search servers"
            placeholder="Search servers..."
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            className="w-full rounded-lg border border-gray-200 bg-white py-2 pl-9 pr-4 text-sm transition focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-brand-primary/30"
          />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1 rounded-lg border border-gray-200 bg-white p-1">
            <span className="inline-flex items-center gap-1.5 px-2 text-xs font-medium text-gray-500">
              <Filter className="h-3.5 w-3.5" aria-hidden="true" />
              Status
            </span>
            {([
              ['all', 'All'],
              ['enabled', 'Enabled'],
              ['disabled', 'Disabled'],
            ] as const).map(([value, label]) => (
              <button
                key={value}
                type="button"
                aria-pressed={enabledFilter === value}
                onClick={() => {
                  setEnabledFilter(value);
                  setPageOffset(0);
                }}
                className={`rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors ${
                  enabledFilter === value ? 'bg-brand-primary text-brand-on-primary shadow-sm' : 'text-gray-600 hover:bg-gray-50'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={() => refetch()}
            className="rounded-lg p-2 text-gray-400 transition hover:bg-blue-50 hover:text-brand-primary-ink"
            title="Refresh list"
            aria-label="Refresh list"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} aria-hidden="true" />
          </button>
          {canRegister ? (
            <button
              type="button"
              onClick={() => setCreateOpen(true)}
              className="inline-flex items-center gap-2 rounded-lg bg-brand-primary px-4 py-2 text-sm font-medium text-brand-on-primary shadow-sm transition-colors hover:bg-brand-primary-hover"
            >
              <Plus className="h-4 w-4" aria-hidden="true" />
              Register server
            </button>
          ) : null}
        </div>
      </div>

      <MCPServerTable
        servers={servers}
        loading={loading}
        filtered={Boolean(search) || enabledFilter !== 'all'}
        pagination={pagination}
        offset={pageOffset}
        pageSize={pageSize}
        onPageChange={setPageOffset}
        refreshingId={refreshingId}
        healthCheckId={healthCheckId}
        onRefreshTools={(server) => void handleRefreshTools(server)}
        onHealthCheck={(server) => void handleHealthCheck(server)}
        onDelete={setDeleteTarget}
      />

      <MCPServerCreateModal open={createOpen && canRegister} onClose={() => setCreateOpen(false)} />

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete MCP server"
        description={deleteTarget ? `Delete "${deleteTarget.server_key}" and remove all its bindings and policies?` : ''}
        confirmLabel="Delete"
        destructive
        confirming={deleting}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => void handleDelete()}
      />
    </div>
  );
}
