import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, HeartPulse, RefreshCw, Save, Trash2 } from 'lucide-react';
import Card from '../components/Card';
import ConfirmDialog from '../components/ConfirmDialog';
import DataTable from '../components/DataTable';
import MCPServerForm, { buildMCPServerPayload, formFromMCPServer, type MCPServerFormValues } from '../components/mcp/MCPServerForm';
import {
  type MCPApprovalRequest,
  mcpServers,
  type MCPBinding,
  type MCPServerDetail,
  type MCPServerOperations,
  type MCPToolPolicy,
} from '../lib/api';
import { useAuth } from '../lib/auth';
import { useApi } from '../lib/hooks';
import { useToast } from '../components/ToastProvider';

type BindingFormState = {
  scope_type: 'organization' | 'team' | 'api_key';
  scope_id: string;
  tool_allowlist: string;
  enabled: boolean;
};

type PolicyFormState = {
  tool_name: string;
  scope_type: 'organization' | 'team' | 'api_key';
  scope_id: string;
  require_approval: 'never' | 'manual';
  max_rpm: string;
  max_concurrency: string;
  max_total_execution_time_ms: string;
  result_cache_ttl_seconds: string;
  enabled: boolean;
};

const EMPTY_BINDING_FORM: BindingFormState = {
  scope_type: 'team',
  scope_id: '',
  tool_allowlist: '',
  enabled: true,
};

const EMPTY_POLICY_FORM: PolicyFormState = {
  tool_name: '',
  scope_type: 'team',
  scope_id: '',
  require_approval: 'never',
  max_rpm: '',
  max_concurrency: '',
  max_total_execution_time_ms: '',
  result_cache_ttl_seconds: '',
  enabled: true,
};

function fmtDate(value?: string | null) {
  if (!value) return '—';
  return new Date(value).toLocaleString();
}

function fmtPct(value: number) {
  return `${(value * 100).toFixed(1)}%`;
}

