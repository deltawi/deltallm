import assert from 'node:assert/strict';
import test from 'node:test';
import type { MCPApprovalRequest } from '../src/lib/api';
import {
  approvalDecisionActions,
  approvalStatusQueryValue,
  approvalStatusView,
  approvalsExportFileName,
  buildApprovalsCsv,
  csvCell,
  mcpApprovalsTabPath,
  MCP_APPROVAL_STATUS_FILTERS,
  offsetAfterEmptyPage,
  parseApprovalStatusFilter,
  resolveMCPPageTab,
} from '../src/lib/mcpApprovals';

function request(overrides: Partial<MCPApprovalRequest> = {}): MCPApprovalRequest {
  return {
    mcp_approval_request_id: 'apr-1',
    mcp_server_id: 'srv-1',
    tool_name: 'github.create_issue',
    scope_type: 'team',
    scope_id: 'team-1',
    status: 'pending',
    request_fingerprint: 'fp',
    created_at: '2026-10-01T10:00:00Z',
    server: { mcp_server_id: 'srv-1', server_key: 'github', name: 'GitHub' },
    ...overrides,
  };
}

test('the approvals tab needs approval access', () => {
  assert.equal(resolveMCPPageTab('approvals', true), 'approvals');
  assert.equal(resolveMCPPageTab('approvals', false), 'servers');
  assert.equal(resolveMCPPageTab(null, true), 'servers');
  assert.equal(resolveMCPPageTab('unknown', true), 'servers');
});

test('status filters start with pending and end with all', () => {
  assert.deepEqual(MCP_APPROVAL_STATUS_FILTERS.map((item) => item.id), ['pending', 'approved', 'rejected', 'expired', 'all']);
  assert.equal(parseApprovalStatusFilter(null), 'pending');
  assert.equal(parseApprovalStatusFilter('bogus'), 'pending');
  assert.equal(parseApprovalStatusFilter('all'), 'all');
  assert.equal(parseApprovalStatusFilter('expired'), 'expired');
  assert.equal(approvalStatusQueryValue('all'), undefined);
  assert.equal(approvalStatusQueryValue('rejected'), 'rejected');
});

test('the legacy approvals path keeps only the known filters', () => {
  assert.equal(mcpApprovalsTabPath(''), '/mcp-servers?tab=approvals');
  assert.equal(
    mcpApprovalsTabPath('?status=approved&server_id=srv%201&other=x'),
    '/mcp-servers?tab=approvals&status=approved&server_id=srv+1',
  );
  assert.equal(
    mcpApprovalsTabPath(new URLSearchParams({ server_id: 'srv-1' })),
    '/mcp-servers?tab=approvals&server_id=srv-1',
  );
});

test('status labels show single-use approvals and never guess an unknown status', () => {
  assert.deepEqual(approvalStatusView('approved', null), { label: 'Approved', tone: 'approved' });
  assert.deepEqual(approvalStatusView('approved', '2026-10-01T10:05:00Z'), { label: 'Approved · used', tone: 'approved' });
  assert.deepEqual(approvalStatusView('rejected'), { label: 'Rejected', tone: 'rejected' });
  assert.deepEqual(approvalStatusView('cancelled'), { label: 'cancelled', tone: 'unknown' });
  assert.deepEqual(approvalStatusView(''), { label: 'Unknown', tone: 'unknown' });
});

test('csv cells are quoted and cannot start a spreadsheet formula', () => {
  assert.equal(csvCell('plain'), '"plain"');
  assert.equal(csvCell('say "hi"'), '"say ""hi"""');
  assert.equal(csvCell('=HYPERLINK("x")'), '"\'=HYPERLINK(""x"")"');
  assert.equal(csvCell('+1'), '"\'+1"');
  assert.equal(csvCell('@cmd'), '"\'@cmd"');
  assert.equal(csvCell(null), '""');
});

test('the csv export has one header row and one row for each request', () => {
  const csv = buildApprovalsCsv([
    request(),
    request({
      mcp_approval_request_id: 'apr-2',
      status: 'approved',
      server: null,
      requested_by_user: 'user@example.com',
      decided_at: '2026-10-01T10:01:00Z',
      consumed_at: '2026-10-01T10:02:00Z',
      decision_comment: 'ok, "checked"',
    }),
  ]);
  const lines = csv.split('\n');
  assert.equal(lines.length, 3);
  assert.equal(lines[0], 'id,created_at,server,tool_name,scope_type,scope_id,status,requestor,expires_at,decided_at,consumed_at,decision_comment');
  assert.equal(lines[1], '"apr-1","2026-10-01T10:00:00Z","GitHub","github.create_issue","team","team-1","pending","team-1","","","",""');
  assert.equal(
    lines[2],
    '"apr-2","2026-10-01T10:00:00Z","srv-1","github.create_issue","team","team-1","approved","user@example.com","","2026-10-01T10:01:00Z","2026-10-01T10:02:00Z","ok, ""checked"""',
  );
  assert.equal(buildApprovalsCsv([]).split('\n').length, 1);
});

test('export file names name the filter and a safe server id', () => {
  assert.equal(approvalsExportFileName('pending'), 'mcp-approvals-pending.csv');
  assert.equal(approvalsExportFileName('all', 'srv/1 x'), 'mcp-approvals-all-srv_1_x.csv');
});

test('a requester can reject an own call but cannot approve it', () => {
  assert.deepEqual(approvalDecisionActions({ capabilities: { can_decide: true, can_approve: false } }), { canApprove: false, canReject: true, ownRequest: true });
  assert.deepEqual(approvalDecisionActions({ capabilities: { can_decide: true, can_approve: true } }), { canApprove: true, canReject: true, ownRequest: false });
  // An older API does not send can_approve.
  assert.deepEqual(approvalDecisionActions({ capabilities: { can_decide: true } }), { canApprove: true, canReject: true, ownRequest: false });
  assert.deepEqual(approvalDecisionActions({ capabilities: { can_decide: false, can_approve: false } }), { canApprove: false, canReject: false, ownRequest: false });
  assert.deepEqual(approvalDecisionActions({}), { canApprove: false, canReject: false, ownRequest: false });
});

test('an empty page moves back to a page that has rows', () => {
  assert.equal(offsetAfterEmptyPage(0, 0, 0, 20), null);
  assert.equal(offsetAfterEmptyPage(20, 3, 23, 20), null);
  assert.equal(offsetAfterEmptyPage(20, 0, 20, 20), 0);
  assert.equal(offsetAfterEmptyPage(60, 0, 25, 20), 20);
  assert.equal(offsetAfterEmptyPage(40, 0, 0, 20), 0);
});
