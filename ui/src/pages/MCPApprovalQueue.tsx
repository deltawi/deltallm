import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AlertTriangle, Clock, Download, RefreshCw } from 'lucide-react';
import MCPApprovalTable from '../components/mcp/MCPApprovalTable';
import { mcpServers, type MCPApprovalRequest } from '../lib/api';
import { useApi } from '../lib/hooks';
import { useToast } from '../components/ToastProvider';
import { IndexShell } from '../components/admin/shells';

const LIMIT = 20;
type StatusFilter = 'all' | 'pending' | 'approved' | 'rejected' | 'expired';

export default function MCPApprovalQueue() {
  const navigate = useNavigate();
  const { pushToast } = useToast();

  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [offset, setOffset] = useState(0);
  const [decidingId, setDecidingId] = useState<string | null>(null);

  const { data: approvalsData, loading, refetch } = useApi<{ data: MCPApprovalRequest[]; pagination: any } | null>(
    () =>
      mcpServers.listApprovalRequests({
        status: statusFilter === 'all' ? undefined : statusFilter,
        limit: LIMIT,
        offset,
      }),
    [statusFilter, offset]
  );
  const { data: pendingData, refetch: refetchPending } = useApi<{ data: MCPApprovalRequest[]; pagination: any } | null>(
    () => mcpServers.listApprovalRequests({ status: 'pending', limit: 1, offset: 0 }),
    []
  );

  const approvals = approvalsData?.data || [];
  const pagination = approvalsData?.pagination;
  const pendingCount = pendingData?.pagination?.total ?? 0;
  const hasMore = pagination?.has_more ?? false;

  const handleDecide = async (request: MCPApprovalRequest, decision: 'approved' | 'rejected', decisionComment?: string) => {
    setDecidingId(request.mcp_approval_request_id);
    try {
      await mcpServers.decideApprovalRequest(request.mcp_approval_request_id, {
        status: decision,
        decision_comment: decision === 'rejected' ? decisionComment : undefined,
      });
      pushToast({
        tone: decision === 'approved' ? 'success' : 'info',
        title: decision === 'approved' ? 'Approved' : 'Rejected',
        message: `"${request.tool_name}" execution was ${decision}.`,
      });
      refetch();
      refetchPending();
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Decision failed', message: error?.message || 'Failed to process decision.' });
    } finally {
      setDecidingId(null);
    }
  };

  const handleExport = () => {
    if (!approvals.length) return;
    const headers = ['id', 'created_at', 'server', 'tool_name', 'scope_type', 'scope_id', 'status', 'requestor', 'expires_at', 'decided_at', 'decision_comment'];
    const rows = approvals.map((request) =>
      [
        request.mcp_approval_request_id,
        request.created_at ?? '',
        request.server?.name ?? request.server?.server_key ?? request.mcp_server_id,
        request.tool_name,
        request.scope_type,
        request.scope_id,
        request.status,
        request.requested_by_user ?? request.requested_by_api_key ?? request.scope_id,
        request.expires_at ?? '',
        request.decided_at ?? '',
        request.decision_comment ?? '',
      ]
        .map((value) => `"${String(value).replace(/"/g, '""')}"`)
        .join(',')
    );
    const csv = [headers.join(','), ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'mcp-approvals.csv';
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const statusTabs: Array<{ label: string; value: StatusFilter }> = [
    { label: 'All', value: 'all' },
    { label: 'Pending', value: 'pending' },
    { label: 'Approved', value: 'approved' },
    { label: 'Rejected', value: 'rejected' },
    { label: 'Expired', value: 'expired' },
  ];

  return (
    <IndexShell
      title="Tool Approvals"
      count={pagination?.total ?? null}
      description="Review and approve pending MCP tool execution requests before they reach the upstream server."
      action={(
        <div className="flex flex-shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={() => refetch()}
            className="inline-flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-2 text-sm font-medium text-gray-600 transition hover:bg-gray-50"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
          <button
            type="button"
            onClick={handleExport}
            disabled={!approvals.length}
            className="inline-flex items-center gap-2 rounded-lg border border-gray-200 px-3.5 py-2 text-sm font-medium text-gray-600 transition hover:bg-gray-50 disabled:opacity-40"
          >
            <Download className="h-4 w-4" />
            Export
          </button>
        </div>
      )}
      notice={pendingCount > 0 ? (
        <div className="flex items-center gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3.5">
          <span className="relative flex h-2.5 w-2.5 flex-shrink-0">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-75" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-amber-500" />
          </span>
          <AlertTriangle className="h-4 w-4 flex-shrink-0 text-amber-600" />
          <span className="text-sm font-medium text-amber-800">
            {pendingCount} request{pendingCount !== 1 ? 's' : ''} awaiting review
          </span>
        </div>
      ) : null}
      toolbar={(
        <div className="flex flex-col items-start justify-between gap-4 sm:flex-row sm:items-center">
          <div className="flex items-center gap-1 rounded-lg border border-gray-200 bg-white p-1 shadow-sm">
            {statusTabs.map((tab) => (
              <button
                key={tab.value}
                type="button"
                onClick={() => {
                  setStatusFilter(tab.value);
                  setOffset(0);
                }}
                className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                  statusFilter === tab.value ? 'bg-blue-600 text-white shadow-sm' : 'text-gray-600 hover:bg-gray-100'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-1.5 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs font-semibold text-amber-700">
              <Clock className="h-3 w-3" />
              {pendingCount} Pending
            </span>
            <button
              type="button"
              className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-gray-50 px-2.5 py-1.5 text-xs font-semibold text-gray-600 transition hover:bg-gray-100"
              onClick={() => navigate('/mcp-servers')}
            >
              View MCP Servers →
            </button>
          </div>
        </div>
      )}
    >
      <MCPApprovalTable
        approvals={approvals}
        loading={loading}
        totalCount={pagination?.total ?? 0}
        offset={offset}
        limit={LIMIT}
        hasMore={hasMore}
        decidingId={decidingId}
        onDecision={handleDecide}
        onPageChange={setOffset}
        emptyMessage="No requests found"
        emptyHint={statusFilter !== 'all' ? 'Try a different filter.' : 'No approval requests yet.'}
      />
    </IndexShell>
  );
}
