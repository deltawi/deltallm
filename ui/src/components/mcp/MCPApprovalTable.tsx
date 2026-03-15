import { Fragment, useState } from 'react';
import { CheckCircle2, ChevronDown, ChevronUp, Clock, Filter, Hourglass, RefreshCw, XCircle } from 'lucide-react';
import type { MCPApprovalRequest } from '../../lib/api';

function fmtDate(value?: string | null) {
  if (!value) return '—';
  return new Date(value).toLocaleString();
}

function fmtRelative(value?: string | null) {
  if (!value) return '—';
  const diff = Date.now() - new Date(value).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function fmtRequestor(request: MCPApprovalRequest) {
  const apiKey = request.requested_by_api_key;
  if (apiKey && apiKey.length > 12) return `${apiKey.slice(0, 12)}…`;
  if (apiKey) return apiKey;
  if (request.requested_by_user) return request.requested_by_user;
  return request.scope_id;
}

function fmtInputPreview(request: MCPApprovalRequest) {
  if (!request.arguments_json) return '{}';
  try {
    const text = JSON.stringify(request.arguments_json);
    return text.length > 60 ? `${text.slice(0, 60)}…` : text;
  } catch {
    return '{ … }';
  }
}

function fmtInputFull(request: MCPApprovalRequest) {
  if (!request.arguments_json) return '{}';
  try {
    return JSON.stringify(request.arguments_json, null, 2);
  } catch {
    return '{}';
  }
}

function serverLabel(request: MCPApprovalRequest) {
  return request.server?.name || request.server?.server_key || request.mcp_server_id;
}

function ownerLabel(request: MCPApprovalRequest) {
  if (!request.server) return '—';
  return request.server.owner_scope_type === 'organization'
    ? `Organization · ${request.server.owner_scope_id || 'Unknown'}`
    : 'Global';
}

function ApprovalStatusBadge({ request }: { request: MCPApprovalRequest }) {
  if (request.status === 'pending') {
    return (
      <div className="flex items-center gap-1.5">
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-75" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-amber-500" />
        </span>
        <span className="text-xs font-semibold text-amber-700">Pending</span>
      </div>
    );
  }
  if (request.status === 'approved') {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-semibold text-emerald-700">
        <CheckCircle2 className="h-3.5 w-3.5" />
        Approved
      </span>
    );
  }
  if (request.status === 'expired') {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-semibold text-gray-500">
        <Hourglass className="h-3.5 w-3.5" />
        Expired
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs font-semibold text-red-600">
      <XCircle className="h-3.5 w-3.5" />
      Rejected
    </span>
  );
}

type MCPApprovalTableProps = {
  approvals: MCPApprovalRequest[];
  loading: boolean;
  totalCount: number;
  offset: number;
  limit: number;
  hasMore: boolean;
  onPageChange: (nextOffset: number) => void;
  onDecision?: (request: MCPApprovalRequest, decision: 'approved' | 'rejected', comment?: string) => Promise<void>;
  decidingId?: string | null;
  showServerColumn?: boolean;
  emptyMessage?: string;
  emptyHint?: string;
};

