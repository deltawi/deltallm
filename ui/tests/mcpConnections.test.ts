import assert from 'node:assert/strict';
import test from 'node:test';
import { connectionAttentionCount } from '../src/lib/mcpConnectionAttention';
import {
  connectionAction,
  connectionExpiryText,
  connectionStatusView,
  connectResultView,
  parseScopes,
  toggleScope,
} from '../src/lib/mcpConnections';
import { buildMCPServerPayload, EMPTY_MCP_SERVER_FORM, formFromMCPServer } from '../src/lib/mcpServerForm';

test('connection status, action and badge count', () => {
  assert.deepEqual(connectionStatusView('connected'), { label: 'Connected', tone: 'success' });
  assert.deepEqual(connectionStatusView('needs_reconnect'), { label: 'Connect again', tone: 'warning' });
  assert.deepEqual(connectionStatusView('not_connected'), { label: 'Not connected', tone: 'neutral' });
  assert.equal(connectionAction({ status: 'connected' }), 'disconnect');
  assert.equal(connectionAction({ status: 'needs_reconnect' }), 'reconnect');
  assert.equal(connectionAction({ status: 'not_connected' }), 'connect');
  // A server that the user never connected is not an error.
  assert.equal(connectionAttentionCount([{ status: 'not_connected' }, { status: 'needs_reconnect' }, { status: 'connected' }]), 1);
});

test('expiry text', () => {
  const now = new Date('2026-10-18T12:00:00Z');
  assert.equal(connectionExpiryText({ status: 'connected', expires_at: '2026-10-18T12:30:00Z', renews_automatically: true }, now), 'In 30 min (renews automatically)');
  assert.equal(connectionExpiryText({ status: 'connected', expires_at: '2026-10-18T15:00:00Z', renews_automatically: false }, now), 'In 3 h');
  assert.equal(connectionExpiryText({ status: 'connected', expires_at: '2026-10-25T12:00:00Z', renews_automatically: false }, now), 'In 7 days');
  assert.equal(connectionExpiryText({ status: 'connected', expires_at: null, renews_automatically: false }, now), 'Does not expire');
  assert.equal(connectionExpiryText({ status: 'connected', expires_at: '2026-10-18T11:00:00Z', renews_automatically: false }, now), 'Expired');
  assert.equal(connectionExpiryText({ status: 'not_connected', expires_at: null, renews_automatically: false }, now), '—');
});

test('callback result messages', () => {
  assert.deepEqual(connectResultView('?result=connected', 'GitHub'), {
    tone: 'success',
    title: 'Account connected',
    message: 'Your GitHub account is connected.',
  });
  assert.match(connectResultView('?result=error&reason=wrong_account')!.message, /different account/);
  assert.match(connectResultView('?result=error&reason=denied')!.message, /denied/);
  assert.match(connectResultView('?result=error&reason=<script>')!.message, /did not complete/);
  assert.equal(connectResultView(''), null);
});

test('scope parsing and toggling', () => {
  assert.deepEqual(parseScopes(' repo,read:user  repo '), ['repo', 'read:user']);
  assert.equal(toggleScope('repo', 'read:user'), 'repo read:user');
  assert.equal(toggleScope('repo read:user', 'repo'), 'read:user');
});

test('oauth2 payload sends the client secret only when typed', () => {
  const form = {
    ...EMPTY_MCP_SERVER_FORM,
    server_key: 'github',
    name: 'GitHub',
    base_url: 'https://mcp.example.com/mcp',
    auth_mode: 'oauth2' as const,
    oauth_client_id: ' own-client ',
    oauth_scopes: 'repo read:user',
  };
  assert.deepEqual(buildMCPServerPayload(form).auth_config, { client_id: 'own-client', scopes: ['repo', 'read:user'] });
  assert.deepEqual(buildMCPServerPayload({ ...form, oauth_client_secret: 's3cret' }).auth_config, {
    client_id: 'own-client',
    scopes: ['repo', 'read:user'],
    client_secret: 's3cret',
  });
  assert.deepEqual(
    buildMCPServerPayload({ ...form, oauth_clear_client_secret: true }, { preserveExistingCredentials: true }).auth_config,
    { client_id: 'own-client', scopes: ['repo', 'read:user'], clear_client_secret: true },
  );
});

test('a client that the gateway registered is not loaded as typed input', () => {
  const server = {
    server_key: 'github',
    name: 'GitHub',
    owner_scope_type: 'global' as const,
    base_url: 'https://mcp.example.com/mcp',
    transport: 'streamable_http' as const,
    enabled: true,
    auth_mode: 'oauth2' as const,
    request_timeout_ms: 30000,
  };
  const oauth = { client_secret_set: false, scopes: ['repo'], discovery: null, discovery_error: null };
  const dynamic = formFromMCPServer({ ...server, oauth: { ...oauth, client_id: 'dyn-1', client_id_source: 'dynamic' } });
  assert.equal(dynamic.oauth_client_id, '');
  assert.equal(dynamic.oauth_scopes, 'repo');
  const manual = formFromMCPServer({ ...server, oauth: { ...oauth, client_id: 'own', client_id_source: 'manual' } });
  assert.equal(manual.oauth_client_id, 'own');
});
