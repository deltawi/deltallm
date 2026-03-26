import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  Globe,
  RefreshCw,
  HeartPulse,
  Pencil,
  Trash2,
  Zap,
  Activity,
  AlertTriangle,
  BarChart2,
  Users,
  Lock,
  Settings,
  Copy,
  Check,
  Search,
  Save,
  Plus,
  X,
} from 'lucide-react';
import ConfirmDialog from '../components/ConfirmDialog';
import DataTable from '../components/DataTable';
import MCPApprovalTable from '../components/mcp/MCPApprovalTable';
import MCPServerForm, {
  buildMCPServerPayload,
  formFromMCPServer,
  type MCPServerFormValues,
} from '../components/mcp/MCPServerForm';
import {
  type MCPApprovalRequest,
  type MCPBinding,
  mcpServers,
  type MCPNamespacedTool,
  type MCPServerDetail,
  type MCPServerOperations,
  type MCPToolPolicy,
} from '../lib/api';
import { useAuth } from '../lib/auth';
import { resolveUiAccess } from '../lib/authorization';
import { useApi } from '../lib/hooks';
import { useToast } from '../components/ToastProvider';
import { HeroTabbedDetailShell } from '../components/admin/shells';

function fmtDate(value?: string | null) {
  if (!value) return '—';
  return new Date(value).toLocaleString();
}

function fmtPct(value: number) {
  return `${(value * 100).toFixed(1)}%`;
}

function isWriteTool(name: string) {
  return /create|update|delete|add|merge|post|put|patch|write|upsert|modify|edit/i.test(name);
}

function paramCount(tool: MCPNamespacedTool) {
  const props = (tool.input_schema as { properties?: Record<string, unknown> } | undefined)?.properties;
  return props ? Object.keys(props).length : 0;
}

function policyApprovalLabel(policy: MCPToolPolicy) {
  return policy.require_approval === 'manual' ? 'Manual approval' : 'Auto-execute';
}

function policyTimeoutLabel(policy: MCPToolPolicy) {
  if (policy.max_total_execution_time_ms == null) return '—';
  return `${policy.max_total_execution_time_ms} ms`;
}

function bindingFormFromBinding(binding: MCPBinding): BindingFormState {
  return {
    scope_type: binding.scope_type,
    scope_id: binding.scope_id,
    tool_allowlist: (binding.tool_allowlist || []).join(', '),
    enabled: binding.enabled,
  };
}

function policyFormFromPolicy(policy: MCPToolPolicy): PolicyFormState {
  return {
    tool_name: policy.tool_name,
    scope_type: policy.scope_type,
    scope_id: policy.scope_id,
    require_approval: policy.require_approval === 'manual' ? 'manual' : 'never',
    max_rpm: policy.max_rpm != null ? String(policy.max_rpm) : '',
    max_concurrency: policy.max_concurrency != null ? String(policy.max_concurrency) : '',
    max_total_execution_time_ms: policy.max_total_execution_time_ms != null ? String(policy.max_total_execution_time_ms) : '',
    result_cache_ttl_seconds: policy.result_cache_ttl_seconds != null ? String(policy.result_cache_ttl_seconds) : '',
    enabled: policy.enabled,
  };
}

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

type TabId = 'Tools' | 'Access' | 'Policies' | 'Activity' | 'Settings';

const EMPTY_BINDING: BindingFormState = {
  scope_type: 'team',
  scope_id: '',
  tool_allowlist: '',
  enabled: true,
};

const EMPTY_POLICY: PolicyFormState = {
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

function FormField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-gray-500">{label}</label>
      {children}
    </div>
  );
}

const inputCls =
  'w-full rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-900 transition focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-500/30';
const selectCls = inputCls;
const APPROVALS_LIMIT = 20;

