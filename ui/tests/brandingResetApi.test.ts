import assert from 'node:assert/strict';
import test from 'node:test';

import { ApiError } from '../src/lib/api/transport';
import { resetBranding } from '../src/lib/brandingResetApi';

const responsePayload = {
  instance_name: 'DeltaLLM',
  logo_mark_url: null,
  logo_full_url: null,
  favicon_url: null,
  primary_color: '#5B50D6',
  secondary_color: '#8B7CFF',
  menu_hover_color: '#F7F5FF',
  reconciliation_pending: false,
};

test('branding reset uses the dedicated server-owned reset operation', async () => {
  const originalFetch = globalThis.fetch;
  let capturedInput: RequestInfo | URL | undefined;
  let capturedInit: RequestInit | undefined;
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    capturedInput = input;
    capturedInit = init;
    return new Response(JSON.stringify(responsePayload), {
      headers: { 'content-type': 'application/json' },
    });
  };

  try {
    const response = await resetBranding();

    assert.deepEqual(response, responsePayload);
    assert.equal(capturedInput, '/ui/api/branding/reset');
    assert.equal(capturedInit?.method, 'POST');
    assert.equal(capturedInit?.body, undefined);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('branding reset preserves structured transport errors', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(
    JSON.stringify({ detail: { code: 'audit_persistence_unavailable', message: 'Audit unavailable' } }),
    { status: 503, headers: { 'content-type': 'application/json' } },
  );

  try {
    await assert.rejects(
      resetBranding(),
      (error: unknown) => error instanceof ApiError
        && error.status === 503
        && error.message === 'Audit unavailable',
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});
