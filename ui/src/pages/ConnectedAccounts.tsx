import { useState } from 'react';
import { useLocation, useParams } from 'react-router-dom';
import { AlertTriangle, CheckCircle2, Info, Link2, Plug, XCircle } from 'lucide-react';
import Button from '../components/Button';
import ConfirmDialog from '../components/ConfirmDialog';
import DataTable, { type Column } from '../components/DataTable';
import StatusBadge from '../components/StatusBadge';
import { useToast } from '../components/ToastProvider';
import { ContentCard, IndexShell } from '../components/admin/shells';
import { mcpOAuth, type MCPConnectionRow } from '../lib/api/mcpOAuth';
import { errorMessage } from '../lib/errorMessage';
import { useApi } from '../lib/hooks';
import {
  browserNavigation,
  connectionAction,
  connectionExpiryText,
  connectionStatusView,
  connectResultView,
} from '../lib/mcpConnections';
import { notifyMCPConnectionsChanged } from '../lib/mcpConnectionAttention';

const STATUS_BADGE: Record<string, string> = { success: 'active', warning: 'warning', neutral: 'disabled' };

function SkeletonRows() {
  return (
    <div role="status" aria-label="Loading connected accounts" className="divide-y divide-gray-100">
      {[0, 1, 2].map((row) => (
        <div key={row} className="flex items-center gap-4 p-4">
          <span aria-hidden="true" className="block h-4 w-40 animate-pulse rounded bg-gray-100" />
          <span aria-hidden="true" className="block h-4 w-24 animate-pulse rounded bg-gray-100" />
          <span aria-hidden="true" className="block h-4 flex-1 animate-pulse rounded bg-gray-100" />
          <span aria-hidden="true" className="block h-7 w-24 animate-pulse rounded bg-gray-100" />
        </div>
      ))}
    </div>
  );
}

function ResultNotice({ tone, title, message }: { tone: 'success' | 'error'; title: string; message: string }) {
  const Icon = tone === 'success' ? CheckCircle2 : XCircle;
  return (
    <div
      role={tone === 'success' ? 'status' : 'alert'}
      className={`flex items-start gap-3 rounded-xl border p-4 text-sm ${tone === 'success' ? 'border-emerald-200 bg-emerald-50 text-emerald-900' : 'border-red-200 bg-red-50 text-red-900'}`}
    >
      <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <div>
        <p className="font-semibold">{title}</p>
        <p className="mt-0.5">{message}</p>
      </div>
    </div>
  );
}

