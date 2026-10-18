import type { MCPApprovalRequest } from './api';

export type MCPPageTab = 'servers' | 'bundles' | 'approvals';

export type MCPApprovalStatusFilter = 'pending' | 'approved' | 'rejected' | 'expired' | 'all';

export const MCP_APPROVAL_STATUS_FILTERS: ReadonlyArray<{ id: MCPApprovalStatusFilter; label: string }> = [
  { id: 'pending', label: 'Pending' },
  { id: 'approved', label: 'Approved' },
  { id: 'rejected', label: 'Rejected' },
  { id: 'expired', label: 'Expired' },
  { id: 'all', label: 'All' },
];

export const DEFAULT_MCP_APPROVAL_STATUS: MCPApprovalStatusFilter = 'pending';

// The API returns at most 500 rows for one read. The export uses one read only.
export const MCP_APPROVAL_EXPORT_LIMIT = 500;

export const MCP_SERVERS_PATH = '/mcp-servers';

/**
 * Resolve the active tab from the `tab` query value. A user without approval
 * access always gets the Servers tab, also when the URL asks for approvals.
 * Every user of the page can open the Bundles tab.
 */
export function resolveMCPPageTab(raw: string | null | undefined, canReviewApprovals: boolean): MCPPageTab {
  if (raw === 'approvals' && canReviewApprovals) return 'approvals';
  if (raw === 'bundles') return 'bundles';
  return 'servers';
}

export function parseApprovalStatusFilter(raw: string | null | undefined): MCPApprovalStatusFilter {
  const match = MCP_APPROVAL_STATUS_FILTERS.find((item) => item.id === raw);
  return match ? match.id : DEFAULT_MCP_APPROVAL_STATUS;
}

/** The value to send as the `status` query parameter. `all` sends no filter. */
export function approvalStatusQueryValue(filter: MCPApprovalStatusFilter): string | undefined {
  return filter === 'all' ? undefined : filter;
}

/**
 * Build the URL of the Approvals tab. The legacy `/mcp-approvals` route uses
 * this to keep the `status` and `server_id` filters of an old link.
 */
export function mcpApprovalsTabPath(search: string | URLSearchParams = ''): string {
  const source = typeof search === 'string' ? new URLSearchParams(search) : search;
  const next = new URLSearchParams();
  next.set('tab', 'approvals');
  for (const key of ['status', 'server_id']) {
    const value = source.get(key);
    if (value) next.set(key, value);
  }
  return `${MCP_SERVERS_PATH}?${next.toString()}`;
}

export type ApprovalStatusTone = 'pending' | 'approved' | 'rejected' | 'expired' | 'unknown';

export type ApprovalStatusView = { label: string; tone: ApprovalStatusTone };

/**
 * Map an API status to its label. An approval is single-use: after the gateway
 * runs the approved call, the API sets `consumed_at`. An unknown status shows
 * its raw text, so the UI never shows a status that the API did not send.
 */
export function approvalStatusView(status: string, consumedAt?: string | null): ApprovalStatusView {
  switch (status) {
    case 'pending':
      return { label: 'Pending', tone: 'pending' };
    case 'approved':
      return { label: consumedAt ? 'Approved · used' : 'Approved', tone: 'approved' };
    case 'rejected':
      return { label: 'Rejected', tone: 'rejected' };
    case 'expired':
      return { label: 'Expired', tone: 'expired' };
    default:
      return { label: status || 'Unknown', tone: 'unknown' };
  }
}

const CSV_HEADERS = [
  'id',
  'created_at',
  'server',
  'tool_name',
  'scope_type',
  'scope_id',
  'status',
  'requestor',
  'expires_at',
  'decided_at',
  'consumed_at',
  'decision_comment',
] as const;

// A spreadsheet can run a cell that starts with one of these characters as a formula.
const FORMULA_PREFIX = /^[=+\-@\t\r]/;

export function csvCell(value: string | null | undefined): string {
  const text = value ?? '';
  const safe = FORMULA_PREFIX.test(text) ? `'${text}` : text;
  return `"${safe.replace(/"/g, '""')}"`;
}

function approvalCsvRow(request: MCPApprovalRequest): string[] {
  return [
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
    request.consumed_at ?? '',
    request.decision_comment ?? '',
  ];
}

export function buildApprovalsCsv(requests: readonly MCPApprovalRequest[]): string {
  const lines = [CSV_HEADERS.join(','), ...requests.map((request) => approvalCsvRow(request).map(csvCell).join(','))];
  return lines.join('\n');
}

export function approvalsExportFileName(filter: MCPApprovalStatusFilter, serverId?: string | null): string {
  const parts = ['mcp-approvals', filter];
  if (serverId) parts.push(serverId.replace(/[^A-Za-z0-9_-]/g, '_'));
  return `${parts.join('-')}.csv`;
}

export type ApprovalDecisionActions = { canApprove: boolean; canReject: boolean; ownRequest: boolean };

/**
 * The decisions that the viewer can make on one request. A requester cannot
 * approve an own call but can reject it. An older API does not send
 * `can_approve`; then approval follows `can_decide`.
 */
export function approvalDecisionActions(request: Pick<MCPApprovalRequest, 'capabilities'>): ApprovalDecisionActions {
  const canDecide = Boolean(request.capabilities?.can_decide);
  const approveBlocked = request.capabilities?.can_approve === false;
  return { canApprove: canDecide && !approveBlocked, canReject: canDecide, ownRequest: canDecide && approveBlocked };
}

/**
 * The offset to use when a page has no rows, for example after the last
 * pending row of the page was decided. Returns `null` when no change is
 * necessary.
 */
export function offsetAfterEmptyPage(offset: number, rowCount: number, total: number, pageSize: number): number | null {
  if (rowCount > 0 || offset <= 0) return null;
  if (total <= 0) return 0;
  const lastPageOffset = Math.floor((total - 1) / pageSize) * pageSize;
  return Math.max(0, Math.min(offset - pageSize, lastPageOffset));
}
