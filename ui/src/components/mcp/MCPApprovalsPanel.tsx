import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Clock, Download, ExternalLink, RefreshCw, X } from 'lucide-react';
import MCPApprovalTable from './MCPApprovalTable';
import { TextTabs } from '../admin/shells';
import { useToast } from '../ToastProvider';
import { mcpServers, type MCPApprovalRequest } from '../../lib/api';
import { useApi } from '../../lib/hooks';
import { errorMessage } from '../../lib/errorMessage';
import {
  approvalStatusQueryValue,
  approvalsExportFileName,
  buildApprovalsCsv,
  mcpApprovalsTabPath,
  MCP_APPROVAL_EXPORT_LIMIT,
  MCP_APPROVAL_STATUS_FILTERS,
  offsetAfterEmptyPage,
  type MCPApprovalStatusFilter,
} from '../../lib/mcpApprovals';

const PAGE_SIZE = 20;

type MCPApprovalsPanelProps = {
  /** Show only the requests of this server. */
  serverId?: string | null;
  /** The server is part of the page (server detail). Hides the server column and the filter chip. */
  serverFixed?: boolean;
  status: MCPApprovalStatusFilter;
  onStatusChange: (status: MCPApprovalStatusFilter) => void;
  /** Removes the server filter. Required to show the removable server chip. */
  onClearServer?: () => void;
  /**
   * Number of pending requests for all servers. The panel uses it only when no
   * server filter is set. `null` or `undefined` means unknown, and the panel shows no number.
   */
  pendingCount?: number | null;
  /** Called after a decision or a manual refresh, so the parent can reload its own counts. */
  onRefresh?: () => void;
};

