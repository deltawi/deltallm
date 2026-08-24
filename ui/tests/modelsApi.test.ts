import assert from 'node:assert/strict';
import test from 'node:test';

import { models } from '../src/lib/api';
import { mutationOutcome } from '../src/lib/mutationOutcome';

test('model mutations preserve post-commit routing warnings', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => new Response(JSON.stringify({
    deployment_id: 'dep-1',
    model_name: 'support',
    provider: 'openai',
    mode: 'chat',
    credential_source: 'inline',
    inline_credentials_present: false,
    connection_summary: {},
    named_credential_id: null,
    named_credential_name: null,
    deltallm_params: { model: 'openai/gpt-4o-mini' },
    model_info: { mode: 'chat' },
    warnings: ['Runtime refresh is pending'],
  }), { headers: { 'content-type': 'application/json' } })) as typeof fetch;

  try {
    const result = await models.create({
      model_name: 'support',
      deltallm_params: { model: 'openai/gpt-4o-mini' },
      model_info: { mode: 'chat' },
    });

    assert.deepEqual(result.warnings, ['Runtime refresh is pending']);
    assert.deepEqual(mutationOutcome('Model deployment was created.', result.warnings), {
      tone: 'info',
      message: 'Model deployment was created. Runtime warning: Runtime refresh is pending',
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('model reads pass AbortSignal to the shared transport', async () => {
  const originalFetch = globalThis.fetch;
  let capturedSignal: AbortSignal | null | undefined;
  globalThis.fetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
    capturedSignal = init?.signal;
    return new Response(JSON.stringify({
      data: [],
      pagination: { total: 0, limit: 20, offset: 0, has_more: false },
    }), { headers: { 'content-type': 'application/json' } });
  }) as typeof fetch;

  try {
    const controller = new AbortController();
    await models.list({ limit: 20, offset: 0 }, controller.signal);
    assert.equal(capturedSignal, controller.signal);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('model mutations pass AbortSignal to the shared transport', async () => {
  const originalFetch = globalThis.fetch;
  let capturedSignal: AbortSignal | null | undefined;
  globalThis.fetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
    capturedSignal = init?.signal;
    return new Response(JSON.stringify({ warnings: [] }), {
      headers: { 'content-type': 'application/json' },
    });
  }) as typeof fetch;

  try {
    const controller = new AbortController();
    await models.create({
      model_name: 'support',
      deltallm_params: { model: 'openai/gpt-4o-mini' },
      model_info: { mode: 'chat' },
    }, controller.signal);
    assert.equal(capturedSignal, controller.signal);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