export default function ConnectedAccounts() {
  const { serverKey: focusKey } = useParams<{ serverKey?: string }>();
  const location = useLocation();
  const { pushToast } = useToast();
  const { data, error, loading, refetch } = useApi((signal) => mcpOAuth.listConnections(signal), []);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [disconnecting, setDisconnecting] = useState<MCPConnectionRow | null>(null);

  const rows = data?.data ?? [];
  const focusRow = focusKey ? rows.find((row) => row.server_key === focusKey) ?? null : null;
  const result = connectResultView(location.search, focusRow?.server_name);

  const startConnect = async (row: MCPConnectionRow) => {
    if (busyKey) return;
    setBusyKey(row.server_key);
    try {
      const { authorization_url: authorizationUrl } = await mcpOAuth.connect(row.server_key);
      browserNavigation.assign(authorizationUrl);
    } catch (connectError) {
      setBusyKey(null);
      pushToast({ tone: 'error', title: 'Cannot connect', message: errorMessage(connectError, 'The connection did not start.') });
    }
  };

  const confirmDisconnect = async () => {
    const row = disconnecting;
    if (!row || busyKey) return;
    setBusyKey(row.server_key);
    try {
      const outcome = await mcpOAuth.disconnect(row.server_key);
      pushToast({
        tone: 'success',
        title: 'Account disconnected',
        message: outcome.revoked
          ? `The ${row.server_name} tokens were revoked and deleted.`
          : `The ${row.server_name} tokens were deleted. The server did not confirm the revocation.`,
      });
      setDisconnecting(null);
      notifyMCPConnectionsChanged();
      refetch();
    } catch (disconnectError) {
      pushToast({ tone: 'error', title: 'Disconnect failed', message: errorMessage(disconnectError, 'The connection was not removed.') });
    } finally {
      setBusyKey(null);
    }
  };

  const columns: Column<MCPConnectionRow>[] = [
    {
      key: 'server',
      header: 'Server',
      render: (row) => (
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-gray-900">{row.server_name}</span>
            {row.server_key === focusKey ? <StatusBadge status="enabled" label={result?.tone === 'success' ? 'Just connected' : 'Selected'} /> : null}
          </div>
          <span className="font-mono text-xs text-gray-500">{row.server_key}</span>
        </div>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (row) => {
        const view = connectionStatusView(row.status);
        return (
          <div>
            <StatusBadge status={STATUS_BADGE[view.tone]} label={view.label} />
            {row.status === 'needs_reconnect' && row.status_reason ? (
              <p className="mt-1 text-xs text-amber-800">{row.status_reason}</p>
            ) : null}
          </div>
        );
      },
    },
    {
      key: 'account',
      header: 'Account',
      render: (row) => (row.account_label ? <span className="text-gray-900">{row.account_label}</span> : <span className="text-gray-400">—</span>),
    },
    {
      key: 'access',
      header: 'Access',
      render: (row) => (row.scopes.length ? (
        <div className="flex max-w-xs flex-wrap gap-1">
          {row.scopes.map((scope) => (
            <span key={scope} className="rounded bg-gray-100 px-1.5 font-mono text-xs text-gray-700">{scope}</span>
          ))}
        </div>
      ) : <span className="text-gray-400">Default</span>),
    },
    {
      key: 'expires',
      header: 'Expires',
      render: (row) => <span className="whitespace-nowrap">{connectionExpiryText(row)}</span>,
    },
  ];

  const renderAction = (row: MCPConnectionRow) => {
    const action = connectionAction(row);
    const busy = busyKey === row.server_key;
    if (action === 'disconnect') {
      return (
        <Button variant="danger-outline" size="sm" onClick={() => setDisconnecting(row)} disabled={Boolean(busyKey)}>
          Disconnect
        </Button>
      );
    }
    return (
      <Button
        variant={action === 'reconnect' ? 'warning-outline' : 'primary'}
        size="sm"
        onClick={() => { void startConnect(row); }}
        loading={busy}
        disabled={!row.connect_available || Boolean(busyKey)}
        title={row.connect_available ? undefined : 'An administrator must finish the OAuth setup of this server first.'}
      >
        <Link2 className="h-3.5 w-3.5" aria-hidden="true" />
        {action === 'reconnect' ? 'Connect again' : 'Connect'}
      </Button>
    );
  };

  const focusNeedsAction = focusRow && focusRow.status !== 'connected' && !result;

  return (
    <IndexShell
      eyebrowPrefix="Account"
      title="Connected accounts"
      titleIcon={Plug}
      count={data ? rows.length : null}
      description="Connect your own accounts for MCP servers that use OAuth 2.0 (per user)."
      notice={result ? <ResultNotice {...result} /> : null}
    >
      <div className="space-y-4">
        <div className="flex items-start gap-3 rounded-xl border border-blue-100 bg-blue-50 p-4 text-sm text-blue-900">
          <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <p>
            Your connection is used only by API keys that you own. Keys that are not owned by a user, such as service
            account keys, cannot use it. Disconnect to stop all tool calls that use your account.
          </p>
        </div>

        {focusNeedsAction ? (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
            <p className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              {focusRow.status === 'needs_reconnect'
                ? `Connect your ${focusRow.server_name} account again to use its tools.`
                : `Connect your ${focusRow.server_name} account to use its tools.`}
            </p>
            {renderAction(focusRow)}
          </div>
        ) : null}
        {focusKey && data && !focusRow ? (
          <p role="alert" className="text-sm text-red-700">
            The MCP server "{focusKey}" does not use per-user OAuth, or you do not have access to it.
          </p>
        ) : null}

        <ContentCard>
          {loading && !data ? (
            <SkeletonRows />
          ) : error ? (
            <div role="alert" className="flex flex-wrap items-center justify-between gap-3 p-4 text-sm text-red-700">
              <span>{errorMessage(error, 'Connected accounts are not available now.')}</span>
              <Button variant="outline" size="sm" onClick={refetch}>Try again</Button>
            </div>
          ) : data && !data.account_connectable ? (
            <p className="p-4 text-sm text-gray-600">
              Sign in with a user account to connect MCP servers. The master key has no account.
            </p>
          ) : (
            <DataTable
              columns={columns}
              data={rows}
              rowKey={(row) => row.server_id}
              rowActions={renderAction}
              caption="Connected accounts"
              responsive
              emptyMessage="No MCP server that you can use asks for a personal connection."
            />
          )}
        </ContentCard>
      </div>

      <ConfirmDialog
        open={disconnecting !== null}
        title={`Disconnect ${disconnecting?.server_name ?? ''}`}
        description="The gateway revokes your tokens at the server and deletes them. Tool calls from your keys stop until you connect again."
        confirmLabel="Disconnect"
        destructive
        confirming={busyKey !== null && busyKey === disconnecting?.server_key}
        onConfirm={() => { void confirmDisconnect(); }}
        onClose={() => setDisconnecting(null)}
      />
    </IndexShell>
  );
}
