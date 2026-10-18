import type { MCPConnectionRow, MCPConnectionStatus } from './api/mcpOAuth';

export interface ConnectionStatusView {
  label: string;
  tone: 'success' | 'warning' | 'neutral';
}

export function connectionStatusView(status: MCPConnectionStatus): ConnectionStatusView {
  if (status === 'connected') return { label: 'Connected', tone: 'success' };
  if (status === 'needs_reconnect') return { label: 'Connect again', tone: 'warning' };
  return { label: 'Not connected', tone: 'neutral' };
}

export type ConnectionAction = 'connect' | 'reconnect' | 'disconnect';

// The main action for a row. A connected row can also connect again from its menu.
export function connectionAction(row: Pick<MCPConnectionRow, 'status'>): ConnectionAction {
  if (row.status === 'connected') return 'disconnect';
  if (row.status === 'needs_reconnect') return 'reconnect';
  return 'connect';
}

export function connectionExpiryText(
  row: Pick<MCPConnectionRow, 'status' | 'expires_at' | 'renews_automatically'>,
  now: Date = new Date(),
): string {
  if (row.status !== 'connected') return '—';
  if (!row.expires_at) return 'Does not expire';
  const expires = new Date(row.expires_at);
  if (Number.isNaN(expires.getTime())) return '—';
  const minutes = Math.round((expires.getTime() - now.getTime()) / 60000);
  const relative = minutes <= 0
    ? 'Expired'
    : minutes < 60
      ? `In ${minutes} min`
      : minutes < 48 * 60
        ? `In ${Math.round(minutes / 60)} h`
        : `In ${Math.round(minutes / (24 * 60))} days`;
  return row.renews_automatically ? `${relative} (renews automatically)` : relative;
}

export interface ConnectResultView {
  tone: 'success' | 'error';
  title: string;
  message: string;
}

const CALLBACK_ERROR_MESSAGES: Record<string, string> = {
  denied: 'The authorization was cancelled or denied at the server.',
  expired_state: 'The sign-in link expired. Start the connection again.',
  invalid_state: 'The sign-in link is not valid or was already used. Start the connection again.',
  wrong_account: 'The sign-in was started by a different account. Start the connection again from this account.',
  sign_in_required: 'Sign in to DeltaLLM first, then start the connection again.',
  server_unavailable: 'The MCP server is not available for OAuth connections now.',
  client_changed: 'The server OAuth settings changed during the sign-in. Start the connection again.',
  token_exchange_failed: 'The server did not issue a token. Start the connection again or contact your administrator.',
  configuration: 'OAuth connections are not configured on this gateway. Contact your administrator.',
};

// Turns the ?result=&reason= query from the OAuth callback redirect into a message.
export function connectResultView(search: string, serverName?: string | null): ConnectResultView | null {
  const params = new URLSearchParams(search);
  const result = params.get('result');
  const name = serverName || 'the server';
  if (result === 'connected') {
    return { tone: 'success', title: 'Account connected', message: `Your ${name} account is connected.` };
  }
  if (result !== 'error') return null;
  const reason = params.get('reason') || '';
  return {
    tone: 'error',
    title: 'Connection failed',
    message: CALLBACK_ERROR_MESSAGES[reason] || 'The connection did not complete. Start it again.',
  };
}

export function parseScopes(value: string): string[] {
  const seen = new Set<string>();
  const scopes: string[] = [];
  for (const item of value.split(/[\s,]+/)) {
    const scope = item.trim();
    if (!scope || seen.has(scope)) continue;
    seen.add(scope);
    scopes.push(scope);
  }
  return scopes;
}

export function toggleScope(value: string, scope: string): string {
  const scopes = parseScopes(value);
  return (scopes.includes(scope) ? scopes.filter((item) => item !== scope) : [...scopes, scope]).join(' ');
}

// The page leaves the app for the authorization server. Tests replace assign.
export const browserNavigation = {
  assign(url: string): void {
    window.location.assign(url);
  },
};
