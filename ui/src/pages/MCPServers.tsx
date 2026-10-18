import { useSearchParams } from 'react-router-dom';
import { Boxes, CheckCircle2, Plug, Server } from 'lucide-react';
import MCPApprovalsPanel from '../components/mcp/MCPApprovalsPanel';
import MCPServersTab from '../components/mcp/MCPServersTab';
import MCPBundlesTab from '../components/mcp/bundles/MCPBundlesTab';
import { IconTabs, IndexShell } from '../components/admin/shells';
import { mcpServers } from '../lib/api';
import { useAuth } from '../lib/auth';
import { resolveUiAccess } from '../lib/authorization';
import { useApi } from '../lib/hooks';
import {
  DEFAULT_MCP_APPROVAL_STATUS,
  parseApprovalStatusFilter,
  resolveMCPPageTab,
  type MCPApprovalStatusFilter,
  type MCPPageTab,
} from '../lib/mcpApprovals';
import { navSectionLabel } from '../lib/navigation';

const TABS_ID = 'mcp';

export default function MCPServers() {
  const { session, authMode } = useAuth();
  const uiAccess = resolveUiAccess(authMode, session);
  const canReviewApprovals = uiAccess.mcp_approvals;
  const [searchParams, setSearchParams] = useSearchParams();

  const activeTab = resolveMCPPageTab(searchParams.get('tab'), canReviewApprovals);
  const approvalStatus = parseApprovalStatusFilter(searchParams.get('status'));
  const serverFilter = searchParams.get('server_id');

  const { data: pendingData, error: pendingError, refetch: refetchPending } = useApi(
    (signal) => (canReviewApprovals
      ? mcpServers.listApprovalRequests({ status: 'pending', limit: 1 }, signal)
      : Promise.resolve(null)),
    [canReviewApprovals],
  );
  // When the count read fails, show no number. A failed read is not zero.
  const pendingCount = pendingData && !pendingError ? pendingData.pagination.total : null;

  // Replace the history entry, so Back leaves the page and does not step through tabs and filters.
  const updateParams = (change: (next: URLSearchParams) => void) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      change(next);
      return next;
    }, { replace: true });
  };

  const selectTab = (tab: MCPPageTab) => {
    updateParams((next) => {
      if (tab === 'approvals') {
        next.set('tab', 'approvals');
      } else if (tab === 'bundles') {
        next.set('tab', 'bundles');
        next.delete('status');
        next.delete('server_id');
      } else {
        next.delete('tab');
        next.delete('status');
        next.delete('server_id');
      }
    });
  };

  const selectStatus = (status: MCPApprovalStatusFilter) => {
    updateParams((next) => {
      if (status === DEFAULT_MCP_APPROVAL_STATUS) next.delete('status');
      else next.set('status', status);
    });
  };

  const tabs: Array<{ id: MCPPageTab; label: string; icon: typeof Server; count?: number }> = [
    { id: 'servers', label: 'Servers', icon: Server },
    { id: 'bundles', label: 'Bundles', icon: Boxes },
  ];
  if (canReviewApprovals) {
    tabs.push({ id: 'approvals', label: 'Approvals', icon: CheckCircle2, count: pendingCount ?? undefined });
  }

  return (
    <IndexShell
      eyebrowPrefix={navSectionLabel('/mcp-servers')}
      title="MCP"
      titleIcon={Plug}
      description="Connect external tool servers, group their tools into bundles, and review the tool calls that need approval."
      tabs={tabs.length > 1 ? (
        <IconTabs id={TABS_ID} label="MCP sections" variant="header" items={tabs} active={activeTab} onChange={selectTab} />
      ) : null}
    >
      <div role={tabs.length > 1 ? 'tabpanel' : undefined} id={`${TABS_ID}-panel-${activeTab}`} aria-labelledby={tabs.length > 1 ? `${TABS_ID}-tab-${activeTab}` : undefined}>
        {activeTab === 'bundles' ? <MCPBundlesTab /> : null}
        {activeTab === 'approvals' ? (
          <MCPApprovalsPanel
            serverId={serverFilter}
            status={approvalStatus}
            onStatusChange={selectStatus}
            onClearServer={() => updateParams((next) => next.delete('server_id'))}
            pendingCount={pendingCount}
            onRefresh={refetchPending}
          />
        ) : null}
        {activeTab === 'servers' ? <MCPServersTab canRegister={uiAccess.mcp_servers} /> : null}
      </div>
    </IndexShell>
  );
}