export default function MCPServerDetail() {
  const { serverId = '' } = useParams();
  const navigate = useNavigate();
  const { session, authMode } = useAuth();
  const { pushToast } = useToast();
  const userRole = session?.role || (authMode === 'master_key' ? 'platform_admin' : '');
  const isPlatformAdmin = userRole === 'platform_admin';
  const permissions = new Set(session?.effective_permissions || []);
  const orgIds = (session?.organization_memberships || [])
    .map((membership) => String(membership.organization_id || ''))
    .filter(Boolean);
  const ownerScopeOptions = orgIds.map((organizationId) => ({ value: organizationId, label: organizationId }));
  const canUpdateMcp = isPlatformAdmin || permissions.has('key.update');
  const [form, setForm] = useState<MCPServerFormValues | null>(null);
  const [bindingForm, setBindingForm] = useState<BindingFormState>(EMPTY_BINDING_FORM);
  const [policyForm, setPolicyForm] = useState<PolicyFormState>(EMPTY_POLICY_FORM);
  const [saving, setSaving] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [checkingHealth, setCheckingHealth] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [savingBinding, setSavingBinding] = useState(false);
  const [savingPolicy, setSavingPolicy] = useState(false);

  const { data, loading, refetch } = useApi<MCPServerDetail | null>(
    () => (serverId ? mcpServers.get(serverId) : Promise.resolve(null)),
    [serverId]
  );
  const { data: operations, loading: operationsLoading, refetch: refetchOperations } = useApi<MCPServerOperations | null>(
    () => (serverId ? mcpServers.operations(serverId) : Promise.resolve(null)),
    [serverId]
  );
  const { data: approvalsData, loading: approvalsLoading, refetch: refetchApprovals } = useApi<{ data: MCPApprovalRequest[]; pagination: any } | null>(
    () => (serverId && canUpdateMcp ? mcpServers.listApprovalRequests({ server_id: serverId, limit: 10, offset: 0 }) : Promise.resolve(null)),
    [serverId, canUpdateMcp]
  );

  useEffect(() => {
    if (data?.server) {
      setForm(formFromMCPServer(data.server));
    }
  }, [data?.server]);

  const currentServer = data?.server;
  const canMutateServer = Boolean(currentServer?.capabilities?.can_mutate);
  const canOperateServer = Boolean(currentServer?.capabilities?.can_operate);
  const canManageScopeConfig = Boolean(currentServer?.capabilities?.can_manage_scope_config);

  const toolOptions = useMemo(() => (data?.tools || []).map((tool) => tool.original_name), [data?.tools]);

  const handleSave = async () => {
    if (!form || !serverId) return;
    setSaving(true);
    try {
      await mcpServers.update(serverId, buildMCPServerPayload(form));
      pushToast({ tone: 'success', title: 'Server updated', message: 'MCP server settings were saved.' });
      refetch();
      refetchOperations();
      refetchApprovals();
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Save failed', message: error?.message || 'Failed to save MCP server.' });
    } finally {
      setSaving(false);
    }
  };

  const handleRefreshCapabilities = async () => {
    if (!serverId) return;
    setRefreshing(true);
    try {
      await mcpServers.refreshCapabilities(serverId);
      pushToast({ tone: 'success', title: 'Capabilities refreshed', message: 'Discovered tools were updated from the remote MCP server.' });
      refetch();
      refetchOperations();
      refetchApprovals();
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Refresh failed', message: error?.message || 'Failed to refresh MCP tools.' });
    } finally {
      setRefreshing(false);
    }
  };

  const handleHealthCheck = async () => {
    if (!serverId) return;
    setCheckingHealth(true);
    try {
      const result = await mcpServers.healthCheck(serverId);
      pushToast({
        tone: result.health.status === 'healthy' ? 'success' : 'error',
        title: 'Health check completed',
        message: result.health.status === 'healthy'
          ? `Server responded in ${result.health.latency_ms} ms.`
          : (result.health.error || 'The MCP server is unhealthy.'),
      });
      refetch();
      refetchOperations();
      refetchApprovals();
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Health check failed', message: error?.message || 'Failed to run health check.' });
    } finally {
      setCheckingHealth(false);
    }
  };

  const handleDelete = async () => {
    if (!serverId || !data?.server) return;
    setDeleting(true);
    try {
      await mcpServers.delete(serverId);
      pushToast({ tone: 'success', title: 'Server deleted', message: `MCP server "${data.server.server_key}" was deleted.` });
      navigate('/mcp-servers');
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Delete failed', message: error?.message || 'Failed to delete MCP server.' });
    } finally {
      setDeleting(false);
    }
  };

  const handleSaveBinding = async () => {
    if (!serverId || !data?.server) return;
    setSavingBinding(true);
    try {
      await mcpServers.upsertBinding({
        server_id: data.server.mcp_server_id,
        scope_type: bindingForm.scope_type,
        scope_id: bindingForm.scope_id.trim(),
        enabled: bindingForm.enabled,
        tool_allowlist: bindingForm.tool_allowlist.split(',').map((item) => item.trim()).filter(Boolean),
      });
      setBindingForm(EMPTY_BINDING_FORM);
      pushToast({ tone: 'success', title: 'Binding saved', message: 'Scope access was updated for this MCP server.' });
      refetch();
      refetchOperations();
      refetchApprovals();
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Binding failed', message: error?.message || 'Failed to save binding.' });
    } finally {
      setSavingBinding(false);
    }
  };

  const handleDeleteBinding = async (binding: MCPBinding) => {
    try {
      await mcpServers.deleteBinding(binding.mcp_binding_id);
      pushToast({ tone: 'success', title: 'Binding removed', message: `${binding.scope_type}:${binding.scope_id} no longer has access.` });
      refetch();
      refetchOperations();
      refetchApprovals();
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Delete failed', message: error?.message || 'Failed to delete binding.' });
    }
  };

  const handleSavePolicy = async () => {
    if (!serverId || !data?.server) return;
    setSavingPolicy(true);
    try {
      await mcpServers.upsertToolPolicy({
        server_id: data.server.mcp_server_id,
        tool_name: policyForm.tool_name.trim(),
        scope_type: policyForm.scope_type,
        scope_id: policyForm.scope_id.trim(),
        enabled: policyForm.enabled,
        require_approval: policyForm.require_approval,
        max_rpm: policyForm.max_rpm ? Number(policyForm.max_rpm) : null,
        max_concurrency: policyForm.max_concurrency ? Number(policyForm.max_concurrency) : null,
        result_cache_ttl_seconds: policyForm.result_cache_ttl_seconds ? Number(policyForm.result_cache_ttl_seconds) : null,
        metadata: policyForm.max_total_execution_time_ms
          ? { max_total_mcp_execution_time_ms: Number(policyForm.max_total_execution_time_ms) }
          : null,
      });
      setPolicyForm(EMPTY_POLICY_FORM);
      pushToast({ tone: 'success', title: 'Policy saved', message: 'Tool policy was updated.' });
      refetch();
      refetchOperations();
      refetchApprovals();
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Policy failed', message: error?.message || 'Failed to save tool policy.' });
    } finally {
      setSavingPolicy(false);
    }
  };

  const handleDeletePolicy = async (policy: MCPToolPolicy) => {
    try {
      await mcpServers.deleteToolPolicy(policy.mcp_tool_policy_id);
      pushToast({ tone: 'success', title: 'Policy removed', message: `Policy for ${policy.tool_name} was deleted.` });
      refetch();
      refetchOperations();
      refetchApprovals();
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Delete failed', message: error?.message || 'Failed to delete tool policy.' });
    }
  };

  const handleApprovalDecision = async (approval: MCPApprovalRequest, decision: 'approved' | 'rejected') => {
    try {
      await mcpServers.decideApprovalRequest(approval.mcp_approval_request_id, { status: decision });
      pushToast({
        tone: 'success',
        title: decision === 'approved' ? 'Approval granted' : 'Approval rejected',
        message: `${approval.tool_name} request was ${decision}.`,
      });
      refetchApprovals();
      refetchOperations();
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Decision failed', message: error?.message || 'Failed to update approval request.' });
    }
  };

  const bindingColumns = [
    { key: 'scope_type', header: 'Scope', render: (row: MCPBinding) => `${row.scope_type}:${row.scope_id}` },
    { key: 'tool_allowlist', header: 'Allowed Tools', render: (row: MCPBinding) => (row.tool_allowlist?.length ? row.tool_allowlist.join(', ') : 'All tools') },
    { key: 'enabled', header: 'Status', render: (row: MCPBinding) => (row.enabled ? 'Enabled' : 'Disabled') },
    ...(canManageScopeConfig
      ? [{
          key: 'actions',
          header: '',
          render: (row: MCPBinding) => (
            <button type="button" onClick={(event) => { event.stopPropagation(); void handleDeleteBinding(row); }} className="rounded-lg p-1.5 hover:bg-red-50" title="Delete binding">
              <Trash2 className="h-4 w-4 text-red-500" />
            </button>
          ),
        }]
      : []),
  ];

  const policyColumns = [
    { key: 'tool_name', header: 'Tool' },
    { key: 'scope_type', header: 'Scope', render: (row: MCPToolPolicy) => `${row.scope_type}:${row.scope_id}` },
    { key: 'require_approval', header: 'Approval', render: (row: MCPToolPolicy) => row.require_approval ?? '—' },
    { key: 'max_rpm', header: 'Max RPM', render: (row: MCPToolPolicy) => row.max_rpm ?? '—' },
    { key: 'max_concurrency', header: 'Max Concurrency', render: (row: MCPToolPolicy) => row.max_concurrency ?? '—' },
    {
      key: 'max_total_execution_time_ms',
      header: 'Max Exec Time',
      render: (row: MCPToolPolicy) => {
        const rawValue = row.metadata?.max_total_mcp_execution_time_ms;
        return typeof rawValue === 'number' ? `${rawValue} ms` : '—';
      },
    },
    { key: 'result_cache_ttl_seconds', header: 'Cache TTL', render: (row: MCPToolPolicy) => row.result_cache_ttl_seconds ?? '—' },
    ...(canManageScopeConfig
      ? [{
          key: 'actions',
          header: '',
          render: (row: MCPToolPolicy) => (
            <button type="button" onClick={(event) => { event.stopPropagation(); void handleDeletePolicy(row); }} className="rounded-lg p-1.5 hover:bg-red-50" title="Delete policy">
              <Trash2 className="h-4 w-4 text-red-500" />
            </button>
          ),
        }]
      : []),
  ];

  const toolColumns = [
    { key: 'namespaced_name', header: 'Namespaced Tool' },
    { key: 'original_name', header: 'Upstream Tool' },
    { key: 'description', header: 'Description', render: (row: any) => row.description || '—' },
  ];
  const operationToolColumns = [
    { key: 'tool_name', header: 'Tool' },
    { key: 'total_calls', header: 'Calls' },
    { key: 'failed_calls', header: 'Failures' },
    { key: 'avg_latency_ms', header: 'Avg Latency', render: (row: any) => `${Math.round(Number(row.avg_latency_ms || 0))} ms` },
  ];
  const failureColumns = [
    { key: 'occurred_at', header: 'Time', render: (row: any) => fmtDate(row.occurred_at) },
    { key: 'tool_name', header: 'Tool' },
    {
      key: 'error_type',
      header: 'Error',
      render: (row: any) => row.error_code ? `${row.error_type || 'Error'} (${row.error_code})` : (row.error_type || 'Error'),
    },
  ];
  const approvalColumns = [
    { key: 'created_at', header: 'Requested', render: (row: MCPApprovalRequest) => fmtDate(row.created_at) },
    { key: 'tool_name', header: 'Tool' },
    { key: 'scope_type', header: 'Scope', render: (row: MCPApprovalRequest) => `${row.scope_type}:${row.scope_id}` },
    { key: 'status', header: 'Status' },
    ...(canUpdateMcp
      ? [{
          key: 'actions',
          header: '',
          render: (row: MCPApprovalRequest) =>
            row.status !== 'pending' ? (
              <span className="text-xs text-gray-400">Resolved</span>
            ) : (
              <div className="flex items-center justify-end gap-2">
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    void handleApprovalDecision(row, 'approved');
                  }}
                  className="rounded-lg border border-gray-200 px-2 py-1 text-xs text-gray-700 hover:bg-gray-50"
                >
                  Approve
                </button>
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    void handleApprovalDecision(row, 'rejected');
                  }}
                  className="rounded-lg border border-red-200 px-2 py-1 text-xs text-red-600 hover:bg-red-50"
                >
                  Reject
                </button>
              </div>
            ),
        }]
      : []),
  ];

  if (loading || !data || !form) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-blue-600" />
      </div>
    );
  }

  const server = data.server;

  return (
    <div className="p-4 sm:p-6">
      <div className="mb-6 flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <Link to="/mcp-servers" className="mb-3 inline-flex items-center gap-2 text-sm text-gray-500 hover:text-gray-700">
            <ArrowLeft className="h-4 w-4" />
            Back to MCP Servers
          </Link>
          <h1 className="text-2xl font-bold text-gray-900">{server.name}</h1>
          <p className="mt-1 text-sm text-gray-500">Server key <code className="rounded bg-gray-100 px-1.5 py-0.5">{server.server_key}</code> • {server.base_url}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {canOperateServer && (
            <>
              <button type="button" onClick={() => void handleRefreshCapabilities()} disabled={refreshing} className="inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50">
                <RefreshCw className="h-4 w-4" />
                {refreshing ? 'Refreshing...' : 'Refresh Capabilities'}
              </button>
              <button type="button" onClick={() => void handleHealthCheck()} disabled={checkingHealth} className="inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50">
                <HeartPulse className="h-4 w-4" />
                {checkingHealth ? 'Checking...' : 'Check Health'}
              </button>
            </>
          )}
          {canMutateServer && (
            <button type="button" onClick={() => setDeleteOpen(true)} className="inline-flex items-center gap-2 rounded-lg bg-red-600 px-3 py-2 text-sm text-white hover:bg-red-700">
              <Trash2 className="h-4 w-4" />
              Delete
            </button>
          )}
        </div>
      </div>

      <div className="mb-5 grid gap-4 lg:grid-cols-4">
        <Card className="lg:col-span-1"><div className="text-xs font-semibold uppercase tracking-wide text-gray-500">Health</div><div className="mt-2 text-lg font-semibold text-gray-900">{server.last_health_status || 'Unchecked'}</div><div className="mt-1 text-sm text-gray-500">{server.last_health_latency_ms != null ? `${server.last_health_latency_ms} ms` : 'No latency recorded yet'}</div></Card>
        <Card className="lg:col-span-1"><div className="text-xs font-semibold uppercase tracking-wide text-gray-500">Discovered Tools</div><div className="mt-2 text-lg font-semibold text-gray-900">{server.tool_count}</div><div className="mt-1 text-sm text-gray-500">Last refresh {fmtDate(server.capabilities_fetched_at)}</div></Card>
        <Card className="lg:col-span-1"><div className="text-xs font-semibold uppercase tracking-wide text-gray-500">Ownership</div><div className="mt-2 text-lg font-semibold text-gray-900">{server.owner_scope_type === 'organization' ? 'Organization' : 'Global'}</div><div className="mt-1 text-sm text-gray-500">{server.owner_scope_type === 'organization' ? server.owner_scope_id : 'Shared across organizations'}</div></Card>
        <Card className="lg:col-span-1"><div className="text-xs font-semibold uppercase tracking-wide text-gray-500">Policies</div><div className="mt-2 text-lg font-semibold text-gray-900">{data.tool_policies.length}</div><div className="mt-1 text-sm text-gray-500">Per-tool limits and cache settings</div></Card>
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)]">
        <div className="space-y-5">
          <Card title="Operations (24h)">
            {operationsLoading && !operations ? (
              <div className="text-sm text-gray-500">Loading operations…</div>
            ) : operations ? (
              <div className="space-y-5">
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                    <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">Calls</div>
                    <div className="mt-1 text-xl font-semibold text-gray-900">{operations.summary.total_calls}</div>
                  </div>
                  <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                    <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">Failures</div>
                    <div className="mt-1 text-xl font-semibold text-gray-900">{operations.summary.failed_calls}</div>
                  </div>
                  <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                    <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">Failure Rate</div>
                    <div className="mt-1 text-xl font-semibold text-gray-900">{fmtPct(operations.summary.failure_rate)}</div>
                  </div>
                  <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                    <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">Avg Latency</div>
                    <div className="mt-1 text-xl font-semibold text-gray-900">{Math.round(operations.summary.avg_latency_ms)} ms</div>
                  </div>
                </div>
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                    <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">Approval Requests</div>
                    <div className="mt-1 text-xl font-semibold text-gray-900">{operations.summary.approval_requests}</div>
                  </div>
                  <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                    <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">Pending</div>
                    <div className="mt-1 text-xl font-semibold text-gray-900">{operations.summary.pending_approvals}</div>
                  </div>
                  <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                    <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">Approved</div>
                    <div className="mt-1 text-xl font-semibold text-gray-900">{operations.summary.approved_approvals}</div>
                  </div>
                  <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                    <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">Rejected</div>
                    <div className="mt-1 text-xl font-semibold text-gray-900">{operations.summary.rejected_approvals}</div>
                  </div>
                </div>
                <div className="grid gap-5 lg:grid-cols-2">
                  <div>
                    <div className="mb-2 text-sm font-semibold text-gray-900">Top Tools</div>
                    <DataTable columns={operationToolColumns} data={operations.top_tools} emptyMessage="No tool calls recorded in the last 24 hours" />
                  </div>
                  <div>
                    <div className="mb-2 text-sm font-semibold text-gray-900">Recent Failures</div>
                    <DataTable columns={failureColumns} data={operations.recent_failures} emptyMessage="No MCP tool failures in the last 24 hours" />
                  </div>
                </div>
              </div>
            ) : (
              <div className="text-sm text-gray-500">Operations data is unavailable.</div>
            )}
          </Card>

          <Card title="Server Configuration" action={
            canMutateServer ? (
              <button type="button" onClick={() => void handleSave()} disabled={saving} className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-3 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50">
                <Save className="h-4 w-4" />
                {saving ? 'Saving...' : 'Save'}
              </button>
            ) : undefined
          }>
            {!canMutateServer && (
              <div className="mb-4 rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-sm text-blue-900">
                Read-only configuration. You can edit only MCP servers owned by one of your organizations.
              </div>
            )}
            <MCPServerForm
              value={form}
              onChange={setForm}
              disableServerKey
              disabled={!canMutateServer}
              ownerScopeOptions={ownerScopeOptions}
              lockOwnerScopeType
              disableOwnerScopeId
            />
          </Card>

          <Card title="Discovered Tools">
            <DataTable columns={toolColumns} data={data.tools} emptyMessage="No tools discovered yet" />
          </Card>
        </div>

        <div className="space-y-5">
          <Card title="Bindings">
            {canManageScopeConfig ? (
              <div className="mb-4 grid gap-3">
                <div className="grid gap-3 sm:grid-cols-2">
                  <select value={bindingForm.scope_type} onChange={(event) => setBindingForm({ ...bindingForm, scope_type: event.target.value as BindingFormState['scope_type'] })} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
                    <option value="organization">Organization</option>
                    <option value="team">Team</option>
                    <option value="api_key">API Key</option>
                  </select>
                  <input value={bindingForm.scope_id} onChange={(event) => setBindingForm({ ...bindingForm, scope_id: event.target.value })} placeholder="Scope ID" className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
                </div>
                <input value={bindingForm.tool_allowlist} onChange={(event) => setBindingForm({ ...bindingForm, tool_allowlist: event.target.value })} placeholder="Allowed tools, comma-separated (optional)" className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
                <label className="flex items-center gap-2 text-sm text-gray-700">
                  <input type="checkbox" checked={bindingForm.enabled} onChange={(event) => setBindingForm({ ...bindingForm, enabled: event.target.checked })} className="rounded border-gray-300" />
                  Enabled
                </label>
                <button type="button" onClick={() => void handleSaveBinding()} disabled={savingBinding} className="rounded-lg bg-blue-600 px-3 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50">
                  {savingBinding ? 'Saving binding...' : 'Save Binding'}
                </button>
              </div>
            ) : (
              <div className="mb-4 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-600">
                Visible access bindings for this server.
              </div>
            )}
            <DataTable columns={bindingColumns} data={data.bindings} emptyMessage="No bindings defined" />
          </Card>

          <Card title="Tool Policies">
            {canManageScopeConfig ? (
              <div className="mb-4 grid gap-3">
                <div className="grid gap-3 sm:grid-cols-2">
                  <select value={policyForm.tool_name} onChange={(event) => setPolicyForm({ ...policyForm, tool_name: event.target.value })} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
                    <option value="">Select tool</option>
                    {toolOptions.map((toolName) => (
                      <option key={toolName} value={toolName}>{toolName}</option>
                    ))}
                  </select>
                  <select value={policyForm.scope_type} onChange={(event) => setPolicyForm({ ...policyForm, scope_type: event.target.value as PolicyFormState['scope_type'] })} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
                    <option value="organization">Organization</option>
                    <option value="team">Team</option>
                    <option value="api_key">API Key</option>
                  </select>
                </div>
                <input value={policyForm.scope_id} onChange={(event) => setPolicyForm({ ...policyForm, scope_id: event.target.value })} placeholder="Scope ID" className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
                <select value={policyForm.require_approval} onChange={(event) => setPolicyForm({ ...policyForm, require_approval: event.target.value as PolicyFormState['require_approval'] })} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
                  <option value="never">Auto-execute</option>
                  <option value="manual">Manual approval</option>
                </select>
                <div className="grid gap-3 sm:grid-cols-3">
                  <input value={policyForm.max_rpm} onChange={(event) => setPolicyForm({ ...policyForm, max_rpm: event.target.value })} placeholder="Max RPM" type="number" className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
                  <input value={policyForm.max_concurrency} onChange={(event) => setPolicyForm({ ...policyForm, max_concurrency: event.target.value })} placeholder="Max concurrency" type="number" className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
                  <input value={policyForm.result_cache_ttl_seconds} onChange={(event) => setPolicyForm({ ...policyForm, result_cache_ttl_seconds: event.target.value })} placeholder="Cache TTL seconds" type="number" className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
                </div>
                <input value={policyForm.max_total_execution_time_ms} onChange={(event) => setPolicyForm({ ...policyForm, max_total_execution_time_ms: event.target.value })} placeholder="Max total execution time (ms)" type="number" className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
                <label className="flex items-center gap-2 text-sm text-gray-700">
                  <input type="checkbox" checked={policyForm.enabled} onChange={(event) => setPolicyForm({ ...policyForm, enabled: event.target.checked })} className="rounded border-gray-300" />
                  Enabled
                </label>
                <button type="button" onClick={() => void handleSavePolicy()} disabled={savingPolicy} className="rounded-lg bg-blue-600 px-3 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50">
                  {savingPolicy ? 'Saving policy...' : 'Save Policy'}
                </button>
              </div>
            ) : (
              <div className="mb-4 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-600">
                Visible tool policies for this server.
              </div>
            )}
            <DataTable columns={policyColumns} data={data.tool_policies} emptyMessage="No tool policies defined" />
          </Card>

          {canUpdateMcp && (
            <Card title="Approval Requests">
              <DataTable
                columns={approvalColumns}
                data={approvalsData?.data || []}
                loading={approvalsLoading}
                emptyMessage="No approval requests recorded"
              />
            </Card>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={deleteOpen}
        title="Delete MCP server"
        description={`Delete "${server.server_key}" and remove all of its bindings and policies?`}
        confirmLabel="Delete"
        destructive
        confirming={deleting}
        onClose={() => setDeleteOpen(false)}
        onConfirm={() => void handleDelete()}
      />
    </div>
  );
}