export default function MCPServerDetail() {
  const { serverId = '' } = useParams();
  const navigate = useNavigate();
  const { session, authMode } = useAuth();
  const { pushToast } = useToast();

  const uiAccess = resolveUiAccess(authMode, session);
  const canReviewApprovals = uiAccess.mcp_approvals;
  const orgIds = (session?.organization_memberships || [])
    .map((membership) => String(membership.organization_id || ''))
    .filter(Boolean);
  const ownerScopeOptions = orgIds.map((organizationId) => ({ value: organizationId, label: organizationId }));

  const [activeTab, setActiveTab] = useState<TabId>('Tools');
  const [toolFilter, setToolFilter] = useState<'All' | 'Read' | 'Write'>('All');
  const [copied, setCopied] = useState(false);

  const [form, setForm] = useState<MCPServerFormValues | null>(null);
  const [bindingForm, setBindingForm] = useState<BindingFormState>(EMPTY_BINDING);
  const [policyForm, setPolicyForm] = useState<PolicyFormState>(EMPTY_POLICY);
  const [showBindingForm, setShowBindingForm] = useState(false);
  const [showPolicyForm, setShowPolicyForm] = useState(false);
  const [editingBindingId, setEditingBindingId] = useState<string | null>(null);
  const [editingPolicyId, setEditingPolicyId] = useState<string | null>(null);
  const [approvalsOffset, setApprovalsOffset] = useState(0);

  const [saving, setSaving] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [checkingHealth, setCheckingHealth] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [savingBinding, setSavingBinding] = useState(false);
  const [savingPolicy, setSavingPolicy] = useState(false);
  const [decidingApprovalId, setDecidingApprovalId] = useState<string | null>(null);

  const { data, loading, refetch } = useApi<MCPServerDetail | null>(
    () => (serverId ? mcpServers.get(serverId) : Promise.resolve(null)),
    [serverId]
  );
  const { data: operations, loading: operationsLoading, refetch: refetchOperations } = useApi<MCPServerOperations | null>(
    () => (serverId ? mcpServers.operations(serverId) : Promise.resolve(null)),
    [serverId]
  );
  const { data: approvalsData, loading: approvalsLoading, refetch: refetchApprovals } = useApi<{ data: MCPApprovalRequest[]; pagination: any } | null>(
    () => (serverId && canReviewApprovals ? mcpServers.listApprovalRequests({ server_id: serverId, limit: APPROVALS_LIMIT, offset: approvalsOffset }) : Promise.resolve(null)),
    [serverId, canReviewApprovals, approvalsOffset]
  );

  useEffect(() => {
    if (data?.server) {
      setForm(formFromMCPServer(data.server));
    }
  }, [data?.server]);

  useEffect(() => {
    setApprovalsOffset(0);
  }, [serverId]);

  const resetBindingForm = () => {
    setBindingForm(EMPTY_BINDING);
    setEditingBindingId(null);
    setShowBindingForm(false);
  };

  const resetPolicyForm = () => {
    setPolicyForm(EMPTY_POLICY);
    setEditingPolicyId(null);
    setShowPolicyForm(false);
  };

  const server = data?.server;
  const canMutateServer = Boolean(server?.capabilities?.can_mutate);
  const canOperateServer = Boolean(server?.capabilities?.can_operate);
  const canManageScopeConfig = Boolean(server?.capabilities?.can_manage_scope_config);

  const toolOptions = useMemo(() => (data?.tools || []).map((tool) => tool.original_name), [data?.tools]);

  const handleSave = async () => {
    if (!form || !serverId) return;
    setSaving(true);
    try {
      await mcpServers.update(serverId, buildMCPServerPayload(form, { preserveExistingCredentials: true }));
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
    if (!serverId || !canOperateServer) return;
    setRefreshing(true);
    try {
      await mcpServers.refreshCapabilities(serverId);
      pushToast({ tone: 'success', title: 'Capabilities refreshed', message: 'Discovered tools were updated.' });
      refetch();
      refetchOperations();
      refetchApprovals();
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Refresh failed', message: error?.message || 'Failed to refresh tools.' });
    } finally {
      setRefreshing(false);
    }
  };

  const handleHealthCheck = async () => {
    if (!serverId || !canOperateServer) return;
    setCheckingHealth(true);
    try {
      const result = await mcpServers.healthCheck(serverId);
      pushToast({
        tone: result.health.status === 'healthy' ? 'success' : 'error',
        title: 'Health check completed',
        message:
          result.health.status === 'healthy'
            ? `Server responded in ${result.health.latency_ms} ms.`
            : result.health.error || 'The MCP server is unhealthy.',
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
    if (!serverId || !server || !canMutateServer) return;
    setDeleting(true);
    try {
      await mcpServers.delete(serverId);
      pushToast({ tone: 'success', title: 'Server deleted', message: `"${server.server_key}" was deleted.` });
      navigate('/mcp-servers');
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Delete failed', message: error?.message || 'Failed to delete server.' });
    } finally {
      setDeleting(false);
    }
  };

  const handleSaveBinding = async () => {
    if (!server || !canManageScopeConfig) return;
    setSavingBinding(true);
    try {
      await mcpServers.upsertBinding({
        server_id: server.mcp_server_id,
        scope_type: bindingForm.scope_type,
        scope_id: bindingForm.scope_id.trim(),
        enabled: bindingForm.enabled,
        tool_allowlist: bindingForm.tool_allowlist
          .split(',')
          .map((item) => item.trim())
          .filter(Boolean),
      });
      const updatedExisting = editingBindingId !== null;
      resetBindingForm();
      pushToast({
        tone: 'success',
        title: updatedExisting ? 'Binding updated' : 'Binding saved',
        message: 'Scope access updated.',
      });
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
    if (!canManageScopeConfig) return;
    try {
      await mcpServers.deleteBinding(binding.mcp_binding_id);
      if (editingBindingId === binding.mcp_binding_id) {
        resetBindingForm();
      }
      pushToast({ tone: 'success', title: 'Binding removed', message: `${binding.scope_type}:${binding.scope_id} access removed.` });
      refetch();
      refetchOperations();
      refetchApprovals();
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Delete failed', message: error?.message || 'Failed to delete binding.' });
    }
  };

  const handleSavePolicy = async () => {
    if (!server || !canManageScopeConfig) return;
    setSavingPolicy(true);
    try {
      await mcpServers.upsertToolPolicy({
        server_id: server.mcp_server_id,
        tool_name: policyForm.tool_name.trim(),
        scope_type: policyForm.scope_type,
        scope_id: policyForm.scope_id.trim(),
        enabled: policyForm.enabled,
        require_approval: policyForm.require_approval,
        max_rpm: policyForm.max_rpm ? Number(policyForm.max_rpm) : null,
        max_concurrency: policyForm.max_concurrency ? Number(policyForm.max_concurrency) : null,
        result_cache_ttl_seconds: policyForm.result_cache_ttl_seconds ? Number(policyForm.result_cache_ttl_seconds) : null,
        max_total_execution_time_ms: policyForm.max_total_execution_time_ms ? Number(policyForm.max_total_execution_time_ms) : null,
      });
      const updatedExisting = editingPolicyId !== null;
      resetPolicyForm();
      pushToast({
        tone: 'success',
        title: updatedExisting ? 'Policy updated' : 'Policy saved',
        message: 'Tool policy updated.',
      });
      refetch();
      refetchOperations();
      refetchApprovals();
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Policy failed', message: error?.message || 'Failed to save policy.' });
    } finally {
      setSavingPolicy(false);
    }
  };

  const handleDeletePolicy = async (policy: MCPToolPolicy) => {
    if (!canManageScopeConfig) return;
    try {
      await mcpServers.deleteToolPolicy(policy.mcp_tool_policy_id);
      if (editingPolicyId === policy.mcp_tool_policy_id) {
        resetPolicyForm();
      }
      pushToast({ tone: 'success', title: 'Policy removed', message: `Policy for "${policy.tool_name}" deleted.` });
      refetch();
      refetchOperations();
      refetchApprovals();
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Delete failed', message: error?.message || 'Failed to delete policy.' });
    }
  };

  const handleApprovalDecision = async (approval: MCPApprovalRequest, decision: 'approved' | 'rejected', decisionComment?: string) => {
    setDecidingApprovalId(approval.mcp_approval_request_id);
    try {
      await mcpServers.decideApprovalRequest(approval.mcp_approval_request_id, {
        status: decision,
        decision_comment: decision === 'rejected' ? decisionComment : undefined,
      });
      pushToast({
        tone: 'success',
        title: decision === 'approved' ? 'Approved' : 'Rejected',
        message: `${approval.tool_name} request ${decision}.`,
      });
      refetchApprovals();
      refetchOperations();
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Decision failed', message: error?.message || 'Failed to update approval.' });
    } finally {
      setDecidingApprovalId(null);
    }
  };

  const handleEditBinding = (binding: MCPBinding) => {
    setBindingForm(bindingFormFromBinding(binding));
    setEditingBindingId(binding.mcp_binding_id);
    setShowBindingForm(true);
  };

  const handleEditPolicy = (policy: MCPToolPolicy) => {
    setPolicyForm(policyFormFromPolicy(policy));
    setEditingPolicyId(policy.mcp_tool_policy_id);
    setShowPolicyForm(true);
  };

  if (loading || !data || !form || !server) {
    return (
      <div className="flex min-h-full items-center justify-center bg-[#f5f6f7] py-24">
        <div className="flex flex-col items-center gap-3 text-gray-400">
          <RefreshCw className="h-8 w-8 animate-spin text-blue-500" />
          <p className="text-sm">Loading server…</p>
        </div>
      </div>
    );
  }

  const tools = data.tools || [];
  const bindings = data.bindings || [];
  const policies = data.tool_policies || [];
  const approvals = approvalsData?.data || [];
  const pendingApprovals = operations?.summary.pending_approvals ?? approvals.filter((approval) => approval.status === 'pending').length;
  const approvalsPagination = approvalsData?.pagination;

  const healthStatus = server.last_health_status;
  const latencyMs = server.last_health_latency_ms;
  const readTools = tools.filter((tool) => !isWriteTool(tool.original_name));
  const writeTools = tools.filter((tool) => isWriteTool(tool.original_name));
  const filteredTools = toolFilter === 'Read' ? readTools : toolFilter === 'Write' ? writeTools : tools;
  const ownershipLabel = server.owner_scope_type === 'organization' ? `Organization · ${server.owner_scope_id || 'Unknown'}` : 'Global';

  const tabs: Array<{ id: TabId; label: string; count: number | null; icon: typeof Zap }> = [
    { id: 'Tools', label: 'Tools', icon: Zap, count: tools.length },
    { id: 'Access', label: 'Access', icon: Users, count: bindings.length },
    { id: 'Policies', label: 'Policies', icon: Lock, count: policies.length },
    { id: 'Activity', label: 'Activity', icon: Activity, count: null },
    { id: 'Settings', label: 'Settings', icon: Settings, count: null },
  ];

  return (
    <>
      <HeroTabbedDetailShell
        layout="contained"
        backBar={(
          <button
            type="button"
            onClick={() => navigate('/mcp-servers')}
            className="group flex items-center text-sm text-gray-500 transition-colors hover:text-gray-900"
          >
            <ArrowLeft className="mr-2 h-4 w-4 text-gray-400 group-hover:text-gray-600" />
            <span className="font-medium text-gray-600">MCP Servers</span>
            <span className="mx-2 text-gray-300">/</span>
            <span className="font-semibold text-gray-900">{server.name}</span>
          </button>
        )}
        hero={(
          <div className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
          <div className="flex flex-col items-start justify-between gap-6 p-6 md:p-8 lg:flex-row lg:items-center">
            <div className="flex flex-col gap-5 sm:flex-row">
              <div className="flex h-14 w-14 flex-shrink-0 items-center justify-center rounded-xl border border-blue-100 bg-blue-50 text-blue-600 shadow-sm">
                <Globe className="h-7 w-7" />
              </div>
              <div className="space-y-2">
                <div className="flex flex-wrap items-center gap-2.5">
                  <h1 className="text-xl font-bold text-gray-900">{server.name}</h1>
                  <span className="rounded-full border border-blue-200 bg-blue-50 px-2.5 py-0.5 text-xs font-medium text-blue-700">
                    Streamable HTTP
                  </span>
                  <span className="rounded-full border border-gray-200 bg-gray-50 px-2.5 py-0.5 text-xs font-medium text-gray-600">
                    {ownershipLabel}
                  </span>
                  {healthStatus === 'healthy' ? (
                    <div className="flex items-center gap-1.5 rounded-full border border-emerald-100 bg-emerald-50 px-2.5 py-0.5">
                      <span className="relative flex h-2 w-2">
                        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                        <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
                      </span>
                      <span className="text-xs font-medium text-emerald-700">
                        Healthy{latencyMs != null ? ` · ${latencyMs}ms` : ''}
                      </span>
                    </div>
                  ) : healthStatus === 'unhealthy' ? (
                    <div className="flex items-center gap-1.5 rounded-full border border-red-100 bg-red-50 px-2.5 py-0.5">
                      <span className="relative inline-flex h-2 w-2 rounded-full bg-red-500" />
                      <span className="text-xs font-medium text-red-700">Unhealthy</span>
                    </div>
                  ) : (
                    <div className="flex items-center gap-1.5 rounded-full border border-gray-200 bg-gray-50 px-2.5 py-0.5">
                      <span className="relative inline-flex h-2 w-2 rounded-full bg-gray-300" />
                      <span className="text-xs font-medium text-gray-500">Unchecked</span>
                    </div>
                  )}
                  {!server.enabled ? (
                    <span className="rounded-full border border-gray-200 bg-gray-100 px-2.5 py-0.5 text-xs font-medium text-gray-500">
                      Disabled
                    </span>
                  ) : null}
                </div>
                {server.description ? <p className="max-w-xl text-sm text-gray-500">{server.description}</p> : null}
                <div className="mt-1 flex items-center gap-2">
                  <code className="max-w-xs truncate rounded border border-gray-200 bg-gray-100 px-2.5 py-1 text-xs text-gray-600">
                    {server.base_url}
                  </code>
                  <button
                    type="button"
                    onClick={() => {
                      void navigator.clipboard.writeText(server.base_url);
                      setCopied(true);
                      window.setTimeout(() => setCopied(false), 2000);
                    }}
                    className="rounded-md p-1.5 text-gray-400 transition-colors hover:bg-blue-50 hover:text-blue-600"
                    title="Copy URL"
                  >
                    {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
                  </button>
                </div>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              {canOperateServer ? (
                <>
                  <button
                    type="button"
                    disabled={refreshing}
                    onClick={() => void handleRefreshCapabilities()}
                    className="flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-2 text-xs font-medium text-gray-600 transition hover:bg-gray-50 disabled:opacity-50"
                  >
                    <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? 'animate-spin' : ''}`} />
                    {refreshing ? 'Refreshing…' : 'Refresh tools'}
                  </button>
                  <button
                    type="button"
                    disabled={checkingHealth}
                    onClick={() => void handleHealthCheck()}
                    className="flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-2 text-xs font-medium text-gray-600 transition hover:bg-gray-50 disabled:opacity-50"
                  >
                    <HeartPulse className={`h-3.5 w-3.5 ${checkingHealth ? 'animate-pulse' : ''}`} />
                    {checkingHealth ? 'Checking…' : 'Check health'}
                  </button>
                </>
              ) : null}
              {canMutateServer ? (
                <>
                  <button
                    type="button"
                    onClick={() => setActiveTab('Settings')}
                    className="flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-2 text-xs font-medium text-gray-600 transition hover:bg-gray-50"
                  >
                    <Pencil className="h-3.5 w-3.5" />
                    Edit
                  </button>
                  <button
                    type="button"
                    onClick={() => setDeleteOpen(true)}
                    className="flex items-center gap-1.5 rounded-lg border border-red-200 px-3 py-2 text-xs font-medium text-red-600 transition hover:bg-red-50"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    Remove
                  </button>
                </>
              ) : null}
            </div>
          </div>

          <div className="grid grid-cols-2 border-t border-gray-100 md:grid-cols-4">
            {[
              { label: 'Tools', value: server.tool_count, icon: Zap, color: 'text-violet-600', bg: 'bg-violet-50', border: 'border-violet-100' },
              {
                label: 'Calls 24h',
                value: operations ? operations.summary.total_calls : '—',
                icon: Activity,
                color: 'text-blue-600',
                bg: 'bg-blue-50',
                border: 'border-blue-100',
              },
              {
                label: 'Errors',
                value: operations ? operations.summary.failed_calls : '—',
                icon: AlertTriangle,
                color: 'text-orange-600',
                bg: 'bg-orange-50',
                border: 'border-orange-100',
              },
              {
                label: 'Avg latency',
                value: operations ? `${Math.round(operations.summary.avg_latency_ms)}ms` : latencyMs != null ? `${latencyMs}ms` : '—',
                icon: BarChart2,
                color: 'text-blue-600',
                bg: 'bg-blue-50',
                border: 'border-blue-100',
              },
            ].map((stat, index) => (
              <div key={stat.label} className={`flex items-center gap-3.5 p-5 ${index < 3 ? 'border-r border-gray-100' : ''}`}>
                <div className={`rounded-lg border p-2.5 ${stat.bg} ${stat.color} ${stat.border}`}>
                  <stat.icon className="h-4 w-4" />
                </div>
                <div>
                  <p className="text-2xl font-semibold">{stat.value}</p>
                  <p className="mt-0.5 text-xs text-gray-400">{stat.label}</p>
                </div>
              </div>
            ))}
          </div>
          </div>
        )}
        body={(
          <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
          <div className="flex overflow-x-auto border-b border-gray-100">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-2 whitespace-nowrap border-b-2 px-5 py-4 text-sm font-medium transition-colors ${
                  activeTab === tab.id
                    ? 'border-blue-600 bg-blue-50/40 text-blue-600'
                    : 'border-transparent text-gray-500 hover:bg-gray-50 hover:text-gray-800'
                }`}
              >
                <tab.icon className="h-4 w-4" />
                {tab.label}
                {tab.count !== null ? (
                  <span
                    className={`ml-0.5 rounded px-1.5 py-0.5 text-xs font-semibold ${
                      activeTab === tab.id ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-500'
                    }`}
                  >
                    {tab.id === 'Activity' && pendingApprovals > 0 ? pendingApprovals : tab.count}
                  </span>
                ) : null}
              </button>
            ))}
          </div>

          {activeTab === 'Tools' ? (
            <div className="p-6">
              <div className="mb-5 flex flex-col items-start justify-between gap-3 sm:flex-row sm:items-center">
                <div className="flex items-center gap-2">
                  {(['All', 'Read', 'Write'] as const).map((filterValue) => (
                    <button
                      key={filterValue}
                      type="button"
                      onClick={() => setToolFilter(filterValue)}
                      className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
                        toolFilter === filterValue
                          ? 'border-blue-600 bg-blue-600 text-white shadow-sm'
                          : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
                      }`}
                    >
                      {filterValue}
                      <span className={`ml-1.5 ${toolFilter === filterValue ? 'text-blue-200' : 'text-gray-400'}`}>
                        {filterValue === 'All' ? tools.length : filterValue === 'Read' ? readTools.length : writeTools.length}
                      </span>
                    </button>
                  ))}
                </div>
                <div className="flex items-center gap-3 text-xs text-gray-500">
                  <span className="flex items-center gap-1.5">
                    <span className="inline-block h-2.5 w-2.5 rounded-sm bg-emerald-500" />
                    Read-only
                  </span>
                  <span className="flex items-center gap-1.5">
                    <span className="inline-block h-2.5 w-2.5 rounded-sm bg-amber-500" />
                    Write
                  </span>
                </div>
              </div>

              {tools.length === 0 ? (
                <div className="rounded-xl border border-dashed border-gray-200 py-14 text-center text-gray-400">
                  <Zap className="mx-auto mb-2 h-8 w-8 text-gray-200" />
                  <p className="text-sm font-medium text-gray-500">No tools discovered yet</p>
                  <p className="mt-1 text-sm">Click "Refresh tools" above to pull capabilities from this server.</p>
                </div>
              ) : (
                <div className="overflow-hidden rounded-xl border border-gray-200">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-gray-200 bg-gray-50">
                        <th className="px-5 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">Tool</th>
                        <th className="w-24 px-5 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">Type</th>
                        <th className="w-28 px-5 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">Params</th>
                        <th className="px-5 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">Description</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {filteredTools.map((tool) => {
                        const write = isWriteTool(tool.original_name);
                        const params = paramCount(tool);
                        return (
                          <tr key={tool.namespaced_name} className="transition-colors hover:bg-gray-50/60">
                            <td className="relative px-5 py-3.5">
                              <div className={`absolute bottom-0 left-0 top-0 w-[3px] ${write ? 'bg-amber-500' : 'bg-emerald-500'}`} />
                              <span className="font-mono text-xs font-medium text-gray-800">{tool.namespaced_name}</span>
                              {tool.original_name !== tool.namespaced_name ? (
                                <div className="mt-0.5 font-mono text-[10px] text-gray-400">{tool.original_name}</div>
                              ) : null}
                            </td>
                            <td className="px-5 py-3.5">
                              <span
                                className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-semibold ${
                                  write
                                    ? 'border-amber-200 bg-amber-50 text-amber-700'
                                    : 'border-emerald-200 bg-emerald-50 text-emerald-700'
                                }`}
                              >
                                {write ? 'WRITE' : 'READ'}
                              </span>
                            </td>
                            <td className="px-5 py-3.5">
                              <span className="inline-flex items-center rounded-md border border-gray-200 bg-gray-100 px-2 py-1 text-xs font-medium text-gray-600">
                                {params} param{params !== 1 ? 's' : ''}
                              </span>
                            </td>
                            <td className="px-5 py-3.5 text-xs text-gray-600">{tool.description || '—'}</td>
                          </tr>
                        );
                      })}
                      {filteredTools.length === 0 ? (
                        <tr>
                          <td colSpan={4} className="py-12 text-center text-gray-400">
                            <Search className="mx-auto mb-2 h-8 w-8 text-gray-200" />
                            <p className="text-sm">No tools match this filter.</p>
                          </td>
                        </tr>
                      ) : null}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          ) : null}

          {activeTab === 'Access' ? (
            <div className="space-y-5 p-6">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-semibold text-gray-900">Scope Bindings</h3>
                  <p className="mt-0.5 text-xs text-gray-500">Control which organizations, teams, or API keys can access this server.</p>
                </div>
                {canManageScopeConfig ? (
                  <button
                    type="button"
                    onClick={() => {
                      if (showBindingForm && editingBindingId === null) {
                        resetBindingForm();
                        return;
                      }
                      setBindingForm(EMPTY_BINDING);
                      setEditingBindingId(null);
                      setShowBindingForm(true);
                    }}
                    className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-xs font-medium text-white transition hover:bg-blue-700"
                  >
                    <Plus className="h-3.5 w-3.5" />
                    {showBindingForm && editingBindingId === null ? 'Close' : 'Add binding'}
                  </button>
                ) : null}
              </div>

              {!canManageScopeConfig ? (
                <div className="rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-900">
                  Read-only. Scope bindings are managed by the server owner or delegated administrators.
                </div>
              ) : null}

              {showBindingForm && canManageScopeConfig ? (
                <div className="space-y-4 rounded-xl border border-blue-100 bg-blue-50/40 p-5">
                  <div className="flex items-center justify-between">
                    <p className="text-sm font-semibold text-gray-800">{editingBindingId ? 'Edit Binding' : 'New Binding'}</p>
                    <button type="button" onClick={resetBindingForm} className="text-gray-400 hover:text-gray-600">
                      <X className="h-4 w-4" />
                    </button>
                  </div>
                  <div className="grid gap-4 sm:grid-cols-2">
                    <FormField label="Scope Type">
                      <select
                        className={selectCls}
                        value={bindingForm.scope_type}
                        disabled={editingBindingId !== null}
                        onChange={(event) => setBindingForm((value) => ({ ...value, scope_type: event.target.value as BindingFormState['scope_type'] }))}
                      >
                        <option value="team">Team</option>
                        <option value="organization">Organization</option>
                        <option value="api_key">API Key</option>
                      </select>
                    </FormField>
                    <FormField label="Scope ID">
                      <input
                        className={inputCls}
                        placeholder="team-id or org-id…"
                        disabled={editingBindingId !== null}
                        value={bindingForm.scope_id}
                        onChange={(event) => setBindingForm((value) => ({ ...value, scope_id: event.target.value }))}
                      />
                    </FormField>
                    <FormField label="Tool Allowlist (comma-separated, blank = all)">
                      <input className={inputCls} placeholder="tool_name_1, tool_name_2…" value={bindingForm.tool_allowlist} onChange={(event) => setBindingForm((value) => ({ ...value, tool_allowlist: event.target.value }))} />
                    </FormField>
                    <FormField label="Status">
                      <select className={selectCls} value={bindingForm.enabled ? 'true' : 'false'} onChange={(event) => setBindingForm((value) => ({ ...value, enabled: event.target.value === 'true' }))}>
                        <option value="true">Enabled</option>
                        <option value="false">Disabled</option>
                      </select>
                    </FormField>
                  </div>
                  {editingBindingId ? (
                    <div className="rounded-xl border border-gray-200 bg-white px-4 py-3 text-sm text-gray-600">
                      Scope type and scope ID are locked while editing. Delete and recreate the binding to change its identity.
                    </div>
                  ) : null}
                  <div className="flex justify-end gap-2">
                    <button type="button" onClick={resetBindingForm} className="rounded-lg border border-gray-200 px-3 py-2 text-xs text-gray-600 hover:bg-gray-50">
                      Cancel
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleSaveBinding()}
                      disabled={savingBinding || !bindingForm.scope_id.trim()}
                      className="rounded-lg bg-blue-600 px-4 py-2 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                    >
                      {savingBinding ? 'Saving…' : editingBindingId ? 'Save Changes' : 'Save Binding'}
                    </button>
                  </div>
                </div>
              ) : null}

              {bindings.length === 0 ? (
                <div className="rounded-xl border border-dashed border-gray-200 py-12 text-center text-gray-400">
                  <Users className="mx-auto mb-2 h-8 w-8 text-gray-200" />
                  <p className="text-sm font-medium text-gray-500">No bindings yet</p>
                  <p className="mt-1 text-sm">Add a binding to grant scope access to this server.</p>
                </div>
              ) : (
                <div className="overflow-hidden rounded-xl border border-gray-200">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-gray-200 bg-gray-50">
                        <th className="px-5 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">Scope</th>
                        <th className="px-5 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">Allowed Tools</th>
                        <th className="w-24 px-5 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">Status</th>
                        {canManageScopeConfig ? <th className="w-12 px-5 py-3" /> : null}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {bindings.map((binding) => (
                        <tr key={binding.mcp_binding_id} className="transition-colors hover:bg-gray-50/60">
                          <td className="px-5 py-3.5 font-mono text-xs text-gray-800">{binding.scope_type}:{binding.scope_id}</td>
                          <td className="px-5 py-3.5 text-xs text-gray-600">
                            {binding.tool_allowlist?.length ? binding.tool_allowlist.join(', ') : <span className="italic text-gray-400">All tools</span>}
                          </td>
                          <td className="px-5 py-3.5">
                            <span
                              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                                binding.enabled
                                  ? 'border border-blue-100 bg-blue-50 text-blue-700'
                                  : 'border border-gray-200 bg-gray-100 text-gray-500'
                              }`}
                            >
                              {binding.enabled ? 'Enabled' : 'Disabled'}
                            </span>
                          </td>
                          {canManageScopeConfig ? (
                            <td className="px-5 py-3.5 text-right">
                              <div className="flex justify-end gap-1">
                                <button
                                  type="button"
                                  onClick={() => handleEditBinding(binding)}
                                  className="rounded-md p-1.5 text-gray-400 transition-colors hover:bg-blue-50 hover:text-blue-600"
                                >
                                  <Pencil className="h-3.5 w-3.5" />
                                </button>
                                <button type="button" onClick={() => void handleDeleteBinding(binding)} className="rounded-md p-1.5 text-gray-400 transition-colors hover:bg-red-50 hover:text-red-600">
                                  <Trash2 className="h-3.5 w-3.5" />
                                </button>
                              </div>
                            </td>
                          ) : null}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          ) : null}

          {activeTab === 'Policies' ? (
            <div className="space-y-5 p-6">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-semibold text-gray-900">Tool Policies</h3>
                  <p className="mt-0.5 text-xs text-gray-500">Per-tool rate limits, approval gates, and cache settings.</p>
                </div>
                {canManageScopeConfig ? (
                  <button
                    type="button"
                    onClick={() => {
                      if (showPolicyForm && editingPolicyId === null) {
                        resetPolicyForm();
                        return;
                      }
                      setPolicyForm(EMPTY_POLICY);
                      setEditingPolicyId(null);
                      setShowPolicyForm(true);
                    }}
                    className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-xs font-medium text-white transition hover:bg-blue-700"
                  >
                    <Plus className="h-3.5 w-3.5" />
                    {showPolicyForm && editingPolicyId === null ? 'Close' : 'Add policy'}
                  </button>
                ) : null}
              </div>

              {!canManageScopeConfig ? (
                <div className="rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-900">
                  Read-only. Tool policies are managed by the server owner or delegated administrators.
                </div>
              ) : null}

              {showPolicyForm && canManageScopeConfig ? (
                <div className="space-y-4 rounded-xl border border-blue-100 bg-blue-50/40 p-5">
                  <div className="flex items-center justify-between">
                    <p className="text-sm font-semibold text-gray-800">{editingPolicyId ? 'Edit Policy' : 'New Policy'}</p>
                    <button type="button" onClick={resetPolicyForm} className="text-gray-400 hover:text-gray-600">
                      <X className="h-4 w-4" />
                    </button>
                  </div>
                  <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                    <FormField label="Tool Name">
                      {toolOptions.length > 0 ? (
                        <select
                          className={selectCls}
                          value={policyForm.tool_name}
                          disabled={editingPolicyId !== null}
                          onChange={(event) => setPolicyForm((value) => ({ ...value, tool_name: event.target.value }))}
                        >
                          <option value="">Select tool…</option>
                          {toolOptions.map((toolName) => (
                            <option key={toolName} value={toolName}>
                              {toolName}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <input
                          className={inputCls}
                          placeholder="tool_name…"
                          disabled={editingPolicyId !== null}
                          value={policyForm.tool_name}
                          onChange={(event) => setPolicyForm((value) => ({ ...value, tool_name: event.target.value }))}
                        />
                      )}
                    </FormField>
                    <FormField label="Scope Type">
                      <select
                        className={selectCls}
                        value={policyForm.scope_type}
                        disabled={editingPolicyId !== null}
                        onChange={(event) => setPolicyForm((value) => ({ ...value, scope_type: event.target.value as PolicyFormState['scope_type'] }))}
                      >
                        <option value="team">Team</option>
                        <option value="organization">Organization</option>
                        <option value="api_key">API Key</option>
                      </select>
                    </FormField>
                    <FormField label="Scope ID">
                      <input
                        className={inputCls}
                        placeholder="scope-id…"
                        disabled={editingPolicyId !== null}
                        value={policyForm.scope_id}
                        onChange={(event) => setPolicyForm((value) => ({ ...value, scope_id: event.target.value }))}
                      />
                    </FormField>
                    <FormField label="Approval">
                      <select className={selectCls} value={policyForm.require_approval} onChange={(event) => setPolicyForm((value) => ({ ...value, require_approval: event.target.value as PolicyFormState['require_approval'] }))}>
                        <option value="never">Auto-execute</option>
                        <option value="manual">Manual approval</option>
                      </select>
                    </FormField>
                    <FormField label="Max RPM">
                      <input className={inputCls} type="number" placeholder="unlimited" value={policyForm.max_rpm} onChange={(event) => setPolicyForm((value) => ({ ...value, max_rpm: event.target.value }))} />
                    </FormField>
                    <FormField label="Max Concurrency">
                      <input className={inputCls} type="number" placeholder="unlimited" value={policyForm.max_concurrency} onChange={(event) => setPolicyForm((value) => ({ ...value, max_concurrency: event.target.value }))} />
                    </FormField>
                    <FormField label="Cache TTL (seconds)">
                      <input className={inputCls} type="number" placeholder="no cache" value={policyForm.result_cache_ttl_seconds} onChange={(event) => setPolicyForm((value) => ({ ...value, result_cache_ttl_seconds: event.target.value }))} />
                    </FormField>
                    <FormField label="Max Exec Time (ms)">
                      <input className={inputCls} type="number" placeholder="unlimited" value={policyForm.max_total_execution_time_ms} onChange={(event) => setPolicyForm((value) => ({ ...value, max_total_execution_time_ms: event.target.value }))} />
                    </FormField>
                    <FormField label="Status">
                      <select className={selectCls} value={policyForm.enabled ? 'true' : 'false'} onChange={(event) => setPolicyForm((value) => ({ ...value, enabled: event.target.value === 'true' }))}>
                        <option value="true">Enabled</option>
                        <option value="false">Disabled</option>
                      </select>
                    </FormField>
                  </div>
                  {editingPolicyId ? (
                    <div className="rounded-xl border border-gray-200 bg-white px-4 py-3 text-sm text-gray-600">
                      Tool name and scope are locked while editing. Delete and recreate the policy to change its identity.
                    </div>
                  ) : null}
                  {policyForm.require_approval === 'manual' ? (
                    <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                      Manual approval currently applies only to direct <code className="rounded bg-amber-100 px-1 py-0.5 text-xs">/mcp</code> tool
                      calls. Chat and responses MCP bridge requests will be rejected until resume-after-approval is implemented.
                    </div>
                  ) : null}
                  <div className="flex justify-end gap-2">
                    <button type="button" onClick={resetPolicyForm} className="rounded-lg border border-gray-200 px-3 py-2 text-xs text-gray-600 hover:bg-gray-50">
                      Cancel
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleSavePolicy()}
                      disabled={savingPolicy || !policyForm.tool_name.trim() || !policyForm.scope_id.trim()}
                      className="rounded-lg bg-blue-600 px-4 py-2 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                    >
                      {savingPolicy ? 'Saving…' : editingPolicyId ? 'Save Changes' : 'Save Policy'}
                    </button>
                  </div>
                </div>
              ) : null}

              {policies.length === 0 ? (
                <div className="rounded-xl border border-dashed border-gray-200 py-12 text-center text-gray-400">
                  <Lock className="mx-auto mb-2 h-8 w-8 text-gray-200" />
                  <p className="text-sm font-medium text-gray-500">No policies yet</p>
                  <p className="mt-1 text-sm">Add a policy to set rate limits, approval gates, or cache rules.</p>
                </div>
              ) : (
                <div className="overflow-hidden rounded-xl border border-gray-200">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-gray-200 bg-gray-50">
                        {['Tool', 'Scope', 'Status', 'Approval', 'RPM', 'Concurrency', 'Cache TTL', 'Exec Timeout', ''].map((header, index) => (
                          <th key={`${header}-${index}`} className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">
                            {header}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {policies.map((policy) => (
                        <tr key={policy.mcp_tool_policy_id} className="transition-colors hover:bg-gray-50/60">
                          <td className="px-4 py-3.5 font-mono text-xs text-gray-800">{policy.tool_name}</td>
                          <td className="px-4 py-3.5 font-mono text-xs text-gray-600">{policy.scope_type}:{policy.scope_id}</td>
                          <td className="px-4 py-3.5">
                            <span
                              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                                policy.enabled
                                  ? 'border border-blue-100 bg-blue-50 text-blue-700'
                                  : 'border border-gray-200 bg-gray-100 text-gray-500'
                              }`}
                            >
                              {policy.enabled ? 'Enabled' : 'Disabled'}
                            </span>
                          </td>
                          <td className="px-4 py-3.5">
                            <span
                              className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium ${
                                policy.require_approval === 'manual'
                                  ? 'border-amber-200 bg-amber-50 text-amber-700'
                                  : 'border-gray-200 bg-gray-100 text-gray-500'
                              }`}
                            >
                              {policyApprovalLabel(policy)}
                            </span>
                            {policy.require_approval === 'manual' ? <div className="mt-1 text-[11px] text-amber-700">Direct /mcp only</div> : null}
                          </td>
                          <td className="px-4 py-3.5 text-xs text-gray-600">{policy.max_rpm ?? '—'}</td>
                          <td className="px-4 py-3.5 text-xs text-gray-600">{policy.max_concurrency ?? '—'}</td>
                          <td className="px-4 py-3.5 text-xs text-gray-600">
                            {policy.result_cache_ttl_seconds != null ? `${policy.result_cache_ttl_seconds}s` : '—'}
                          </td>
                          <td className="px-4 py-3.5 text-xs text-gray-600">{policyTimeoutLabel(policy)}</td>
                          {canManageScopeConfig ? (
                            <td className="px-4 py-3.5 text-right">
                              <div className="flex justify-end gap-1">
                                <button
                                  type="button"
                                  onClick={() => handleEditPolicy(policy)}
                                  className="rounded-md p-1.5 text-gray-400 transition-colors hover:bg-blue-50 hover:text-blue-600"
                                >
                                  <Pencil className="h-3.5 w-3.5" />
                                </button>
                                <button type="button" onClick={() => void handleDeletePolicy(policy)} className="rounded-md p-1.5 text-gray-400 transition-colors hover:bg-red-50 hover:text-red-600">
                                  <Trash2 className="h-3.5 w-3.5" />
                                </button>
                              </div>
                            </td>
                          ) : (
                            <td className="px-4 py-3.5" />
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          ) : null}

          {activeTab === 'Activity' ? (
            <div className="space-y-6 p-6">
              {canReviewApprovals ? (
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-gray-900">
                      Approval Requests
                      {pendingApprovals > 0 ? (
                        <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-xs font-semibold text-amber-700">
                          {pendingApprovals} pending
                        </span>
                      ) : null}
                    </h3>
                  </div>
                  <MCPApprovalTable
                    approvals={approvals}
                    loading={approvalsLoading}
                    totalCount={approvalsPagination?.total ?? 0}
                    offset={approvalsOffset}
                    limit={APPROVALS_LIMIT}
                    hasMore={approvalsPagination?.has_more ?? false}
                    decidingId={decidingApprovalId}
                    onDecision={handleApprovalDecision}
                    onPageChange={setApprovalsOffset}
                    showServerColumn={false}
                    emptyMessage="No approval requests"
                    emptyHint="No approval requests yet."
                  />
                </div>
              ) : null}

              <div className="space-y-4">
                <h3 className="text-sm font-semibold text-gray-900">Operations (24h)</h3>
                {operationsLoading && !operations ? (
                  <p className="text-sm text-gray-400">Loading operations…</p>
                ) : operations ? (
                  <div className="space-y-4">
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                      {[
                        { label: 'Total Calls', value: operations.summary.total_calls },
                        { label: 'Failures', value: operations.summary.failed_calls },
                        { label: 'Failure Rate', value: fmtPct(operations.summary.failure_rate) },
                        { label: 'Avg Latency', value: `${Math.round(operations.summary.avg_latency_ms)}ms` },
                      ].map((item) => (
                        <div key={item.label} className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
                          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">{item.label}</p>
                          <p className="mt-1 text-xl font-semibold text-gray-900">{item.value}</p>
                        </div>
                      ))}
                    </div>
                    <div className="grid gap-5 lg:grid-cols-2">
                      <div>
                        <p className="mb-2 text-sm font-semibold text-gray-700">Top Tools</p>
                        <DataTable
                          columns={[
                            { key: 'tool_name', header: 'Tool', render: (row: any) => <span className="font-mono text-xs">{row.tool_name}</span> },
                            { key: 'total_calls', header: 'Calls' },
                            { key: 'failed_calls', header: 'Failures' },
                            { key: 'avg_latency_ms', header: 'Avg Latency', render: (row: any) => `${Math.round(Number(row.avg_latency_ms || 0))}ms` },
                          ]}
                          data={operations.top_tools}
                          emptyMessage="No tool calls in the last 24h"
                        />
                      </div>
                      <div>
                        <p className="mb-2 text-sm font-semibold text-gray-700">Recent Failures</p>
                        <DataTable
                          columns={[
                            { key: 'occurred_at', header: 'Time', render: (row: any) => fmtDate(row.occurred_at) },
                            { key: 'tool_name', header: 'Tool', render: (row: any) => <span className="font-mono text-xs">{row.tool_name}</span> },
                            {
                              key: 'error_type',
                              header: 'Error',
                              render: (row: any) => (row.error_code ? `${row.error_type || 'Error'} (${row.error_code})` : row.error_type || 'Error'),
                            },
                          ]}
                          data={operations.recent_failures}
                          emptyMessage="No failures in the last 24h"
                        />
                      </div>
                    </div>
                  </div>
                ) : (
                  <p className="text-sm text-gray-400">Operations data unavailable.</p>
                )}
              </div>
            </div>
          ) : null}

          {activeTab === 'Settings' ? (
            <div className="space-y-5 p-6">
              {!canMutateServer ? (
                <div className="rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-900">
                  Read-only. Only the server owner can edit definition settings.
                </div>
              ) : null}
              {canMutateServer ? (
                <>
                  <MCPServerForm
                    value={form}
                    onChange={setForm}
                    disableServerKey
                    ownerScopeOptions={ownerScopeOptions}
                    lockOwnerScopeType
                    disableOwnerScopeId
                    preserveExistingCredentials
                    credentialsConfigured={server.auth_credentials_present}
                  />
                  <div className="flex justify-end pt-2">
                    <button
                      type="button"
                      onClick={() => void handleSave()}
                      disabled={saving}
                      className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-700 disabled:opacity-50"
                    >
                      <Save className="h-4 w-4" />
                      {saving ? 'Saving…' : 'Save changes'}
                    </button>
                  </div>
                </>
              ) : (
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">Server Key</p>
                    <p className="mt-1 font-mono text-sm text-gray-900">{server.server_key}</p>
                  </div>
                  <div className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">Owner</p>
                    <p className="mt-1 text-sm text-gray-900">{ownershipLabel}</p>
                  </div>
                  <div className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">Auth Mode</p>
                    <p className="mt-1 text-sm text-gray-900">{server.auth_mode}</p>
                    <p className="mt-1 text-xs text-gray-500">
                      {server.auth_credentials_present ? 'Credentials configured' : 'No stored credentials'}
                    </p>
                  </div>
                  <div className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">Forwarded Headers</p>
                    <p className="mt-1 text-sm text-gray-900">
                      {server.forwarded_headers_allowlist?.length ? server.forwarded_headers_allowlist.join(', ') : 'None'}
                    </p>
                  </div>
                  <div className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">Timeout</p>
                    <p className="mt-1 text-sm text-gray-900">{server.request_timeout_ms} ms</p>
                  </div>
                  <div className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">Status</p>
                    <p className="mt-1 text-sm text-gray-900">{server.enabled ? 'Enabled' : 'Disabled'}</p>
                  </div>
                </div>
              )}
            </div>
          ) : null}
          </div>
        )}
      />

      <ConfirmDialog
        open={deleteOpen}
        title="Delete MCP server"
        description={`Delete "${server.server_key}" and remove all its bindings and policies? This cannot be undone.`}
        confirmLabel="Delete"
        destructive
        confirming={deleting}
        onClose={() => setDeleteOpen(false)}
        onConfirm={() => void handleDelete()}
      />
    </>
  );
}
