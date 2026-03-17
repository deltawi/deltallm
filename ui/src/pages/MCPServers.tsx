import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Globe,
  RefreshCw,
  HeartPulse,
  Trash2,
  Search,
  Filter,
  Plus,
  Server,
  Wrench,
  ChevronLeft,
  ChevronRight,
  Building2,
} from 'lucide-react';
import ConfirmDialog from '../components/ConfirmDialog';
import Modal from '../components/Modal';
import MCPServerForm, {
  buildMCPServerPayload,
  EMPTY_MCP_SERVER_FORM,
  type MCPServerFormValues,
} from '../components/mcp/MCPServerForm';
import { mcpServers, type MCPServer } from '../lib/api';
import { useAuth } from '../lib/auth';
import { useApi } from '../lib/hooks';
import { useToast } from '../components/ToastProvider';
import { IndexShell } from '../components/admin/shells';

function HealthBadge({ server }: { server: MCPServer }) {
  const status = server.last_health_status;
  const latencyMs = server.last_health_latency_ms;
  const latencyLabel = latencyMs != null ? `${latencyMs}ms` : null;

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
  return server.owner_scope_type === 'organization'
    ? `Organization · ${server.owner_scope_id || 'Unknown'}`
    : 'Global';
}

export default function MCPServers() {
  const navigate = useNavigate();
  const { session, authMode } = useAuth();
  const { pushToast } = useToast();

  const userRole = session?.role || (authMode === 'master_key' ? 'platform_admin' : '');
  const isPlatformAdmin = userRole === 'platform_admin';
  const permissions = new Set(session?.effective_permissions || []);
  const orgMemberships = (session?.organization_memberships || [])
    .map((membership) => ({
      organization_id: String(membership.organization_id || ''),
      role: String(membership.role || ''),
    }))
    .filter((membership) => membership.organization_id);
  const ownerScopeOptions = useMemo(
    () => orgMemberships.map((membership) => ({ value: membership.organization_id, label: membership.organization_id })),
    [orgMemberships]
  );
  const canManageMcp = isPlatformAdmin || permissions.has('org.update');
  const isOrgScopedOnly = canManageMcp && !isPlatformAdmin;

  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [enabledFilter, setEnabledFilter] = useState<'all' | 'enabled' | 'disabled'>('all');
  const [pageOffset, setPageOffset] = useState(0);
  const pageSize = 20;

  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<MCPServerFormValues>({ ...EMPTY_MCP_SERVER_FORM });

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

  useEffect(() => {
    if (!createOpen || !isOrgScopedOnly) {
      return;
    }
    setForm((current) => ({
      ...current,
      owner_scope_type: 'organization',
      owner_scope_id: current.owner_scope_id || ownerScopeOptions[0]?.value || '',
    }));
  }, [createOpen, isOrgScopedOnly, ownerScopeOptions]);

  const servers = result?.data || [];
  const pagination = result?.pagination;
  const totalPages = pagination ? Math.max(1, Math.ceil(pagination.total / pageSize)) : 1;
  const currentPage = pagination ? Math.floor(pagination.offset / pageSize) + 1 : 1;

  const healthyCount = servers.filter((server) => server.last_health_status === 'healthy').length;
  const totalTools = servers.reduce((acc, server) => acc + (server.tool_count || 0), 0);
  const orgOwnedCount = servers.filter((server) => server.owner_scope_type === 'organization').length;

  const handleCreate = async () => {
    try {
      const created = await mcpServers.create(buildMCPServerPayload(form));
      setCreateOpen(false);
      setForm({ ...EMPTY_MCP_SERVER_FORM });
      pushToast({
        tone: 'success',
        title: 'MCP server created',
        message: `Server "${created.server_key}" is ready for capability refresh.`,
      });
      navigate(`/mcp-servers/${created.mcp_server_id}`);
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Create failed', message: error?.message || 'Failed to create MCP server.' });
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await mcpServers.delete(deleteTarget.mcp_server_id);
      pushToast({ tone: 'success', title: 'Server deleted', message: `"${deleteTarget.server_key}" was removed.` });
      setDeleteTarget(null);
      refetch();
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Delete failed', message: error?.message || 'Failed to delete server.' });
    } finally {
      setDeleting(false);
    }
  };

  const handleRefreshTools = async (server: MCPServer, event: React.MouseEvent) => {
    event.stopPropagation();
    if (refreshingId || !server.capabilities?.can_operate) return;
    setRefreshingId(server.mcp_server_id);
    try {
      await mcpServers.refreshCapabilities(server.mcp_server_id);
      pushToast({ tone: 'success', title: 'Tools refreshed', message: `"${server.name}" tool list updated.` });
      refetch();
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Refresh failed', message: error?.message || 'Could not refresh tools.' });
    } finally {
      setRefreshingId(null);
    }
  };

  const handleHealthCheck = async (server: MCPServer, event: React.MouseEvent) => {
    event.stopPropagation();
    if (healthCheckId || !server.capabilities?.can_operate) return;
    setHealthCheckId(server.mcp_server_id);
    try {
      const response = await mcpServers.healthCheck(server.mcp_server_id);
      const { status, latency_ms } = response.health;
      pushToast({
        tone: status === 'healthy' ? 'success' : 'error',
        title: status === 'healthy' ? 'Server is healthy' : 'Server unhealthy',
        message: status === 'healthy' ? `Responded in ${latency_ms}ms.` : response.health.error || 'Health check failed.',
      });
      refetch();
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Health check failed', message: error?.message || 'Could not reach server.' });
    } finally {
      setHealthCheckId(null);
    }
  };

  const stats = [
    { label: 'Total Servers', value: pagination?.total ?? servers.length, icon: Server, color: 'text-blue-600', bg: 'bg-blue-100' },
    { label: 'Healthy', value: healthyCount, icon: HeartPulse, color: 'text-emerald-600', bg: 'bg-emerald-100' },
    { label: 'Tools Exposed', value: totalTools, icon: Wrench, color: 'text-violet-600', bg: 'bg-violet-100' },
    { label: 'Org-Owned', value: orgOwnedCount, icon: Building2, color: 'text-amber-600', bg: 'bg-amber-100' },
  ];

  const accentColor = (server: MCPServer) => {
    if (server.last_health_status === 'healthy') return 'bg-emerald-500';
    if (server.last_health_status === 'unhealthy') return 'bg-red-500';
    return 'bg-gray-300';
  };

  return (
    <IndexShell
      title="MCP Servers"
      count={pagination?.total ?? null}
      description="Connect and manage external tool servers for your AI models."
      action={canManageMcp ? (
        <button
          type="button"
          onClick={() => setCreateOpen(true)}
          className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-blue-700"
        >
          <Plus className="h-4 w-4" />
          Register server
        </button>
      ) : null}
      notice={!canManageMcp ? (
        <div className="rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-sm text-blue-900">
          Read-only. MCP registration requires organization or platform admin permissions.
        </div>
      ) : null}
      summary={(
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {stats.map((stat) => (
            <div key={stat.label} className="flex items-center gap-4 rounded-xl border border-gray-200/60 bg-white p-5 shadow-sm">
              <div className={`rounded-lg p-3 ${stat.bg} ${stat.color}`}>
                <stat.icon className="h-5 w-5" />
              </div>
              <div>
                <p className="text-xs font-medium uppercase tracking-wide text-gray-500">{stat.label}</p>
                <p className="mt-0.5 text-2xl font-semibold">{stat.value}</p>
              </div>
            </div>
          ))}
        </div>
      )}
      toolbar={(
        <div className="flex flex-col items-center justify-between gap-3 sm:flex-row">
          <div className="relative w-full sm:w-80">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
            <input
              type="text"
              placeholder="Search servers..."
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              className="w-full rounded-lg border border-gray-200 bg-gray-50 py-2 pl-9 pr-4 text-sm transition focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-500/30"
            />
          </div>
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1 rounded-lg border border-gray-200 bg-white p-1">
              <span className="inline-flex items-center gap-1.5 px-2 text-xs font-medium text-gray-500">
                <Filter className="h-3.5 w-3.5" />
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
                  onClick={() => {
                    setEnabledFilter(value);
                    setPageOffset(0);
                  }}
                  className={`rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors ${
                    enabledFilter === value ? 'bg-blue-600 text-white shadow-sm' : 'text-gray-600 hover:bg-gray-50'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
            <button
              type="button"
              onClick={() => refetch()}
              className="rounded-lg p-2 text-gray-400 transition hover:bg-blue-50 hover:text-blue-600"
              title="Refresh list"
            >
              <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>
      )}
    >
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
                        {search || enabledFilter !== 'all' ? 'Try adjusting your filters.' : 'Register your first MCP server to get started.'}
                      </p>
                    </td>
                  </tr>
                ) : null}
                {servers.map((server) => {
                  const canOperate = Boolean(server.capabilities?.can_operate);
                  const canMutate = Boolean(server.capabilities?.can_mutate);
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
                                onClick={(event) => void handleRefreshTools(server, event)}
                                className="rounded-md p-1.5 text-gray-400 transition-colors hover:bg-blue-50 hover:text-blue-600 disabled:opacity-50"
                              >
                                <RefreshCw className={`h-3.5 w-3.5 ${refreshingId === server.mcp_server_id ? 'animate-spin' : ''}`} />
                              </button>
                              <button
                                type="button"
                                title="Check health"
                                disabled={healthCheckId === server.mcp_server_id}
                                onClick={(event) => void handleHealthCheck(server, event)}
                                className="rounded-md p-1.5 text-gray-400 transition-colors hover:bg-emerald-50 hover:text-emerald-600 disabled:opacity-50"
                              >
                                <HeartPulse className={`h-3.5 w-3.5 ${healthCheckId === server.mcp_server_id ? 'animate-pulse' : ''}`} />
                              </button>
                            </>
                          ) : null}
                          {canMutate ? (
                            <button
                              type="button"
                              title="Delete server"
                              onClick={(event) => {
                                event.stopPropagation();
                                setDeleteTarget(server);
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
                disabled={pageOffset === 0}
                onClick={() => setPageOffset(Math.max(0, pageOffset - pageSize))}
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
                onClick={() => setPageOffset(pageOffset + pageSize)}
                className="rounded-md border border-gray-200 bg-white p-1.5 text-gray-600 transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>
          </div>
      </div>

      <Modal open={createOpen && canManageMcp} onClose={() => setCreateOpen(false)} title="Register MCP Server" wide>
        <div className="space-y-5">
          <div className="rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-900">
            <div className="font-semibold">What happens next</div>
            <div className="mt-1 text-blue-800">
              This registers the server record only. On the detail page you can run health checks, refresh tool discovery,
              and add bindings or tool policies.
            </div>
          </div>
          <MCPServerForm
            value={form}
            onChange={setForm}
            ownerScopeOptions={ownerScopeOptions}
            lockOwnerScopeType={isOrgScopedOnly}
          />
          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={() => {
                setCreateOpen(false);
                setForm({ ...EMPTY_MCP_SERVER_FORM });
              }}
              className="rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => {
                setCreating(true);
                void handleCreate();
              }}
              disabled={creating}
              className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {creating ? 'Creating…' : 'Create Server'}
            </button>
          </div>
        </div>
      </Modal>

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
    </IndexShell>
  );
}