function downloadCsv(csv: string, fileName: string) {
  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = fileName;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

export default function MCPApprovalsPanel({
  serverId = null,
  serverFixed = false,
  status,
  onStatusChange,
  onClearServer,
  pendingCount,
  onRefresh,
}: MCPApprovalsPanelProps) {
  const { pushToast } = useToast();
  const filterKey = `${serverId ?? ''}|${status}`;
  // The page offset belongs to one filter. A new filter starts again at the first page.
  const [page, setPage] = useState({ key: filterKey, offset: 0 });
  const offset = page.key === filterKey ? page.offset : 0;
  const [decidingId, setDecidingId] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  const filterParams = {
    server_id: serverId || undefined,
    status: approvalStatusQueryValue(status),
  };

  const { data, error, loading, refetch } = useApi(
    (signal) => mcpServers.listApprovalRequests({ ...filterParams, limit: PAGE_SIZE, offset }, signal),
    [serverId, status, offset],
  );
  // With a server filter, the notice counts the pending requests of that server only.
  const serverPending = useApi(
    (signal) => (serverId
      ? mcpServers.listApprovalRequests({ server_id: serverId, status: 'pending', limit: 1 }, signal)
      : Promise.resolve(null)),
    [serverId],
  );
  // The chip needs the server name also when the filtered list is empty.
  const serverDetail = useApi(
    (signal) => (serverId && !serverFixed ? mcpServers.get(serverId, signal) : Promise.resolve(null)),
    [serverId, serverFixed],
  );

  const approvals = data?.data ?? [];
  const pagination = data?.pagination;
  const serverName = serverDetail.data?.server?.name || approvals[0]?.server?.name || serverId;
  const shownPendingCount = serverId
    ? (serverPending.data && !serverPending.error ? serverPending.data.pagination.total : null)
    : pendingCount;

  // A decision can remove the last row of a page. Go back to a page that has rows.
  // Use only a response for the current offset, so one empty page moves back one time only.
  if (data && !loading && page.key === filterKey && data.pagination.offset === offset) {
    const nextOffset = offsetAfterEmptyPage(offset, approvals.length, data.pagination.total, PAGE_SIZE);
    if (nextOffset !== null && nextOffset !== offset) setPage({ key: filterKey, offset: nextOffset });
  }

  const refreshAll = () => {
    refetch();
    serverPending.refetch();
    onRefresh?.();
  };

  const handleDecide = async (request: MCPApprovalRequest, decision: 'approved' | 'rejected', comment?: string) => {
    if (decidingId) return;
    setDecidingId(request.mcp_approval_request_id);
    try {
      await mcpServers.decideApprovalRequest(request.mcp_approval_request_id, {
        status: decision,
        decision_comment: decision === 'rejected' ? comment : undefined,
      });
      pushToast({
        tone: decision === 'approved' ? 'success' : 'info',
        title: decision === 'approved' ? 'Approved' : 'Rejected',
        message: decision === 'approved'
          ? `"${request.tool_name}" can run one time. The caller must send the same call again within 15 minutes.`
          : `"${request.tool_name}" was rejected.`,
      });
    } catch (decisionError) {
      pushToast({ tone: 'error', title: 'Decision failed', message: errorMessage(decisionError, 'Failed to process decision.') });
    } finally {
      setDecidingId(null);
      refreshAll();
    }
  };

  const handleExport = async () => {
    if (exporting) return;
    setExporting(true);
    try {
      const result = await mcpServers.listApprovalRequests({ ...filterParams, limit: MCP_APPROVAL_EXPORT_LIMIT, offset: 0 });
      downloadCsv(buildApprovalsCsv(result.data), approvalsExportFileName(status, serverId));
      if (result.pagination.has_more) {
        pushToast({
          tone: 'info',
          title: 'Export is incomplete',
          message: `The file contains the first ${MCP_APPROVAL_EXPORT_LIMIT} of ${result.pagination.total} requests. Use a narrower filter to export the remaining requests.`,
        });
      } else {
        pushToast({ tone: 'success', title: 'Export ready', message: `The file contains ${result.data.length} requests.` });
      }
    } catch (exportError) {
      pushToast({ tone: 'error', title: 'Export failed', message: errorMessage(exportError, 'Could not export approval requests.') });
    } finally {
      setExporting(false);
    }
  };

  const loadFailed = Boolean(error) && !data;

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-3 border-b border-gray-200 sm:flex-row sm:items-end sm:justify-between">
        <div className="flex gap-5 overflow-x-auto">
          <TextTabs
            label="Approval status"
            items={MCP_APPROVAL_STATUS_FILTERS.map((item) => ({ id: item.id, label: item.label }))}
            active={status}
            onChange={onStatusChange}
          />
        </div>
        <div className="flex flex-wrap items-center gap-2 pb-2">
          {serverFixed && serverId ? (
            <Link
              to={mcpApprovalsTabPath(new URLSearchParams({ server_id: serverId }))}
              className="control-sm inline-flex items-center gap-1.5 rounded-lg border border-gray-200 font-medium text-gray-600 transition hover:bg-gray-50"
            >
              <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
              Open in Approvals
            </Link>
          ) : null}
          <button
            type="button"
            onClick={refreshAll}
            aria-label="Refresh approval requests"
            title="Refresh approval requests"
            className="rounded-lg border border-gray-200 p-1.5 text-gray-600 transition hover:bg-gray-50"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin motion-reduce:animate-none' : ''}`} aria-hidden="true" />
          </button>
          <button
            type="button"
            onClick={() => void handleExport()}
            disabled={exporting || loadFailed}
            className="control-sm inline-flex items-center gap-1.5 rounded-lg border border-gray-200 font-medium text-gray-600 transition hover:bg-gray-50 disabled:opacity-40"
          >
            <Download className="h-3.5 w-3.5" aria-hidden="true" />
            {exporting ? 'Exporting…' : 'Export'}
          </button>
        </div>
      </div>

      {(typeof shownPendingCount === 'number' && shownPendingCount > 0) || (!serverFixed && serverId) ? (
        <div className="flex flex-wrap items-center gap-3 text-sm">
          {typeof shownPendingCount === 'number' && shownPendingCount > 0 ? (
            <span className="inline-flex items-center gap-1.5 font-medium text-amber-800">
              <Clock className="h-4 w-4 text-amber-600" aria-hidden="true" />
              {shownPendingCount} request{shownPendingCount === 1 ? '' : 's'} wait{shownPendingCount === 1 ? 's' : ''} for a decision.
            </span>
          ) : null}
          {!serverFixed && serverId ? (
            <span className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-white py-0.5 pl-2.5 pr-1 text-xs text-gray-700">
              Server: <span className="font-medium">{serverName}</span>
              {onClearServer ? (
                <button
                  type="button"
                  onClick={onClearServer}
                  aria-label="Remove server filter"
                  className="rounded-full p-0.5 text-gray-400 transition hover:bg-gray-100 hover:text-gray-700"
                >
                  <X className="h-3.5 w-3.5" aria-hidden="true" />
                </button>
              ) : null}
            </span>
          ) : null}
        </div>
      ) : null}

      <MCPApprovalTable
        approvals={approvals}
        loading={loading}
        totalCount={pagination?.total ?? 0}
        offset={offset}
        limit={PAGE_SIZE}
        hasMore={pagination?.has_more ?? false}
        decidingId={decidingId}
        onDecision={handleDecide}
        onPageChange={(nextOffset) => setPage({ key: filterKey, offset: nextOffset })}
        showServerColumn={!serverFixed}
        emptyMessage={loadFailed ? 'Approval requests are not available' : 'No requests found'}
        emptyHint={loadFailed
          ? errorMessage(error, 'The request failed. Select refresh to try again.')
          : status === 'pending' ? 'No request waits for a decision.' : 'Try a different filter.'}
      />
    </div>
  );
}
