import assert from 'node:assert/strict';
import test from 'node:test';
import { EMPTY_FORM, type ModelFormValues } from '../src/components/modelFormShared';
import {
  buildConnectionTestPayload,
  connectionTestBlocker,
  connectionTestKey,
} from '../src/components/models/connectionTest';
import { EMPTY_REQUEST_SHAPING } from '../src/components/models/requestShaping';

const ADMIN = { allowInlineCredentials: true, supportsCustomAuth: true };

function form(overrides: Partial<ModelFormValues>): ModelFormValues {
  return { ...EMPTY_FORM, provider: 'vllm', model: ' llama-3 ', ...overrides };
}

test('inline settings are sent trimmed, with custom auth only when the provider supports it', () => {
  const values = form({
    credential_source: 'inline',
    api_key: ' key ',
    api_base: 'https://vllm.example/v1',
    auth_header_name: 'X-API-Key',
    auth_header_format: '{api_key}',
  });
  const payload = buildConnectionTestPayload(values, EMPTY_REQUEST_SHAPING, ADMIN);
  assert.equal(payload.model, 'llama-3');
  assert.equal(payload.api_key, 'key');
  assert.equal(payload.auth_header_name, 'X-API-Key');
  assert.equal(payload.named_credential_id, null);
  assert.equal(payload.deployment_id, null);

  const plain = buildConnectionTestPayload(values, EMPTY_REQUEST_SHAPING, { ...ADMIN, supportsCustomAuth: false });
  assert.equal(plain.auth_header_name, null);
  assert.equal(plain.auth_header_format, null);
});

test('a named credential sends no inline secrets', () => {
  const values = form({ credential_source: 'named', named_credential_id: 'cred-1', api_key: 'stale' });
  const payload = buildConnectionTestPayload(values, { ...EMPTY_REQUEST_SHAPING, outboundProxy: ' corp ' }, ADMIN);
  assert.equal(payload.named_credential_id, 'cred-1');
  assert.equal(payload.api_key, null);
  assert.equal(payload.api_base, null);
  assert.equal(payload.outbound_proxy, 'corp');
});

test('editing reuses saved secrets unless the stored key is being cleared', () => {
  const edit = { ...ADMIN, deploymentId: 'dep-1' };
  const kept = form({ credential_source: 'inline', inline_credentials_present: true });
  assert.equal(buildConnectionTestPayload(kept, EMPTY_REQUEST_SHAPING, edit).deployment_id, 'dep-1');

  const cleared = { ...kept, clear_inline_api_key: true };
  assert.equal(buildConnectionTestPayload(cleared, EMPTY_REQUEST_SHAPING, edit).deployment_id, null);

  const ownerManaged = form({ credential_source: 'named', keep_existing_named_credential: true, named_credential_id: 'hidden' });
  const payload = buildConnectionTestPayload(ownerManaged, EMPTY_REQUEST_SHAPING, edit);
  assert.equal(payload.named_credential_id, null);
  assert.equal(payload.deployment_id, 'dep-1');
});

test('the test needs a provider and, for named credentials, a chosen credential', () => {
  assert.equal(connectionTestBlocker(form({ provider: '' }), ADMIN), 'Choose a provider first.');
  assert.equal(connectionTestBlocker(form({ credential_source: 'named' }), ADMIN), 'Choose a named credential first.');
  assert.equal(
    connectionTestBlocker(form({ credential_source: 'inline' }), { ...ADMIN, allowInlineCredentials: false }),
    'Choose a named credential first.',
  );
  assert.equal(connectionTestBlocker(form({ credential_source: 'inline' }), ADMIN), null);
  assert.equal(connectionTestBlocker(form({ credential_source: 'named', keep_existing_named_credential: true }), ADMIN), null);
});

test('any change to the tested settings gives a new result key', () => {
  const base = buildConnectionTestPayload(form({ credential_source: 'inline', api_key: 'a' }), EMPTY_REQUEST_SHAPING, ADMIN);
  const changed = buildConnectionTestPayload(form({ credential_source: 'inline', api_key: 'b' }), EMPTY_REQUEST_SHAPING, ADMIN);
  assert.notEqual(connectionTestKey(base), connectionTestKey(changed));
  assert.equal(connectionTestKey(base), connectionTestKey({ ...base }));
});