export default function MCPApprovalTable({
  approvals,
  loading,
  totalCount,
  offset,
  limit,
  hasMore,
  onPageChange,
  onDecision,
  decidingId = null,
  showServerColumn = true,
  emptyMessage = 'No requests found',
  emptyHint = 'No approval requests yet.',
}: MCPApprovalTableProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [rejectComment, setRejectComment] = useState('');
  const hasPrev = offset > 0;
  const colSpan = showServerColumn ? 8 : 7;

  return (
    <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-200 bg-gray-50">
            <th className="w-28 px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">Requested</th>
            {showServerColumn ? <th className="w-32 px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">Server</th> : null}
            <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">Tool</th>
            <th className="w-32 px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">Requestor</th>
            <th className="w-32 px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">Scope</th>
            <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">Input preview</th>
            <th className="w-28 px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">Status</th>
            <th className="w-44 px-4 py-3" />
          </tr>
        </thead>
        <tbody>
          {loading && approvals.length === 0 ? (
            <tr>
              <td colSpan={colSpan} className="py-16 text-center">
                <RefreshCw className="mx-auto mb-2 h-6 w-6 animate-spin text-blue-500" />
                <p className="text-sm text-gray-400">Loading approvals…</p>
              </td>
            </tr>
          ) : null}
          {!loading && approvals.length === 0 ? (
            <tr>
              <td colSpan={colSpan} className="py-16 text-center">
                <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-gray-100">
                  <Filter className="h-5 w-5 text-gray-400" />
                </div>
                <h3 className="text-sm font-medium text-gray-900">{emptyMessage}</h3>
                <p className="mt-1 text-sm text-gray-500">{emptyHint}</p>
              </td>
            </tr>
          ) : null}
          {approvals.map((request) => {
            const isExpanded = expandedId === request.mcp_approval_request_id;
            const isDeciding = decidingId === request.mcp_approval_request_id;
            const canDecide = Boolean(onDecision && request.capabilities?.can_decide);
            return (
              <Fragment key={request.mcp_approval_request_id}>
                <tr className="border-b border-gray-100 transition hover:bg-gray-50/60">
                  <td className="px-4 py-3.5 align-top">
                    <div className="text-xs font-medium text-gray-800">{fmtRelative(request.created_at)}</div>
                    <div className="mt-0.5 text-[11px] text-gray-400">{fmtDate(request.created_at)}</div>
                  </td>
                  {showServerColumn ? (
                    <td className="px-4 py-3.5 align-top">
                      <div className="font-medium text-gray-800">{serverLabel(request)}</div>
                      {request.server?.server_key && request.server.server_key !== request.server.name ? (
                        <div className="mt-0.5 font-mono text-[11px] text-gray-400">{request.server.server_key}</div>
                      ) : null}
                    </td>
                  ) : null}
                  <td className="px-4 py-3.5 align-top">
                    <div className="font-mono text-xs text-gray-800">{request.tool_name}</div>
                  </td>
                  <td className="px-4 py-3.5 align-top text-xs text-gray-600">{fmtRequestor(request)}</td>
                  <td className="px-4 py-3.5 align-top">
                    <div className="font-mono text-xs text-gray-600">{request.scope_type}:{request.scope_id}</div>
                  </td>
                  <td className="px-4 py-3.5 align-top">
                    <code className="rounded bg-gray-100 px-2 py-1 font-mono text-[11px] text-gray-600">{fmtInputPreview(request)}</code>
                  </td>
                  <td className="px-4 py-3.5 align-top">
                    <ApprovalStatusBadge request={request} />
                  </td>
                  <td className="px-4 py-3.5 align-top">
                    <div className="flex items-center justify-end gap-2">
                      {canDecide ? (
                        <button
                          type="button"
                          disabled={isDeciding}
                          onClick={() => void onDecision?.(request, 'approved')}
                          className="rounded-md bg-emerald-600 px-2.5 py-1.5 text-xs font-medium text-white transition hover:bg-emerald-700 disabled:opacity-50"
                        >
                          Approve
                        </button>
                      ) : null}
                      <button
                        type="button"
                        onClick={() => {
                          setExpandedId(isExpanded ? null : request.mcp_approval_request_id);
                          setRejectComment('');
                        }}
                        className="inline-flex items-center gap-1 rounded-md border border-gray-200 px-2 py-1.5 text-xs font-medium text-gray-600 transition hover:bg-gray-50"
                      >
                        Details
                        {isExpanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                      </button>
                    </div>
                  </td>
                </tr>
                {isExpanded ? (
                  <tr className="border-b border-gray-100 bg-gray-50/50">
                    <td colSpan={colSpan} className="px-4 py-4">
                      <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
                        <div>
                          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">Input</p>
                          <pre className="overflow-x-auto rounded-lg border border-gray-200 bg-white p-3 text-xs text-gray-700">
                            {fmtInputFull(request)}
                          </pre>
                        </div>
                        <div className="space-y-4">
                          <div className="grid gap-3 sm:grid-cols-2">
                            <div className="rounded-lg border border-gray-200 bg-white px-3 py-2">
                              <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">Server</p>
                              <p className="mt-1 text-sm text-gray-900">{serverLabel(request)}</p>
                              <p className="mt-1 text-xs text-gray-500">{ownerLabel(request)}</p>
                            </div>
                            <div className="rounded-lg border border-gray-200 bg-white px-3 py-2">
                              <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">Expires</p>
                              <p className="mt-1 text-sm text-gray-900">{fmtDate(request.expires_at)}</p>
                            </div>
                            <div className="rounded-lg border border-gray-200 bg-white px-3 py-2">
                              <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">Decided At</p>
                              <p className="mt-1 text-sm text-gray-900">{fmtDate(request.decided_at)}</p>
                            </div>
                            <div className="rounded-lg border border-gray-200 bg-white px-3 py-2">
                              <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">Reviewer</p>
                              <p className="mt-1 break-all font-mono text-xs text-gray-700">{request.decided_by_account_id || '—'}</p>
                            </div>
                          </div>
                          {request.decision_comment ? (
                            <div className="rounded-lg border border-gray-200 bg-white px-3 py-2">
                              <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">Decision Comment</p>
                              <p className="mt-1 text-sm text-gray-700">{request.decision_comment}</p>
                            </div>
                          ) : null}
                          {canDecide ? (
                            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3">
                              <p className="text-xs font-semibold uppercase tracking-wide text-amber-700">Review</p>
                              <textarea
                                value={rejectComment}
                                onChange={(event) => setRejectComment(event.target.value)}
                                rows={3}
                                placeholder="Optional rejection comment"
                                className="mt-2 w-full rounded-lg border border-amber-200 bg-white px-3 py-2 text-sm text-gray-900 focus:border-amber-400 focus:outline-none focus:ring-2 focus:ring-amber-500/30"
                              />
                              <div className="mt-3 flex justify-end gap-2">
                                <button
                                  type="button"
                                  disabled={isDeciding}
                                  onClick={() => void onDecision?.(request, 'rejected', rejectComment.trim() || undefined)}
                                  className="rounded-md border border-red-200 px-3 py-1.5 text-xs font-medium text-red-600 transition hover:bg-red-50 disabled:opacity-50"
                                >
                                  Reject
                                </button>
                                <button
                                  type="button"
                                  disabled={isDeciding}
                                  onClick={() => void onDecision?.(request, 'approved')}
                                  className="rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-emerald-700 disabled:opacity-50"
                                >
                                  Approve
                                </button>
                              </div>
                            </div>
                          ) : null}
                        </div>
                      </div>
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>

      <div className="flex items-center justify-between border-t border-gray-100 bg-gray-50/50 px-4 py-3">
        <p className="text-xs text-gray-500">
          Showing {approvals.length} of {totalCount} request{totalCount !== 1 ? 's' : ''}
        </p>
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-xs font-medium text-gray-600">
            <Clock className="h-3 w-3" />
            Page {Math.floor(offset / limit) + 1}
          </span>
          <button
            type="button"
            onClick={() => onPageChange(Math.max(0, offset - limit))}
            disabled={!hasPrev}
            className="rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-600 transition hover:bg-white disabled:opacity-40"
          >
            Previous
          </button>
          <button
            type="button"
            onClick={() => onPageChange(offset + limit)}
            disabled={!hasMore}
            className="rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-600 transition hover:bg-white disabled:opacity-40"
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}
