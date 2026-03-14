import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Activity, Plus, Trash2 } from 'lucide-react';
import Card from '../components/Card';
import ConfirmDialog from '../components/ConfirmDialog';
import DataTable from '../components/DataTable';
import Modal from '../components/Modal';
import MCPServerForm, { buildMCPServerPayload, EMPTY_MCP_SERVER_FORM, type MCPServerFormValues } from '../components/mcp/MCPServerForm';
import { mcpServers, type MCPServer } from '../lib/api';
import { useAuth } from '../lib/auth';
import { useApi } from '../lib/hooks';
import { useToast } from '../components/ToastProvider';

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
  const [pageOffset, setPageOffset] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<MCPServer | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [form, setForm] = useState<MCPServerFormValues>({ ...EMPTY_MCP_SERVER_FORM });
  const pageSize = 10;

  const { data: result, loading, refetch } = useApi(
    () => mcpServers.list({ search, limit: pageSize, offset: pageOffset }),
    [search, pageOffset]
  );
  const showDeleteColumn = Boolean((result?.data || []).some((row) => row.capabilities?.can_mutate));

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

  const handleCreate = async () => {
    try {
      const created = await mcpServers.create(buildMCPServerPayload(form));
      setCreateOpen(false);
      setForm({ ...EMPTY_MCP_SERVER_FORM });
      pushToast({ tone: 'success', title: 'MCP server created', message: `Server "${created.server_key}" is ready for capability refresh.` });
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
      pushToast({ tone: 'success', title: 'MCP server deleted', message: `Server "${deleteTarget.server_key}" was deleted.` });
      setDeleteTarget(null);
      refetch();
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Delete failed', message: error?.message || 'Failed to delete MCP server.' });
    } finally {
      setDeleting(false);
    }
  };

  const columns = [
    {
      key: 'server',
      header: 'Server',
      render: (row: MCPServer) => (
        <div>
          <div className="font-medium text-gray-900">{row.name}</div>
          <div className="mt-1 text-xs text-gray-500">
            <code className="rounded bg-gray-100 px-1.5 py-0.5">{row.server_key}</code>
          </div>
        </div>
      ),
    },
    { key: 'transport', header: 'Transport', render: () => <span className="text-xs text-gray-600">Streamable HTTP</span> },
    {
      key: 'ownership',
      header: 'Ownership',
      render: (row: MCPServer) => (
        <span className="text-xs text-gray-600">
          {row.owner_scope_type === 'organization' ? `Org: ${row.owner_scope_id}` : 'Global'}
        </span>
      ),
    },
    { key: 'tool_count', header: 'Tools' },
    {
      key: 'health',
      header: 'Health',
      render: (row: MCPServer) => (
        <span
          className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
            row.last_health_status === 'healthy'
              ? 'bg-green-100 text-green-700'
              : row.last_health_status === 'unhealthy'
                ? 'bg-red-100 text-red-700'
                : 'bg-gray-100 text-gray-700'
          }`}
        >
          {row.last_health_status || 'Unchecked'}
        </span>
      ),
    },
    {
      key: 'enabled',
      header: 'Status',
      render: (row: MCPServer) => (
        <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${row.enabled ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-700'}`}>
          {row.enabled ? 'Enabled' : 'Disabled'}
        </span>
      ),
    },
    ...(showDeleteColumn
      ? [
          {
            key: 'actions',
            header: '',
            render: (row: MCPServer) => (
              <div className="flex justify-end" onClick={(event) => event.stopPropagation()}>
                {row.capabilities?.can_mutate ? (
                  <button
                    type="button"
                    onClick={() => setDeleteTarget(row)}
                    className="rounded-lg p-1.5 hover:bg-red-50"
                    title="Delete server"
                  >
                    <Trash2 className="h-4 w-4 text-red-500" />
                  </button>
                ) : null}
              </div>
            ),
          },
        ]
      : []),
  ];

  return (
    <div className="p-4 sm:p-6">
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">MCP Servers</h1>
          <p className="mt-1 text-sm text-gray-500">Register remote MCP servers, refresh tool discovery, and scope access before wiring them into runtime flows.</p>
        </div>
        {canManageMcp ? (
          <button
            type="button"
            onClick={() => setCreateOpen(true)}
            className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
          >
            <Plus className="h-4 w-4" />
            Add MCP Server
          </button>
        ) : (
          <div className="rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-sm text-blue-900">
            Read-only access. MCP server registration requires organization or platform admin permissions.
          </div>
        )}
      </div>

      <Card className="mb-5 border-blue-100 bg-gradient-to-br from-blue-50 via-white to-slate-50">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <div className="inline-flex items-center gap-2 rounded-full bg-white px-3 py-1 text-xs font-medium text-blue-700 shadow-sm ring-1 ring-blue-100">
              <Activity className="h-3.5 w-3.5" />
              Phase 1 workflow
            </div>
            <p className="mt-3 text-sm text-slate-700">Create the server first, refresh capabilities second, then add scope bindings and per-tool policies from the detail page.</p>
          </div>
          <div className="grid gap-2 text-sm text-slate-700 sm:grid-cols-3">
            <div className="rounded-xl border border-slate-200 bg-white px-3 py-3"><div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Step 1</div><div className="mt-1 font-medium">Register server</div></div>
            <div className="rounded-xl border border-slate-200 bg-white px-3 py-3"><div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Step 2</div><div className="mt-1 font-medium">Refresh tools</div></div>
            <div className="rounded-xl border border-slate-200 bg-white px-3 py-3"><div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Step 3</div><div className="mt-1 font-medium">Bind access</div></div>
          </div>
        </div>
      </Card>

      <Card>
        <div className="px-4 pt-3 pb-2">
          <input
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            placeholder="Search MCP servers..."
            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 sm:w-80"
          />
        </div>
        <DataTable
          columns={columns}
          data={result?.data || []}
          loading={loading}
          emptyMessage="No MCP servers found"
          pagination={result?.pagination}
          onPageChange={setPageOffset}
          onRowClick={(row: MCPServer) => navigate(`/mcp-servers/${row.mcp_server_id}`)}
        />
      </Card>

      <Modal open={createOpen && canManageMcp} onClose={() => setCreateOpen(false)} title="Add MCP Server" wide>
        <div className="space-y-5">
          <div className="rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-900">
            <div className="font-semibold">What happens next</div>
            <div className="mt-1 text-blue-800">This registers the server record only. On the detail page you can run health checks, refresh tool discovery, and add bindings or tool policies.</div>
          </div>
          <MCPServerForm
            value={form}
            onChange={setForm}
            ownerScopeOptions={ownerScopeOptions}
            lockOwnerScopeType={isOrgScopedOnly}
          />
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setCreateOpen(false)} className="rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50">
              Cancel
            </button>
            <button
              type="button"
              onClick={() => {
                setCreating(true);
                void handleCreate();
              }}
              disabled={creating}
              className="rounded-lg bg-blue-600 px-3 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {creating ? 'Creating...' : 'Create Server'}
            </button>
          </div>
        </div>
      </Modal>

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete MCP server"
        description={deleteTarget ? `Delete "${deleteTarget.server_key}" and remove its bindings and policies?` : ''}
        confirmLabel="Delete"
        destructive
        confirming={deleting}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => void handleDelete()}
      />
    </div>
  );
}
