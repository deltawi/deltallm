import assert from 'node:assert/strict';
import test from 'node:test';

import { namedCredentials } from '../src/lib/api';

test('named-credential updates preserve post-commit routing warnings', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => new Response(JSON.stringify({
    credential_id: 'credential-1',
    name: 'OpenAI Production',
    provider: 'openai',
    connection_config: { api_key: '***REDACTED***' },
    credentials_present: true,
    usage_count: 2,
    warnings: ['Runtime refresh is pending'],
  }), { headers: { 'content-type': 'application/json' } })) as typeof fetch;

  try {
    const result = await namedCredentials.update('credential-1', {
      name: 'OpenAI Production',
      connection_config: { api_key: 'rotated-secret' },
    });

    assert.deepEqual(result.warnings, ['Runtime refresh is pending']);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('inline conversion responses preserve post-commit routing warnings', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => new Response(JSON.stringify({
    credential: {
      credential_id: 'credential-1',
      name: 'OpenAI Shared',
      provider: 'openai',
      connection_config: { api_key: '***REDACTED***' },
      credentials_present: true,
      usage_count: 1,
    },
    converted_deployments: [{ deployment_id: 'dep-1', model_name: 'gpt-4o-mini' }],
    warnings: ['Runtime refresh is pending'],
  }), { headers: { 'content-type': 'application/json' } })) as typeof fetch;

  try {
    const result = await namedCredentials.convertInlineGroup({
      fingerprint: 'fingerprint',
      name: 'OpenAI Shared',
      provider: 'openai',
      deployment_ids: ['dep-1'],
    });

    assert.deepEqual(result.warnings, ['Runtime refresh is pending']);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
